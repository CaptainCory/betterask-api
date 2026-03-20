"""
BetterAsk API — Question Intelligence powered by END SMALL TALK methodology.
Stop asking "How can I help you?" — BetterAsk.
"""

import hashlib
import json
import logging
import os
import random
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import stripe
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))
CORPUS_PATH = os.getenv(
    "CORPUS_PATH",
    str(Path(__file__).parent / "questions-corpus.txt"),
)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
BASE_URL = os.getenv("BETTERASK_BASE_URL", "http://localhost:8000")
DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "betterask.db"))

stripe.api_key = STRIPE_SECRET_KEY

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("betterask")

# ---------------------------------------------------------------------------
# Tier config
# ---------------------------------------------------------------------------

TIERS = {
    "free": {"name": "Free", "price": 0, "calls_per_day": 100, "stripe_product_id": None},
    "pro": {"name": "Pro", "price": 29, "calls_per_day": 5_000, "stripe_product_id": os.getenv("STRIPE_PRO_PRODUCT_ID", "")},
    "scale": {"name": "Scale", "price_per_call": 0.005, "calls_per_day": None, "stripe_product_id": os.getenv("STRIPE_SCALE_PRODUCT_ID", "")},
    # Legacy tier aliases for backward compat
    "builder": {"name": "Pro", "price": 29, "calls_per_day": 5_000, "stripe_product_id": os.getenv("STRIPE_PRO_PRODUCT_ID", "")},
    "metered": {"name": "Scale", "price_per_call": 0.005, "calls_per_day": None, "stripe_product_id": os.getenv("STRIPE_SCALE_PRODUCT_ID", "")},
}

# Per-call rate for metered billing (cents)
METERED_RATE = 0.005

# Reverse lookup: stripe product -> tier
PRODUCT_TO_TIER = {v["stripe_product_id"]: k for k, v in TIERS.items() if v["stripe_product_id"]}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                tier TEXT NOT NULL DEFAULT 'free',
                calls_today INTEGER NOT NULL DEFAULT 0,
                calls_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_customer ON api_keys(stripe_customer_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL UNIQUE,
                archetype TEXT,
                vectors TEXT,
                source TEXT DEFAULT 'corpus',
                tags TEXT,
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                score_composite REAL,
                score_data TEXT,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        
        # Add vectors column to existing tables (safe migration)
        try:
            conn.execute("ALTER TABLE questions ADD COLUMN vectors TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_archetype ON questions(archetype)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_source ON questions(source)")
        conn.commit()
    logger.info("Database initialized at %s", DB_PATH)


def generate_api_key() -> str:
    """Generate a prefixed API key: ba_live_<32 hex chars>"""
    return f"ba_live_{secrets.token_hex(16)}"


def create_api_key(tier: str = "free", stripe_customer_id: str | None = None,
                   stripe_subscription_id: str | None = None) -> str:
    key = generate_api_key()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO api_keys (key, stripe_customer_id, stripe_subscription_id, tier, calls_today, calls_date) VALUES (?, ?, ?, ?, 0, ?)",
            (key, stripe_customer_id, stripe_subscription_id, tier, date.today().isoformat()),
        )
        conn.commit()
    logger.info("Created API key for tier=%s customer=%s", tier, stripe_customer_id)
    return key


