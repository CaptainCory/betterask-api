"""
BetterAsk API — Question Intelligence powered by END SMALL TALK methodology.
Stop asking "How can I help you?" — BetterAsk.
"""

import hashlib
import logging
import os
import random
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date
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
    "free": {"name": "Free", "price": 0, "calls_per_day": 1_000, "stripe_product_id": None},
    "builder": {"name": "Builder", "price": 9, "calls_per_day": 3_000, "stripe_product_id": os.getenv("STRIPE_BUILDER_PRODUCT_ID", "")},
    "metered": {"name": "Pay-as-you-go", "price_per_call": 0.01, "calls_per_day": None, "stripe_product_id": os.getenv("STRIPE_METERED_PRODUCT_ID", "")},
}

# Per-call rate for metered billing (cents)
METERED_RATE = 0.01

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
# Data (archetypes, contexts, etc.)
# ---------------------------------------------------------------------------

ARCHETYPES = [
    {
        "id": "reframe",
        "name": "The Reframe",
        "emoji": "🎭",
        "description": "Take something mundane, view it through an unexpected metaphor.",
        "pattern": "[Familiar thing] reframed as [unexpected domain]",
        "example": "Describing it as if it's a crime, what do you do for a living?",
        "signal": "Creative thinking style, how they see the world",
    },
    {
        "id": "specificity_trap",
        "name": "The Specificity Trap",
        "emoji": "🔢",
        "description": "Ask for a weirdly specific number. The precision forces honesty.",
        "pattern": "How many [specific thing]?",
        "example": "How many rowdy seven-year-olds would it take to beat you up?",
        "signal": "Self-awareness, honesty, humor",
    },
    {
        "id": "false_binary",
        "name": "The False Binary",
        "emoji": "⚖️",
        "description": "Two options, both defensible. The pick reveals values.",
        "pattern": "[Option A] OR [Option B] — no right answer",
        "example": "Would you rather have a big house in Wyoming OR 1,000 sq ft overlooking Central Park?",
        "signal": "Core values, priorities, identity",
    },
    {
        "id": "mirror",
        "name": "The Mirror",
        "emoji": "🪞",
        "description": "Make someone see themselves from outside.",
        "pattern": "Turn the person into an object of their own observation",
        "example": "What are you trying to communicate by the clothes you're currently wearing?",
        "signal": "Self-awareness, body relationship, identity",
    },
    {
        "id": "thought_experiment",
        "name": "The Thought Experiment",
        "emoji": "🧮",
        "description": "Absurd hypothetical that's secretly revealing.",
        "pattern": "Extreme scenario → reveals risk tolerance, self-knowledge",
        "example": "$10M, immortality, and a turtle that kills you on touch. Plan?",
        "signal": "Problem-solving style, self-belief, creativity",
    },
    {
        "id": "time_machine",
        "name": "The Time Machine",
        "emoji": "🕰️",
        "description": "Memory, aging, time perception as lens.",
        "pattern": "Time as a mirror on life satisfaction",
        "example": "If you repeated yesterday 365 times, where would you be?",
        "signal": "Present-moment awareness, regret, aspiration",
    },
    {
        "id": "absurd_escalation",
        "name": "The Absurd Escalation",
        "emoji": "🎪",
        "description": "Start normal, escalate to ridiculous.",
        "pattern": "Mundane → absurdly meaningful follow-up",
        "example": "What cacao % in your chocolate? → Is that an indicator of emotional maturity?",
        "signal": "Humor, willingness to play",
    },
    {
        "id": "vulnerability_door",
        "name": "The Vulnerability Door",
        "emoji": "💔",
        "description": "Gentle question that opens real emotional space.",
        "pattern": "Simple, quiet, touches something real",
        "example": "What did you need to do in your family to get noticed?",
        "signal": "Emotional depth, trust level, life experience",
    },
    {
        "id": "identity_sort",
        "name": "The Identity Sort",
        "emoji": "🏷️",
        "description": "Force self-categorization.",
        "pattern": "Which [category] are you?",
        "example": "Which day of the week fits your personality?",
        "signal": "Self-concept, values, how they want to be seen",
    },
    {
        "id": "explain_it",
        "name": "The Explain-It Test",
        "emoji": "🔬",
        "description": "Make them explain something they think they know.",
        "pattern": "Explain [common thing]",
        "example": "To the best of your ability, explain how a microwave works.",
        "signal": "Intellectual humility, knowledge vs confidence gap",
    },
    {
        "id": "world_builder",
        "name": "The World-Builder",
        "emoji": "🌍",
        "description": "Design your ideal life/space/world.",
        "pattern": "Invite them to build their ideal — what they choose = what they're missing",
        "example": "Last day on Earth, one place to sit all day. Where?",
        "signal": "Aspirations, unmet needs, imagination",
    },
    {
        "id": "chain",
        "name": "The Chain",
        "emoji": "🔗",
        "description": "Rapid sequence building trust through rhythm.",
        "pattern": "Multiple quick questions building momentum",
        "example": "Favorite COLOR? SOUND? SMELL? — same frame, different senses",
        "signal": "Cumulative self-revelation, comfort level increasing",
    },
]

