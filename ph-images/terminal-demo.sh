#!/bin/bash
# Generate pretty terminal output for Product Hunt screenshot
echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
echo '  BetterAsk API — Quick Demo'
echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
echo ''
echo '$ curl -X POST https://betterask.dev/generate \'
echo '    -H "X-API-Key: ba_demo_public_readonly" \'
echo '    -H "Content-Type: application/json" \'
echo '    -d '\''{"context":"onboarding","about":"career motivation","count":1}'\'''
echo ''
curl -s -X POST https://betterask.dev/generate \
  -H "X-API-Key: ba_demo_public_readonly" \
  -H "Content-Type: application/json" \
  -d '{"context":"onboarding","about":"career motivation","count":1}' | python3 -m json.tool 2>/dev/null | head -25
echo ''
echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
echo ''
echo '$ curl -X POST https://betterask.dev/score \'
echo '    -H "X-API-Key: ba_demo_public_readonly" \'
echo '    -H "Content-Type: application/json" \'
echo '    -d '\''{"question":"What'\''s the most expensive thing you own that you never use?"}'\'''
echo ''
curl -s -X POST https://betterask.dev/score \
  -H "X-API-Key: ba_demo_public_readonly" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the most expensive thing you own that you never use?"}' | python3 -m json.tool 2>/dev/null
echo ''
echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