def get_api_key_record(key: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE key = ? AND active = 1", (key,)).fetchone()
        return dict(row) if row else None


def increment_usage(key: str) -> bool:
    """Increment call count. Returns True if within limit, False if rate-limited."""
    today = date.today().isoformat()
    with get_db() as conn:
        row = conn.execute("SELECT tier, calls_today, calls_date FROM api_keys WHERE key = ? AND active = 1", (key,)).fetchone()
        if not row:
            return False
        tier = row["tier"]
        limit = TIERS.get(tier, {}).get("calls_per_day")

        # Reset counter if new day
        if row["calls_date"] != today:
            conn.execute("UPDATE api_keys SET calls_today = 1, calls_date = ? WHERE key = ?", (today, key))
            conn.commit()
            return True

        # Unlimited tier
        if limit is None:
            conn.execute("UPDATE api_keys SET calls_today = calls_today + 1 WHERE key = ?", (key,))
            conn.commit()
            return True

        if row["calls_today"] >= limit:
            return False

        conn.execute("UPDATE api_keys SET calls_today = calls_today + 1 WHERE key = ?", (key,))
        conn.commit()
        return True


def deactivate_keys_for_subscription(subscription_id: str):
    with get_db() as conn:
        conn.execute("UPDATE api_keys SET active = 0 WHERE stripe_subscription_id = ?", (subscription_id,))
        conn.commit()
    logger.info("Deactivated keys for subscription %s", subscription_id)


def upgrade_keys_for_subscription(subscription_id: str, new_tier: str):
    with get_db() as conn:
        conn.execute("UPDATE api_keys SET tier = ? WHERE stripe_subscription_id = ? AND active = 1",
                      (new_tier, subscription_id))
        conn.commit()
    logger.info("Upgraded subscription %s to tier %s", subscription_id, new_tier)

# ---------------------------------------------------------------------------
# Data (vectors, contexts, etc.)
# ---------------------------------------------------------------------------

VECTORS = [
    {
        "id": "specificity",
        "name": "Specificity",
        "emoji": "🔢",
        "one_liner": "How many?",
        "prompt_template": "Ask for a precise, concrete number or detail related to '{about}'. The precision forces honesty and reveals personality. No vague answers allowed."
    },
    {
        "id": "name_an_example",
        "name": "Name an Example",
        "emoji": "📌",
        "one_liner": "Like what?",
        "prompt_template": "Ask the person to name a specific, concrete example from their life related to '{about}'. The example proves lived experience — no hiding behind abstractions."
    },
    {
        "id": "absurdity",
        "name": "Absurdity",
        "emoji": "🤪",
        "one_liner": "Wait, seriously?",
        "prompt_template": "Create an absurd or ridiculous scenario related to '{about}' that disarms defenses through humor. The silliness is a trojan horse for genuine self-revelation."
    },
    {
        "id": "self_assessment",
        "name": "Self-Assessment",
        "emoji": "🪞",
        "one_liner": "How honest are you being?",
        "prompt_template": "Force the person to evaluate themselves honestly in relation to '{about}'. The question should make self-deception uncomfortable."
    },
    {
        "id": "hypothetical",
        "name": "Hypothetical",
        "emoji": "💭",
        "one_liner": "What if?",
        "prompt_template": "Create an imaginative scenario about '{about}' that feels safe to answer but is secretly revealing. The hypothetical removes real-world stakes so truth can slip through."
    },
    {
        "id": "perspective_shift",
        "name": "Perspective Shift",
        "emoji": "👁️",
        "one_liner": "Seen from where?",
        "prompt_template": "Force the person to see '{about}' from a completely different vantage point — another person's eyes, another time period, another culture, or their own life viewed from the outside."
    },
    {
        "id": "time",
        "name": "Time",
        "emoji": "⏰",
        "one_liner": "When? How long? What changed?",
        "prompt_template": "Use time as a lens on '{about}' — past vs present, time loops, aging, urgency, or the gap between who you were and who you are. Time reveals what we'd rather not see."
    },
    {
        "id": "comparison",
        "name": "Comparison",
        "emoji": "⚖️",
        "one_liner": "Which one wins?",
        "prompt_template": "Force a ranking or comparison related to '{about}'. The act of choosing one thing over another reveals hidden priorities the person might not consciously know they have."
    },
    {
        "id": "emotion",
        "name": "Emotion",
        "emoji": "💗",
        "one_liner": "What did that feel like?",
        "prompt_template": "Open emotional space around '{about}'. The question should gently invite feeling — not demand vulnerability, but make it safe to go there."
    },
    {
        "id": "subversion",
        "name": "Subversion",
        "emoji": "🔄",
        "one_liner": "But what if it's actually…",
        "prompt_template": "Flip expectations about '{about}'. Take something the person assumes is true and reframe it so they see it completely differently. The twist IS the question."
    },
    {
        "id": "sensory_imagination",
        "name": "Sensory/Imagination",
        "emoji": "🎨",
        "one_liner": "What does it look/smell/sound like?",
        "prompt_template": "Ground '{about}' in physical, embodied experience — sights, sounds, smells, textures. Pull the person out of their head and into their body."
    },
    {
        "id": "identity",
        "name": "Identity",
        "emoji": "🏷️",
        "one_liner": "Who are you in this?",
        "prompt_template": "Touch on who the person IS (not what they do) in relation to '{about}'. Force self-categorization using unexpected categories."
    },
    {
        "id": "false_binary",
        "name": "False Binary",
        "emoji": "⚡",
        "one_liner": "This or that?",
        "prompt_template": "Present two defensible options related to '{about}' where there's no right answer. The choice reveals values, not knowledge. Both options must be genuinely appealing."
    },
    {
        "id": "metaphor",
        "name": "Metaphor",
        "emoji": "🎭",
        "one_liner": "What are you LIKE?",
        "prompt_template": "Make the person translate '{about}' into a completely different domain — their life as a soup, their mind as a building, their career as weather. The translation IS the revelation."
    },
    {
        "id": "confirmation_trap",
        "name": "Confirmation Trap",
        "emoji": "🧪",
        "one_liner": "Ever tried to prove yourself wrong?",
        "prompt_template": "Challenge a belief related to '{about}' that the person has never stress-tested. Ask them to argue against themselves. Intellectual honesty over comfort."
    },
    {
        "id": "permission",
        "name": "Permission",
        "emoji": "🔓",
        "one_liner": "You can say it here.",
        "prompt_template": "Open the locked drawer around '{about}'. Give the person license to say something socially dangerous, taboo, or usually off-limits. The question itself creates the safe space."
    },
    {
        "id": "other_eyes",
        "name": "Other Eyes",
        "emoji": "👥",
        "one_liner": "What would they say about you?",
        "prompt_template": "Introduce another person's perspective on '{about}'. How does someone else experience you? The gap between self-image and how you land on others is where the truth lives."
    },
    {
        "id": "contradiction",
        "name": "Contradiction",
        "emoji": "⚔️",
        "one_liner": "Then why don't you?",
        "prompt_template": "Expose the gap between what the person says and what they do regarding '{about}'. Hold both truths side by side and ask them to look at the daylight between them."
    },
    {
        "id": "trajectory",
        "name": "Trajectory",
        "emoji": "📈",
        "one_liner": "Which direction are you moving?",
        "prompt_template": "Ask about the slope, not the snapshot, of '{about}'. Are they accelerating, decelerating, or plateauing? A trendline reveals more than a data point."
    },
    {
        "id": "confession",
        "name": "Confession",
        "emoji": "🗝️",
        "one_liner": "What are you hiding, even from yourself?",
        "prompt_template": "Pull something hidden into daylight about '{about}'. The question's job is to extract what the person knows but hasn't said — maybe even to themselves."
    },
    {
        "id": "scale",
        "name": "Scale",
        "emoji": "📊",
        "one_liner": "Add it all up — now how does it feel?",
        "prompt_template": "Force the person to aggregate their entire life experience of '{about}' into a single quantity. The cumulative total shocks — it reveals unconscious patterns through sheer volume."
    },
]

VECTOR_MAP = {v["id"]: v for v in VECTORS}

# Legacy archetype support for backward compatibility
ARCHETYPE_TO_VECTOR_MAPPING = {
    "the_specific": ["specificity", "name_an_example"],
    "the_shared_nerve": ["permission", "other_eyes"],
    "the_fork": ["false_binary", "comparison"],
    "the_flip": ["perspective_shift", "subversion"],
    "the_dare": ["confession", "self_assessment"],
    "the_build": ["hypothetical", "identity"],
    "auto": ["auto"]
}

CONTEXTS = ["onboarding", "discovery", "coaching", "rapport", "assessment", "content", "interview"]
DEPTHS = ["light", "medium", "deep"]

DEPTH_GUIDANCE = {
    "light": "Keep it fun, playful, bar-conversation energy. Humor first.",
    "medium": "Balance fun with genuine insight. Trojan horse depth.",
    "deep": "Go for real emotional or philosophical territory. Still specific, never vague.",
}

CONTEXT_GUIDANCE = {
    "onboarding": "Meeting this person for the first time. Build rapport while learning who they are.",
    "discovery": "Understand this person's needs, pain points, or goals.",
    "coaching": "Help this person grow. Questions should promote self-reflection.",
    "rapport": "Pure connection-building. Make the person feel seen.",
    "assessment": "Evaluate capabilities, personality, or fit.",
    "content": "Generating questions for social media, card decks, or publications.",
    "interview": "Structured conversation to learn about experience or perspective.",
}

CONTEXT_VECTOR_WEIGHTS = {
    "onboarding": {"specificity": 3, "name_an_example": 3, "false_binary": 3, "comparison": 2, "perspective_shift": 2, "identity": 2},
    "discovery": {"perspective_shift": 3, "subversion": 3, "false_binary": 3, "comparison": 2, "hypothetical": 2, "identity": 2},
    "coaching": {"confession": 3, "self_assessment": 3, "hypothetical": 3, "identity": 2, "perspective_shift": 2, "contradiction": 2},
    "rapport": {"permission": 3, "other_eyes": 3, "specificity": 3, "name_an_example": 2, "perspective_shift": 2, "false_binary": 2},
    "assessment": {"perspective_shift": 3, "subversion": 3, "false_binary": 3, "comparison": 2, "confession": 2, "self_assessment": 2},
    "content": {"permission": 3, "other_eyes": 3, "specificity": 2, "false_binary": 2, "comparison": 2, "perspective_shift": 2},
    "interview": {"confession": 2, "self_assessment": 2, "hypothetical": 3, "identity": 3, "perspective_shift": 2, "contradiction": 2},
}

SCORING_DIMENSIONS = {
    "surprise": {"weight": 0.25, "description": "Did the question catch you off guard?"},
    "specificity": {"weight": 0.20, "description": "Is it concrete and grounded, or vague?"},
    "conversation_fuel": {"weight": 0.20, "description": "Could this spark a 10+ minute discussion?"},
    "self_revelation": {"weight": 0.15, "description": "Does the answer reveal personality/values?"},
    "fun_factor": {"weight": 0.10, "description": "Would you enjoy being asked this at a bar?"},
    "universality": {"weight": 0.10, "description": "Can anyone answer regardless of background?"},
}

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

_corpus: list[str] = []


EXTRAS_PATH = os.getenv("EXTRAS_PATH", str(Path(__file__).parent / "seed-extras.txt"))


def load_corpus():
    """Load questions from DB. If DB is empty, seed from corpus + extras files."""
    global _corpus
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM questions WHERE active=1").fetchone()[0]
        if count == 0:
            # Seed from corpus text file
            for path, source in [(CORPUS_PATH, "corpus"), (EXTRAS_PATH, "manual")]:
                try:
                    text = Path(path).read_text()
                    file_questions = re.findall(r"^\d+\.\s+(.+)$", text, re.MULTILINE)
                    for q in file_questions:
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO questions (question, source) VALUES (?, ?)",
                                (q.strip(), source),
                            )
                        except Exception:
                            pass
                    logger.info("Seeded %d questions from %s", len(file_questions), path)
                except FileNotFoundError:
                    logger.info("Seed file not found: %s (skipping)", path)
            conn.commit()

        # Always load from DB
        rows = conn.execute("SELECT question FROM questions WHERE active=1 ORDER BY id").fetchall()
        _corpus = [r[0] for r in rows]
        logger.info("Loaded %d questions from database", len(_corpus))


