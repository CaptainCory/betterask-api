# BetterAsk API

**Question Intelligence API** powered by the [END SMALL TALK](https://endsmalltalknow.com) methodology.

Stop asking "How can I help you?" — BetterAsk.

## What It Does

BetterAsk provides 12 proven question archetypes that extract real signal from humans. It generates LLM prompts for creating high-quality questions and scores any question against a 6-dimension rubric.

Built on 607 battle-tested questions from Cory Stout's END SMALL TALK books.

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your keys

# Run
uvicorn main:app --reload

# Or with Docker
docker build -t betterask .
docker run -p 8000:8000 --env-file .env betterask
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `STRIPE_SECRET_KEY` | Yes (for payments) | Stripe secret key (`sk_live_...` or `sk_test_...`) |
| `STRIPE_WEBHOOK_SECRET` | Yes (for webhooks) | Stripe webhook signing secret (`whsec_...`) |
| `BETTERASK_BASE_URL` | Yes | Public URL (e.g., `https://betterask.dev`) — used for Stripe redirects |
| `LOG_LEVEL` | No | Default: `INFO` |
| `RATE_LIMIT_RPM` | No | IP-based rate limit for unauthenticated endpoints. Default: `60` |
| `CORPUS_PATH` | No | Path to question corpus file |
| `DB_PATH` | No | SQLite database path. Default: `./betterask.db` |

## Authentication

All `/generate` and `/score` requests require an API key via the `X-API-Key` header:

```bash
curl -X POST https://betterask.dev/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ba_live_abc123..." \
  -d '{
    "context": "onboarding",
    "about": "what drives this person",
    "depth": "medium",
    "count": 3
  }'
```

### Getting an API Key

**Free tier** — instant, no payment:
```bash
curl -X POST https://betterask.dev/api-key/free
# Returns: { "api_key": "ba_live_...", "tier": "free", "calls_per_day": 100 }
```

**Paid tiers** — via Stripe Checkout:
```bash
curl -X POST https://betterask.dev/subscribe \
  -H "Content-Type: application/json" \
  -d '{"tier": "builder"}'
# Returns: { "checkout_url": "https://checkout.stripe.com/...", "session_id": "..." }
```

### Rate Limits by Tier

| Tier | Price | Daily Calls |
|------|-------|-------------|
| Free | $0 | 100 |
| Builder | $9/mo | 5,000 |
| Scale | $49/mo | 50,000 |
| Volume | $199/mo | Unlimited |

When the limit is hit, the API returns `429` with upgrade info.

## Stripe Webhook Setup

1. In Stripe Dashboard → Developers → Webhooks → Add endpoint
2. URL: `https://betterask.dev/webhook`
3. Events to listen for:
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.paid`
   - `invoice.payment_failed`
4. Copy the signing secret → set as `STRIPE_WEBHOOK_SECRET`

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/generate` | API Key | Generate questions using archetypes |
| `POST` | `/score` | API Key | Score a question against the EST rubric |
| `GET` | `/archetypes` | None | List all 12 archetypes |
| `GET` | `/plans` | None | List pricing tiers |
| `POST` | `/api-key/free` | None | Create a free-tier API key |
| `POST` | `/subscribe` | None | Create Stripe Checkout for paid tier |
| `POST` | `/webhook` | Stripe sig | Stripe webhook handler |
| `GET` | `/health` | None | Health check |
| `GET` | `/docs` | None | Swagger UI |
| `GET` | `/redoc` | None | ReDoc |

## The 12 Archetypes

🎭 Reframe · 🔢 Specificity Trap · ⚖️ False Binary · 🪞 Mirror · 🧮 Thought Experiment · 🕰️ Time Machine · 🎪 Absurd Escalation · 💔 Vulnerability Door · 🏷️ Identity Sort · 🔬 Explain-It Test · 🌍 World-Builder · 🔗 Chain

## Scoring Dimensions

| Dimension | Weight |
|-----------|--------|
| Surprise | 25% |
| Specificity | 20% |
| Conversation Fuel | 20% |
| Self-Revelation | 15% |
| Fun Factor | 10% |
| Universality | 10% |

## License

Proprietary — © Cory Stout. END SMALL TALK methodology and question corpus are protected intellectual property.