ARCHETYPE_MAP = {a["id"]: a for a in ARCHETYPES}

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

CONTEXT_ARCHETYPE_WEIGHTS = {
    "onboarding": {"identity_sort": 3, "mirror": 2, "reframe": 2, "specificity_trap": 2},
    "discovery": {"reframe": 3, "false_binary": 3, "time_machine": 2, "world_builder": 2},
    "coaching": {"time_machine": 3, "vulnerability_door": 3, "mirror": 2, "thought_experiment": 2},
    "rapport": {"reframe": 2, "specificity_trap": 2, "absurd_escalation": 3, "identity_sort": 2},
    "assessment": {"explain_it": 3, "thought_experiment": 3, "false_binary": 2, "reframe": 2},
    "content": {"reframe": 2, "false_binary": 2, "thought_experiment": 2, "specificity_trap": 2},
    "interview": {"vulnerability_door": 2, "time_machine": 2, "world_builder": 2, "reframe": 2},
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


def load_corpus():
    global _corpus
    try:
        text = Path(CORPUS_PATH).read_text()
        _corpus = re.findall(r"^\d+\.\s+(.+)$", text, re.MULTILINE)
        logger.info("Loaded %d questions from corpus", len(_corpus))
    except FileNotFoundError:
        logger.warning("Corpus not found at %s — examples will be empty", CORPUS_PATH)
        _corpus = []


# ---------------------------------------------------------------------------
# Rate limiter (IP-based, for unauthenticated endpoints)
# ---------------------------------------------------------------------------

_request_log: dict[str, list[float]] = {}


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
        "12 proven archetypes that extract real signal from humans."
    ),
    version="1.1.0",
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
    context: str = Field("rapport", description="Use case context", enum=CONTEXTS)
    about: str = Field(..., description="What you're trying to learn about", min_length=1, max_length=500)
    depth: str = Field("medium", description="Question depth", enum=DEPTHS)
    archetype: str = Field("auto", description="Specific archetype or 'auto'")
    count: int = Field(3, ge=1, le=10, description="Number of questions to generate")
    avoid: list[str] = Field(default_factory=list, description="Topics to avoid")


class GeneratedQuestion(BaseModel):
    archetype: str
    archetype_name: str
    archetype_emoji: str
    generation_prompt: str
    example_from_corpus: Optional[str] = None


class GenerateResponse(BaseModel):
    questions: list[GeneratedQuestion]
    context: str
    depth: str
    count: int


class ScoreRequest(BaseModel):
    question: str = Field(..., description="Question to score", min_length=1, max_length=1000)


class ScoreResponse(BaseModel):
    question: str
    scoring_prompt: str
    dimensions: dict


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

def select_archetype(context: str) -> str:
    weights = CONTEXT_ARCHETYPE_WEIGHTS.get(context, {})
    pool = []
    for a in ARCHETYPE_MAP:
        pool.extend([a] * weights.get(a, 1))
    return random.choice(pool)