# ---------------------------------------------------------------------------
# Rate limiter (IP-based, for unauthenticated endpoints)
# ---------------------------------------------------------------------------

_request_log: dict[str, list[float]] = {}
_generate_call_count: int = 0
PROMO_EVERY_N = int(os.getenv("PROMO_EVERY_N", "6"))
BOOK_PROMO = "📖 These questions use the 21 Vectors from END SMALL TALK by Cory Stout — endsmalltalknow.com"


def check_rate_limit(client_ip: str):
    now = time.time()
    window = _request_log.setdefault(client_ip, [])
    window[:] = [t for t in window if now - t < 60]
    if len(window) >= RATE_LIMIT_RPM:
        raise HTTPException(429, "Rate limit exceeded. Try again in a minute.")
    window.append(now)


# ---------------------------------------------------------------------------
# API Key auth helper
# ---------------------------------------------------------------------------

DEMO_API_KEY = "ba_demo_public_readonly"

def validate_api_key(x_api_key: str | None) -> dict:
    """Validate API key and check tier rate limit. Returns the key record."""
    if not x_api_key:
        raise HTTPException(401, detail="Missing X-API-Key header. Get one at /api-key/free or subscribe at /plans.")
    # Built-in demo key for the landing page Try It section (free-tier limits)
    if x_api_key == DEMO_API_KEY:
        return {"key": DEMO_API_KEY, "tier": "free", "calls_today": 0, "calls_date": "", "active": 1}
    record = get_api_key_record(x_api_key)
    if not record:
        raise HTTPException(401, detail="Invalid or deactivated API key.")
    if not increment_usage(x_api_key):
        tier = record["tier"]
        limit = TIERS.get(tier, {}).get("calls_per_day", 0)
        raise HTTPException(
            429,
            detail=f"Daily rate limit reached ({limit} calls/day on {tier} tier). Upgrade at {BASE_URL}/#pricing",
        )
    return record


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_corpus()
    yield


