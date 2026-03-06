# ClawHub Publishing Checklist — BetterAsk

## Pre-Publish

- [ ] Skill package exists: `~/workspace/skills/dist/betterask.skill` (31 KB)
- [ ] SKILL.md has proper frontmatter (name, description)
- [ ] All 12 archetypes documented in `references/archetypes.md`
- [ ] Scoring rubric in `references/scoring.md`
- [ ] Scripts work standalone: `generate.py`, `score.py`
- [ ] Corpus file included in `assets/corpus.txt`
- [ ] No API keys or secrets in any file
- [ ] License/attribution clear (proprietary, © Cory Stout)

## API Companion

- [ ] FastAPI app runs: `uvicorn main:app --reload`
- [ ] All endpoints return correct responses
- [ ] OpenAPI docs render at `/docs`
- [ ] Landing page loads at `/`
- [ ] CORS enabled
- [ ] Rate limiting works
- [ ] Dockerfile builds and runs
- [ ] README has usage examples

## Skill Metadata for ClawHub

```yaml
name: betterask
version: 1.0.0
author: Cory Stout
description: >
  Generate high-quality questions using the BetterAsk methodology —
  12 proven archetypes that extract real signal from humans.
  Stop asking "How can I help you?" — BetterAsk.
tags:
  - questions
  - conversation
  - onboarding
  - coaching
  - rapport
  - discovery
license: proprietary
homepage: https://betterask.dev
```

## Publishing Steps

1. [ ] Verify skill package: `openclaw skill verify betterask.skill`
2. [ ] Test install on clean agent: `openclaw skill install betterask.skill`
3. [ ] Confirm agent can use generate + score commands
4. [ ] Publish: `openclaw skill publish betterask.skill`
5. [ ] Verify listing on ClawHub
6. [ ] Test install from ClawHub: `openclaw skill install betterask`

## Post-Publish

- [ ] Deploy API to hosting (Railway/Render/VPS)
- [ ] Configure DNS (see DNS-SETUP.md)
- [ ] Verify https://betterask.dev loads
- [ ] Announce on social / Product Hunt
- [ ] Monitor rate limits and usage
- [ ] Set up error alerting

## Future Enhancements

- API key authentication for heavy users
- Webhook for new archetype additions
- SDKs (Python, JS, Go)
- LLM-powered scoring (not just prompt generation)
- Analytics dashboard