ARCHETYPE_PROMPTS = {
    "reframe": "Generate a question that reframes '{about}' through an unexpected metaphor. The question should make the answerer see something familiar in a completely new way.",
    "specificity_trap": "Generate a question that asks for a weirdly specific number related to '{about}'. The precision should force honesty and reveal personality.",
    "false_binary": "Generate a 'Would you rather' style question about '{about}' where both options are genuinely appealing but choosing one reveals something deep about values.",
    "mirror": "Generate a question about '{about}' that makes the answerer see themselves from the outside.",
    "thought_experiment": "Generate an absurd but engaging hypothetical scenario related to '{about}'. It should seem playful but actually reveal how someone thinks.",
    "time_machine": "Generate a question that uses time or memory as a lens on '{about}'.",
    "absurd_escalation": "Generate a question about '{about}' that starts completely normal but escalates to something absurdly meaningful.",
    "vulnerability_door": "Generate a gentle, quiet question about '{about}' that opens a door to real emotional territory.",
    "identity_sort": "Generate a question that forces self-categorization related to '{about}'. Use unexpected categories.",
    "explain_it": "Generate a question that asks someone to explain something common related to '{about}' that most people can't actually articulate well.",
    "world_builder": "Generate a question about '{about}' that invites the answerer to design their ideal version of something.",
    "chain": "Generate a series of 3-4 quick questions about '{about}' that build on each other with rhythm.",
}


def build_generation_prompt(context: str, about: str, depth: str, archetype: str, avoid: list[str]) -> str:
    arch_prompt = ARCHETYPE_PROMPTS[archetype].format(about=about)
    depth_note = DEPTH_GUIDANCE[depth]
    ctx_note = CONTEXT_GUIDANCE.get(context, "")
    avoid_note = f"\nAVOID these topics: {', '.join(avoid)}" if avoid else ""

    return f"""Generate ONE question using the EST (End Small Talk) methodology.

ARCHETYPE: {archetype.replace('_', ' ').title()}
{arch_prompt}

CONTEXT: {ctx_note}
DEPTH: {depth_note}
{avoid_note}

RULES:
- Never academic — everyday language only
- Humor is a trojan horse for depth
- Reference real, current things (2026 era)
- Concrete > abstract. Specific > vague.
- Include a natural follow-up question
- The question presents; it never judges
- Test: Would you want to answer this at a bar? If no, rewrite.

OUTPUT FORMAT (JSON):
{{
  "question": "The main question",
  "follow_up": "A natural follow-up question",
  "archetype": "{archetype}",
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
  "archetype_detected": "<which of the 12 archetypes>",
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

@app.get("/health")
async def health():
    return {"status": "healthy", "corpus_size": len(_corpus), "version": "1.1.0"}


@app.get("/archetypes", response_model=ArchetypeResponse)
async def get_archetypes():
    return {"archetypes": ARCHETYPES, "total": len(ARCHETYPES)}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, x_api_key: str | None = Header(None)):
    record = validate_api_key(x_api_key)

    if req.archetype != "auto" and req.archetype not in ARCHETYPE_MAP:
        raise HTTPException(400, f"Unknown archetype: {req.archetype}. Valid: {list(ARCHETYPE_MAP.keys())}")
    if req.context not in CONTEXTS:
        raise HTTPException(400, f"Unknown context: {req.context}. Valid: {CONTEXTS}")

    questions = []
    used_archetypes = set()
    for _ in range(req.count):
        arch = req.archetype if req.archetype != "auto" else select_archetype(req.context)
        if req.archetype == "auto" and req.count <= len(ARCHETYPE_MAP):
            attempts = 0
            while arch in used_archetypes and attempts < 10:
                arch = select_archetype(req.context)
                attempts += 1
        used_archetypes.add(arch)

        prompt = build_generation_prompt(req.context, req.about, req.depth, arch, req.avoid)
        info = ARCHETYPE_MAP[arch]

        example = None
        if _corpus:
            example = random.choice(_corpus)

        questions.append(
            GeneratedQuestion(
                archetype=arch,
                archetype_name=info["name"],
                archetype_emoji=info["emoji"],
                generation_prompt=prompt,
                example_from_corpus=example,
            )
        )

    return GenerateResponse(questions=questions, context=req.context, depth=req.depth, count=req.count)


@app.post("/score", response_model=ScoreResponse)
async def score(req: ScoreRequest, x_api_key: str | None = Header(None)):
    record = validate_api_key(x_api_key)
    prompt = build_scoring_prompt(req.question)
    return ScoreResponse(question=req.question, scoring_prompt=prompt, dimensions=SCORING_DIMENSIONS)


@app.get("/", response_class=HTMLResponse)
async def landing():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text())