app = FastAPI(
    title="BetterAsk API",
    description=(
        "Question Intelligence API powered by END SMALL TALK methodology. "
        "21 vectors that combine to create multi-dimensional questions that extract real signal from humans."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


# IP rate-limit on non-API-key endpoints
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    skip = ("/health", "/docs", "/redoc", "/openapi.json", "/", "/static", "/webhook", "/plans")
    if not any(request.url.path.startswith(s) for s in skip):
        # Only IP-rate-limit if no API key provided (API key has its own limits)
        if not request.headers.get("x-api-key"):
            client = request.client.host if request.client else "unknown"
            check_rate_limit(client)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    context: str = Field("rapport", description=f"Use case context. Valid: {CONTEXTS}")
    about: str = Field(..., description="What you're trying to learn about", min_length=1, max_length=500)
    depth: str = Field("medium", description=f"Question depth. Valid: {DEPTHS}")
    vectors: str = Field("auto", description="Comma-separated vector names or 'auto'")
    archetype: str = Field(None, description="[Legacy] Specific archetype or 'auto' - use vectors instead")
    count: int = Field(3, ge=1, le=10, description="Number of questions to generate")
    avoid: list[str] = Field(default_factory=list, description="Topics to avoid")


class GeneratedQuestion(BaseModel):
    vectors: list[str]
    vector_names: list[str]
    vector_emojis: list[str] 
    generation_prompt: str
    example_from_corpus: Optional[str] = None
    # Legacy fields for backward compatibility
    archetype: Optional[str] = None
    archetype_name: Optional[str] = None
    archetype_emoji: Optional[str] = None


class GenerateResponse(BaseModel):
    questions: list[GeneratedQuestion]
    context: str
    depth: str
    count: int
    promo: Optional[str] = None


class ScoreRequest(BaseModel):
    question: str = Field(..., description="Question to score", min_length=1, max_length=1000)


class ScoreResponse(BaseModel):
    question: str
    scoring_prompt: str
    dimensions: dict
    vector_density: Optional[int] = None  # 1=functional, 2=good, 3=great, 4-5=hall of fame


class VectorResponse(BaseModel):
    vectors: list[dict]
    total: int

class ArchetypeResponse(BaseModel):
    archetypes: list[dict]
    total: int


class SubscribeRequest(BaseModel):
    tier: str = Field(..., description="Tier to subscribe to: builder, scale, or volume")
    success_url: str | None = Field(None, description="Override success redirect URL")
    cancel_url: str | None = Field(None, description="Override cancel redirect URL")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def select_vectors(context: str, requested: str = "auto", min_vectors: int = 2, max_vectors: int = 4) -> list[str]:
    """Select vectors for question generation."""
    if requested != "auto":
        # Comma-separated vector names provided
        selected = [v.strip() for v in requested.split(",") if v.strip() in VECTOR_MAP]
        return selected if selected else random.sample(list(VECTOR_MAP.keys()), min_vectors)
    
    # Auto-select based on context weights
    weights = CONTEXT_VECTOR_WEIGHTS.get(context, {})
    pool = []
    for vector_id, weight in weights.items():
        pool.extend([vector_id] * weight)
    
    # If no weights defined for context, use all vectors equally
    if not pool:
        pool = list(VECTOR_MAP.keys())
    
    # Select random number of vectors (min_vectors to max_vectors)
    num_vectors = random.randint(min_vectors, max_vectors)
    return random.sample(list(set(pool)), min(num_vectors, len(set(pool))))


def map_archetype_to_vectors(archetype: str) -> list[str]:
    """Convert legacy archetype to equivalent vectors."""
    return ARCHETYPE_TO_VECTOR_MAPPING.get(archetype, ["specificity", "name_an_example"])


def build_generation_prompt(context: str, about: str, depth: str, vectors: list[str], avoid: list[str]) -> str:
    """Build an LLM prompt from vector combination."""
    vector_instructions = []
    vector_names = []
    for v in vectors:
        vec = VECTOR_MAP[v]
        vector_names.append(vec["name"])
        vector_instructions.append(f"- {vec['prompt_template'].format(about=about)}")
    
    depth_note = DEPTH_GUIDANCE[depth]
    ctx_note = CONTEXT_GUIDANCE.get(context, "")
    avoid_note = f"\nAVOID these topics: {', '.join(avoid)}" if avoid else ""

    return f"""Generate ONE question using the EST (End Small Talk) methodology.

VECTORS TO COMBINE: {', '.join(vector_names)}

{chr(10).join(vector_instructions)}

CONTEXT: {ctx_note}
DEPTH: {depth_note}
{avoid_note}

RULES:
- Never academic or jargon-heavy. Use everyday language.
- Humor is a trojan horse for depth.
- Reference real, current things (2026 era).
- Concrete > abstract. Specific > vague.
- Include a natural follow-up question.
- The question presents; it never judges.
- Test: Would you want to answer this at a bar? If no, rewrite.
- Test: Could this start a 20-minute conversation? If no, sharpen.
- The question must activate ALL listed vectors simultaneously.

OUTPUT FORMAT (JSON):
{{
  "question": "The main question",
  "follow_up": "A natural follow-up question",
  "vectors": {json.dumps(vector_names)},
  "signal": "What this question reveals about the answerer",
  "depth": "{depth}"
}}"""


def build_scoring_prompt(question: str) -> str:
    return f"""Score this question using the EST (End Small Talk) rubric.

QUESTION: "{question}"

Score each dimension 1-10:
1. SURPRISE (25%): Did it catch you off guard? Unexpected angle?
2. SPECIFICITY (20%): Concrete and grounded, or vague?
3. CONVERSATION FUEL (20%): Could spark 10+ min discussion?
4. SELF-REVELATION (15%): Does the answer reveal personality/values?
5. FUN FACTOR (10%): Would you enjoy being asked this at a bar?
6. UNIVERSALITY (10%): Can anyone answer regardless of background?

Composite = (Surprise × 0.25) + (Specificity × 0.20) + (Conversation × 0.20) + (Revelation × 0.15) + (Fun × 0.10) + (Universal × 0.10)

Quality bands: 8-10 publish-worthy | 6-7 good | 4-5 generic | 1-3 delete

Vector Density Levels:
1 = Functional (basic question that works)
2 = Good (solid question with clear signal)
3 = Great (multi-layered, thought-provoking)
4-5 = Hall of Fame (unforgettable, shareable, transformative)

OUTPUT FORMAT (JSON):
{{
  "question": "{question}",
  "scores": {{
    "surprise": <1-10>,
    "specificity": <1-10>,
    "conversation_fuel": <1-10>,
    "self_revelation": <1-10>,
    "fun_factor": <1-10>,
    "universality": <1-10>
  }},
  "composite": <weighted average>,
  "band": "<publish-worthy|good|generic|delete>",
  "vector_density": <1-5>,
  "vectors_detected": ["<list of active vectors in this question>"],
  "improvement_suggestion": "<how to make it better>",
  "reasoning": "<brief explanation>"
}}"""


def resolve_stripe_price_id(product_id: str) -> str | None:
    """Look up the default price for a Stripe product."""
    if not STRIPE_SECRET_KEY:
        return None
    try:
        prices = stripe.Price.list(product=product_id, active=True, limit=1)
        if prices.data:
            return prices.data[0].id
        return None
    except Exception as e:
        logger.error("Failed to resolve price for product %s: %s", product_id, e)
        return None


# Cache price IDs after first lookup
_price_cache: dict[str, str] = {}


def get_price_id(tier: str) -> str:
    if tier in _price_cache:
        return _price_cache[tier]
    product_id = TIERS[tier]["stripe_product_id"]
    if not product_id:
        raise HTTPException(400, "Free tier does not require payment.")
    price_id = resolve_stripe_price_id(product_id)
    if not price_id:
        raise HTTPException(500, f"Could not resolve Stripe price for {tier} tier. Check product configuration.")
    _price_cache[tier] = price_id
    return price_id


# ---------------------------------------------------------------------------
# Endpoints — Stripe & API Keys
# ---------------------------------------------------------------------------

@app.get("/plans")
async def get_plans():
    """Return available tiers with pricing info."""
    plans = []
    for tier_id, info in TIERS.items():
        plans.append({
            "tier": tier_id,
            "name": info["name"],
            "price_monthly": info.get("price", None),
            "price_per_call": info.get("price_per_call", None),
            "calls_per_day": info["calls_per_day"],
            "calls_per_day_display": f"{info['calls_per_day']:,}" if info["calls_per_day"] else "Unlimited",
        })
    return {"plans": plans}


@app.post("/api-key/free")
async def create_free_key():
    """Instantly create a free-tier API key (no payment required)."""
    key = create_api_key(tier="free")
    return {
        "api_key": key,
        "tier": "free",
        "calls_per_day": TIERS["free"]["calls_per_day"],
        "message": "Store this key securely — it won't be shown again.",
    }


@app.post("/subscribe")
async def subscribe(req: SubscribeRequest):
    """Create a Stripe Checkout Session for a paid tier."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe is not configured. Set STRIPE_SECRET_KEY.")
    if req.tier not in ("builder", "scale", "volume"):
        raise HTTPException(400, f"Invalid tier: {req.tier}. Choose builder, scale, or volume.")

    price_id = get_price_id(req.tier)
    success_url = req.success_url or f"{BASE_URL}/subscribe/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = req.cancel_url or f"{BASE_URL}/#pricing"

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"tier": req.tier},
        )
        logger.info("Created Stripe checkout session %s for tier %s", session.id, req.tier)
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.StripeError as e:
        logger.error("Stripe checkout error: %s", e)
        raise HTTPException(502, f"Stripe error: {str(e)}")


@app.get("/subscribe/success")
async def subscribe_success(session_id: str):
    """Post-checkout success page. Shows the API key."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured.")
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        customer_id = session.customer
        subscription_id = session.subscription
        tier = session.metadata.get("tier", "builder")

        # Check if we already created a key for this subscription
        with get_db() as conn:
            existing = conn.execute(
                "SELECT key FROM api_keys WHERE stripe_subscription_id = ? AND active = 1",
                (subscription_id,)
            ).fetchone()

        if existing:
            api_key = existing["key"]
        else:
            api_key = create_api_key(tier=tier, stripe_customer_id=customer_id,
                                     stripe_subscription_id=subscription_id)

        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>BetterAsk — Subscription Active</title>
<style>
  body {{ background: #0a0a0f; color: #e0e0e8; font-family: system-ui; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
  .card {{ background: #12121a; border: 1px solid #1e1e2e; border-radius: 16px; padding: 48px; max-width: 560px; text-align: center; }}
  h1 {{ color: #4ade80; margin-bottom: 16px; }}
  .key {{ background: #0a0a0f; border: 1px solid #7c6aef; border-radius: 8px; padding: 16px; font-family: monospace; font-size: 1.1em; margin: 24px 0; word-break: break-all; color: #7c6aef; cursor: pointer; }}
  .warning {{ color: #fb923c; font-size: 0.9em; margin-top: 12px; }}
  a {{ color: #7c6aef; }}
</style></head><body>
<div class="card">
  <h1>✅ You're In!</h1>
  <p>Your <strong>{tier.title()}</strong> subscription is active.</p>
  <p style="margin-top:8px; color:#8888aa;">Your API Key:</p>
  <div class="key" onclick="navigator.clipboard.writeText(this.textContent).then(()=>this.style.borderColor='#4ade80')" title="Click to copy">{api_key}</div>
  <p class="warning">⚠️ Copy this now — it won't be shown again.</p>
  <p style="margin-top:24px;"><a href="/docs">API Docs →</a></p>
</div></body></html>""")
    except stripe.StripeError as e:
        logger.error("Error retrieving checkout session: %s", e)
        raise HTTPException(502, f"Could not verify subscription: {e}")


@app.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook secret not configured.")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        logger.warning("Webhook signature verification failed")
        raise HTTPException(400, "Invalid signature")
    except Exception as e:
        logger.error("Webhook construction error: %s", e)
        raise HTTPException(400, str(e))

    event_type = event["type"]
    data = event["data"]["object"]
    logger.info("Webhook received: %s", event_type)

    if event_type == "customer.subscription.created":
        customer_id = data["customer"]
        subscription_id = data["id"]
        # Determine tier from product
        items = data.get("items", {}).get("data", [])
        tier = "builder"  # default
        for item in items:
            product_id = item.get("price", {}).get("product")
            if product_id in PRODUCT_TO_TIER:
                tier = PRODUCT_TO_TIER[product_id]
                break

        # Key may already exist (created at checkout success), ensure it exists
        with get_db() as conn:
            existing = conn.execute(
                "SELECT key FROM api_keys WHERE stripe_subscription_id = ? AND active = 1",
                (subscription_id,)
            ).fetchone()
        if not existing:
            create_api_key(tier=tier, stripe_customer_id=customer_id,
                           stripe_subscription_id=subscription_id)
        logger.info("Subscription created: customer=%s tier=%s", customer_id, tier)

    elif event_type == "customer.subscription.deleted":
        subscription_id = data["id"]
        deactivate_keys_for_subscription(subscription_id)
        logger.info("Subscription cancelled: %s", subscription_id)

    elif event_type == "customer.subscription.updated":
        subscription_id = data["id"]
        items = data.get("items", {}).get("data", [])
        for item in items:
            product_id = item.get("price", {}).get("product")
            if product_id in PRODUCT_TO_TIER:
                upgrade_keys_for_subscription(subscription_id, PRODUCT_TO_TIER[product_id])
                break

    elif event_type == "invoice.paid":
        logger.info("Invoice paid: %s", data.get("id"))
        # Subscription continues — nothing to do

    elif event_type == "invoice.payment_failed":
        logger.warning("Payment failed for customer %s", data.get("customer"))

    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Endpoints — Core API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Admin — Question Management (requires admin key)
# ---------------------------------------------------------------------------
ADMIN_KEY = os.getenv("BETTERASK_ADMIN_KEY", "ba_admin_cory_2026")


def require_admin(key: str | None):
    if key != ADMIN_KEY:
        raise HTTPException(403, "Admin access required.")


class AddQuestionsRequest(BaseModel):
    questions: list[str]
    source: str = "manual"
    vectors: str | None = None  # Comma-separated vector names
    archetype: str | None = None  # Legacy support


@app.post("/admin/questions")
async def add_questions(
    req: AddQuestionsRequest,
    x_admin_key: str | None = Header(None),
):
    """Add one or more questions to the permanent database."""
    require_admin(x_admin_key)
    added = 0
    with get_db() as conn:
        for q in req.questions:
            q = q.strip()
            if not q:
                continue
            try:
                # Convert legacy archetype to vectors if needed
                vectors = req.vectors
                if not vectors and req.archetype:
                    vectors = ",".join(map_archetype_to_vectors(req.archetype))
                
                conn.execute(
                    "INSERT OR IGNORE INTO questions (question, archetype, vectors, source) VALUES (?, ?, ?, ?)",
                    (q, req.archetype, vectors, req.source),
                )
                added += 1
            except Exception:
                pass
        conn.commit()
    # Reload corpus
    load_corpus()
    return {"added": added, "total": len(_corpus)}


@app.get("/admin/questions")
async def list_questions(
    x_admin_key: str | None = Header(None),
    source: str | None = None,
    vectors: str | None = None,
    archetype: str | None = None,  # Legacy support
    limit: int = 50,
    offset: int = 0,
):
    """List questions from the database with optional filters."""
    require_admin(x_admin_key)
    query = "SELECT id, question, archetype, vectors, source, score_composite, added_at FROM questions WHERE active=1"
    params = []
    if source:
        query += " AND source=?"
        params.append(source)
    if vectors:
        query += " AND vectors LIKE ?"
        params.append(f"%{vectors}%")
    elif archetype:
        query += " AND archetype=?"
        params.append(archetype)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM questions WHERE active=1").fetchone()[0]

    return {
        "questions": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.delete("/admin/questions/{question_id}")
async def deactivate_question(question_id: int, x_admin_key: str | None = Header(None)):
    """Soft-delete a question (set active=0)."""
    require_admin(x_admin_key)
    with get_db() as conn:
        conn.execute("UPDATE questions SET active=0 WHERE id=?", (question_id,))
        conn.commit()
    load_corpus()
    return {"deactivated": question_id, "total": len(_corpus)}


class ImportQuestionsRequest(BaseModel):
    text: str
    source: str = "import"


@app.post("/admin/questions/import")
async def import_questions_file(
    req: ImportQuestionsRequest,
    x_admin_key: str | None = Header(None),
):
    """Import questions from numbered text (1. Question\\n2. Question...)."""
    require_admin(x_admin_key)
    imported = re.findall(r"^\d+\.\s+(.+)$", req.text, re.MULTILINE)
    added = 0
    with get_db() as conn:
        for q in imported:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO questions (question, source) VALUES (?, ?)",
                    (q.strip(), req.source),
                )
                added += 1
            except Exception:
                pass
        conn.commit()
    load_corpus()
    return {"parsed": len(imported), "added": added, "total": len(_corpus)}


@app.get("/admin/stats")
async def admin_stats(x_admin_key: str | None = Header(None)):
    """Usage stats: total keys, calls today, all-time estimate."""
    require_admin(x_admin_key)
    with get_db() as conn:
        total_keys = conn.execute("SELECT COUNT(*) FROM api_keys WHERE active=1").fetchone()[0]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        calls_today = conn.execute("SELECT SUM(calls_today) FROM api_keys WHERE calls_date=?", (today,)).fetchone()[0] or 0
        keys = conn.execute("SELECT key, tier, calls_today, calls_date, created_at FROM api_keys WHERE active=1 ORDER BY created_at DESC").fetchall()
    return {
        "total_keys": total_keys,
        "calls_today": calls_today,
        "corpus_size": len(_corpus),
        "keys": [dict(r) for r in keys],
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "corpus_size": len(_corpus), "vectors": len(VECTORS), "version": "2.0.0"}


@app.get("/vectors", response_model=VectorResponse)
async def get_vectors():
    return {"vectors": VECTORS, "total": len(VECTORS)}


@app.get("/archetypes", response_model=ArchetypeResponse)
async def get_archetypes():
    """Legacy endpoint - use /vectors instead"""
    # Convert vectors to archetype-like format for backward compatibility
    legacy_archetypes = []
    for vector in VECTORS[:6]:  # Return first 6 for compatibility
        legacy_archetypes.append({
            "id": vector["id"],
            "name": vector["name"],
            "emoji": vector["emoji"],
            "description": vector["one_liner"],
            "pattern": vector["prompt_template"][:100] + "...",
        })
    return {"archetypes": legacy_archetypes, "total": len(legacy_archetypes)}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, request: Request):
    # Free for everyone — no API key required
    client = request.client.host if request.client else "unknown"
    check_rate_limit(client)

    try:
        if req.context not in CONTEXTS:
            raise HTTPException(400, f"Unknown context: {req.context}. Valid: {CONTEXTS}")

        # Handle legacy archetype parameter
        if req.archetype and req.vectors == "auto":
            if req.archetype in ARCHETYPE_TO_VECTOR_MAPPING:
                req.vectors = ",".join(ARCHETYPE_TO_VECTOR_MAPPING[req.archetype])
            else:
                raise HTTPException(400, f"Unknown archetype: {req.archetype}. Use 'vectors' parameter instead.")

        questions = []
        used_vector_sets = set()
        
        for _ in range(req.count):
            # Select vectors for this question
            vectors = select_vectors(req.context, req.vectors)
            
            # Avoid duplicate vector combinations when possible
            vector_signature = tuple(sorted(vectors))
            if req.count <= 10 and vector_signature in used_vector_sets:
                attempts = 0
                while vector_signature in used_vector_sets and attempts < 5:
                    vectors = select_vectors(req.context, req.vectors)
                    vector_signature = tuple(sorted(vectors))
                    attempts += 1
            used_vector_sets.add(vector_signature)

            prompt = build_generation_prompt(req.context, req.about, req.depth, vectors, req.avoid)
            vector_infos = [VECTOR_MAP[v] for v in vectors]
            vector_names = [v["name"] for v in vector_infos]
            vector_emojis = [v["emoji"] for v in vector_infos]

            example = None
            if _corpus:
                example = random.choice(_corpus)

            question = GeneratedQuestion(
                vectors=vectors,
                vector_names=vector_names,
                vector_emojis=vector_emojis,
                generation_prompt=prompt,
                example_from_corpus=example,
            )
            
            # Add legacy fields for backward compatibility
            if len(vectors) > 0:
                question.archetype = vectors[0]  # Use first vector as archetype
                question.archetype_name = vector_infos[0]["name"]
                question.archetype_emoji = vector_infos[0]["emoji"]

            questions.append(question)

        global _generate_call_count
        _generate_call_count += 1
        promo = BOOK_PROMO if _generate_call_count % PROMO_EVERY_N == 0 else None
        return GenerateResponse(questions=questions, context=req.context, depth=req.depth, count=req.count, promo=promo)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Generate endpoint error")
        raise HTTPException(500, f"Generation failed: {str(e)}")


@app.post("/score", response_model=ScoreResponse)
async def score(req: ScoreRequest, request: Request):
    # Free for everyone — no API key required
    client = request.client.host if request.client else "unknown"
    check_rate_limit(client)
    prompt = build_scoring_prompt(req.question)
    # Vector density is calculated by the LLM based on the scoring prompt
    return ScoreResponse(question=req.question, scoring_prompt=prompt, dimensions=SCORING_DIMENSIONS, vector_density=None)


# ---------------------------------------------------------------------------
# /ask — The Zero-Config Brain
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    memory: str = Field(..., description="What you know about this person", max_length=5000)
    history: list[str] = Field(default=[], description="Previous questions already asked", max_length=50)
    goal: str | None = Field(None, description="Optional: what you're trying to learn (auto-detected if omitted)")
    count: int = Field(1, ge=1, le=5, description="Number of questions to return")


class AskQuestion(BaseModel):
    question: str
    follow_up: str | None = None
    vectors: list[str]
    vector_names: list[str]
    density: int
    why: str
    source: str  # "corpus" or "generated"
    generation_prompt: str | None = None  # included if caller wants to regenerate via LLM


class AskResponse(BaseModel):
    questions: list[AskQuestion]
    analysis: dict
    promo: str | None = None


def analyze_memory(memory: str, history: list[str]) -> dict:
    """Analyze memory + history to determine what vectors and topics to target."""
    memory_lower = memory.lower()
    history_lower = [h.lower() for h in history]
    history_joined = " ".join(history_lower)
    
    # Detect themes in memory
    themes = []
    theme_keywords = {
        "career": ["work", "job", "career", "boss", "office", "company", "startup", "business", "role", "promotion", "fired", "hired", "salary"],
        "relationships": ["partner", "wife", "husband", "boyfriend", "girlfriend", "dating", "married", "divorce", "ex", "relationship", "love", "breakup"],
        "family": ["mom", "dad", "mother", "father", "sister", "brother", "parents", "kids", "children", "family", "son", "daughter"],
        "health": ["health", "fitness", "gym", "diet", "sleep", "anxiety", "depression", "therapy", "mental", "weight", "exercise", "sick"],
        "creativity": ["creative", "art", "music", "writing", "design", "build", "project", "create", "maker", "content"],
        "growth": ["stuck", "change", "growth", "improve", "learn", "goal", "dream", "ambition", "potential", "purpose"],
        "money": ["money", "debt", "savings", "invest", "rich", "poor", "financial", "income", "budget", "afford"],
        "identity": ["identity", "values", "believe", "personality", "introvert", "extrovert", "who am i", "purpose", "meaning"],
        "social": ["friends", "social", "lonely", "community", "network", "party", "group", "belong"],
        "location": ["moved", "moving", "city", "town", "home", "travel", "live in", "from", "relocated"],
    }
    
    for theme, keywords in theme_keywords.items():
        if any(kw in memory_lower for kw in keywords):
            themes.append(theme)
    
    # Detect emotional signals
    emotional_signals = []
    emotion_keywords = {
        "stuck": ["stuck", "stagnant", "plateau", "rut", "same", "nothing changes"],
        "excited": ["excited", "pumped", "thrilled", "can't wait", "amazing", "new"],
        "anxious": ["anxious", "worried", "nervous", "stressed", "overwhelmed", "scared"],
        "nostalgic": ["remember", "used to", "back when", "miss", "childhood", "growing up"],
        "conflicted": ["torn", "conflicted", "not sure", "both", "dilemma", "should i"],
        "lonely": ["lonely", "alone", "isolated", "no one", "by myself", "miss people"],
        "ambitious": ["want to", "going to", "plan to", "dream", "goal", "build", "start"],
    }
    
    for signal, keywords in emotion_keywords.items():
        if any(kw in memory_lower for kw in keywords):
            emotional_signals.append(signal)
    
    # Detect what's already been covered by history
    covered_vectors = set()
    for h in history_lower:
        if any(w in h for w in ["how many", "how much", "what percentage"]):
            covered_vectors.add("specificity")
        if any(w in h for w in ["what was", "name a", "which", "favorite"]):
            covered_vectors.add("name_an_example")
        if any(w in h for w in ["imagine", "what if", "would you rather"]):
            covered_vectors.add("hypothetical")
        if any(w in h for w in ["scale of", "1-10", "rate yourself"]):
            covered_vectors.add("self_assessment")
        if any(w in h for w in ["how do you feel", "what does that feel"]):
            covered_vectors.add("emotion")
        if any(w in h for w in ["compared to", "or", "which is more"]):
            covered_vectors.add("comparison")
        if any(w in h for w in ["years ago", "last time", "when did"]):
            covered_vectors.add("time")
    
    # Determine best vectors based on analysis
    recommended_vectors = []
    
    # If person is stuck → trajectory, contradiction, confession
    if "stuck" in emotional_signals or "growth" in themes:
        recommended_vectors.extend(["trajectory", "contradiction", "confession"])
    
    # If person is new/excited → hypothetical, identity, comparison
    if "excited" in emotional_signals or "location" in themes:
        recommended_vectors.extend(["hypothetical", "identity", "comparison"])
    
    # If emotional territory detected → emotion, permission, other_eyes
    if any(s in emotional_signals for s in ["anxious", "lonely", "conflicted"]):
        recommended_vectors.extend(["emotion", "permission", "other_eyes"])
    
    # If career/money themes → self_assessment, trajectory, scale
    if "career" in themes or "money" in themes:
        recommended_vectors.extend(["self_assessment", "trajectory", "scale"])
    
    # If relationships → other_eyes, contradiction, permission
    if "relationships" in themes or "family" in themes:
        recommended_vectors.extend(["other_eyes", "contradiction", "permission"])
    
    # If identity/meaning → identity, metaphor, confession
    if "identity" in themes:
        recommended_vectors.extend(["identity", "metaphor", "confession"])
    
    # If creativity → sensory_imagination, metaphor, subversion
    if "creativity" in themes:
        recommended_vectors.extend(["sensory_imagination", "metaphor", "subversion"])
    
    # Default: always good vectors
    if not recommended_vectors:
        recommended_vectors = ["specificity", "hypothetical", "self_assessment", "time", "identity"]
    
    # Remove already-covered vectors, prioritize fresh ones
    fresh_vectors = [v for v in recommended_vectors if v not in covered_vectors]
    if len(fresh_vectors) < 2:
        fresh_vectors = recommended_vectors  # fall back if too many covered
    
    # Deduplicate while preserving order
    seen = set()
    unique_vectors = []
    for v in fresh_vectors:
        if v not in seen:
            seen.add(v)
            unique_vectors.append(v)
    
    # Determine depth from emotional signals
    depth = "medium"
    if len(history) == 0:
        depth = "light"  # first question should be approachable
    elif len(history) >= 3:
        depth = "deep"  # they've been talking, go deeper
    if any(s in emotional_signals for s in ["anxious", "lonely", "conflicted"]):
        depth = "deep"  # emotional state warrants depth
    
    # Determine implicit goal
    goal = "rapport"
    if "career" in themes or "money" in themes:
        goal = "discovery"
    if "stuck" in emotional_signals or "growth" in themes:
        goal = "coaching"
    if "identity" in themes:
        goal = "assessment"
    if len(history) == 0:
        goal = "onboarding"
    
    return {
        "themes": themes,
        "emotional_signals": emotional_signals,
        "covered_vectors": list(covered_vectors),
        "recommended_vectors": unique_vectors[:6],
        "depth": depth,
        "goal": goal,
        "history_depth": len(history),
    }


def find_corpus_match(vectors: list[str], themes: list[str], history: list[str]) -> str | None:
    """Find the best matching question from the 607 corpus."""
    if not _corpus:
        return None
    
    # Load tagged corpus if available
    tagged_path = Path(__file__).parent.parent / "skills" / "betterask" / "assets" / "tagged_corpus.json"
    if not tagged_path.exists():
        tagged_path = Path(__file__).parent / "tagged_corpus.json"
    
    if tagged_path.exists():
        try:
            with open(tagged_path) as f:
                tagged = json.load(f)
            
            # Score each question by vector overlap
            candidates = []
            history_set = set(h.lower().strip() for h in history)
            
            for q in tagged.get("questions", []):
                q_vectors = set(q.get("vectors", []))
                requested = set(vectors[:4])
                
                # Skip if already asked
                if q["text"].lower().strip() in history_set:
                    continue
                
                overlap = len(q_vectors & requested)
                density = q.get("vector_count", 0)
                
                if overlap > 0:
                    score = overlap * 10 + density * 3 + random.random() * 2
                    candidates.append((score, q))
            
            if candidates:
                candidates.sort(key=lambda x: -x[0])
                # Pick from top 5 with some randomness
                top = candidates[:5]
                winner = random.choice(top)
                return winner[1]["text"]
        except Exception as e:
            logger.warning("Tagged corpus load failed: %s", e)
    
    # Fallback: random from corpus
    available = [q for q in _corpus if q.lower() not in set(h.lower() for h in history)]
    return random.choice(available) if available else random.choice(_corpus)


def build_why(analysis: dict, vectors: list[str]) -> str:
    """Generate a human-readable explanation of why this question was chosen."""
    parts = []
    
    if analysis["themes"]:
        parts.append(f"Detected themes: {', '.join(analysis['themes'][:3])}")
    
    if analysis["emotional_signals"]:
        parts.append(f"Emotional signals: {', '.join(analysis['emotional_signals'][:2])}")
    
    if analysis["covered_vectors"]:
        parts.append(f"Already explored: {', '.join(analysis['covered_vectors'][:3])}")
    
    vector_names = [VECTOR_MAP[v]["name"] for v in vectors if v in VECTOR_MAP]
    parts.append(f"Selected vectors: {' + '.join(vector_names)}")
    
    depth_reasons = {
        "light": "First interaction — keeping it approachable.",
        "medium": "Building on existing rapport.",
        "deep": "Enough trust established to go deeper.",
    }
    parts.append(depth_reasons.get(analysis["depth"], ""))
    
    return " ".join(parts)


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request):
    """The zero-config brain. Send memory + history, get the perfect next question."""
    client = request.client.host if request.client else "unknown"
    check_rate_limit(client)
    
    try:
        # Step 1: Analyze what we know
        analysis = analyze_memory(req.memory, req.history)
        
        # Override goal if explicitly provided
        if req.goal:
            if req.goal in CONTEXTS:
                analysis["goal"] = req.goal
        
        questions = []
        used_questions = set()
        
        for i in range(req.count):
            # Step 2: Pick vectors (2-4 per question, from recommendations)
            available = [v for v in analysis["recommended_vectors"] if v in VECTOR_MAP]
            if len(available) < 2:
                available = list(VECTOR_MAP.keys())
            
            num = random.randint(2, min(4, len(available)))
            selected = random.sample(available, num)
            
            # Step 3: Find best corpus match
            corpus_question = find_corpus_match(selected, analysis["themes"], req.history + list(used_questions))
            
            # Step 4: Also build a generation prompt for custom questions
            about = f"this person ({req.memory[:200]})"
            gen_prompt = build_generation_prompt(
                analysis["goal"], about, analysis["depth"], selected, []
            )
            
            # Step 5: Build the why
            why = build_why(analysis, selected)
            
            vector_names = [VECTOR_MAP[v]["name"] for v in selected if v in VECTOR_MAP]
            
            q = AskQuestion(
                question=corpus_question or "What's something you wish people understood about you without having to explain it?",
                follow_up=None,
                vectors=selected,
                vector_names=vector_names,
                density=len(selected),
                why=why,
                source="corpus" if corpus_question else "fallback",
                generation_prompt=gen_prompt,
            )
            questions.append(q)
            if corpus_question:
                used_questions.add(corpus_question)
        
        global _generate_call_count
        _generate_call_count += 1
        promo = BOOK_PROMO if _generate_call_count % PROMO_EVERY_N == 0 else None
        
        return AskResponse(
            questions=questions,
            analysis=analysis,
            promo=promo,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("/ask endpoint error")
        raise HTTPException(500, f"Ask failed: {str(e)}")


@app.get("/", response_class=HTMLResponse)
async def landing():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text())
