#!/usr/bin/env bash
# Run this the morning of. Everything here has bitten someone.
cd "$(dirname "$0")/.."
set -a; . ./.env 2>/dev/null; set +a
w() { echo -e "  \033[33m!\033[0m $1"; }
o() { echo -e "  \033[32mok\033[0m $1"; }

echo
echo "PREFLIGHT"
echo
grep -q "maxBufferSize" gateway/config.yaml && o "maxBufferSize raised (images need it)" || w "no maxBufferSize -- real photos will 502 at 2 MiB"
grep -q "failureMode: failOpen" gateway/config.yaml && o "failureMode: failOpen" || w "failOpen MISSING -- one dead target kills every tool"
if [ -x ./bin/agentgateway ]; then
  # --version prints JSON across several lines; pull the value, not line 1.
  v=$(./bin/agentgateway --version 2>&1 | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$v" ] || v=$(./bin/agentgateway --version 2>&1 | head -1)
  o "pinned gateway: $v   (./bin/agentgateway -- this is the one that runs)"
  case "$v" in *1.4.*) w "v1.4.x cannot parse bare Gemini :generateContent paths -- the double hop will 400. See README." ;; esac
  case "$v" in *beta*|*rc*) w "that is a pre-release. ./scripts/install-gateway.sh pins the current default." ;; esac
  # The one that bit us: upgrading the binary on PATH changes nothing here,
  # because up.sh deliberately prefers the project-local pin.
  if command -v agentgateway >/dev/null 2>&1; then
    pv=$(agentgateway --version 2>&1 | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | head -1)
    if [ -n "$pv" ] && [ "$pv" != "$v" ]; then
      w "PATH has agentgateway $pv, but the demo runs the pinned $v."
      w "  to use $pv: ./scripts/install-gateway.sh v$pv   (then down && up)"
    fi
  fi
else
  w "no pinned gateway in ./bin -- run: ./scripts/install-gateway.sh"
fi
# --- MCP auth: publishing needs a person (Keycloak) ----------------------
if grep -qE "^    mcpAuthentication:" gateway/config.yaml; then
  iss=$(grep -m1 "^      issuer:" gateway/config.yaml | awk "{print \$2}")
  if curl -sf -m 4 "$iss/.well-known/openid-configuration" >/dev/null 2>&1; then
    o "MCP auth ON and Keycloak up ($iss)"
  else
    w "MCP auth is ON but Keycloak is DOWN -- the gateway will not load."
    w "  start Keycloak, or ./imagine auth off"
  fi
  aud=$(grep -m1 "^      - publish" gateway/config.yaml | tr -d " -")
  o "publish gated on a logged-in person (aud: ${aud:-publish-mcp-server})"
else
  o "MCP auth is off (publishing needs no login)"
fi

# --- sign-in callback port (MCP Inspector owns 6274) ---------------------
CBPORT=6274
if command -v lsof >/dev/null 2>&1; then
  hold=$(lsof -nP -iTCP:$CBPORT -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $1}')
elif (exec 3<>/dev/tcp/127.0.0.1/$CBPORT) 2>/dev/null; then
  hold="something"
fi
if [ -n "${hold:-}" ]; then
  w "port $CBPORT is IN USE (held by: ${hold}) -- most likely MCP Inspector."
  w "  it will intercept the Keycloak sign-in redirect, so publishing hangs"
  w "  with 'no callback'. Quit it before the demo, or paste the URL at login."
else
  o "sign-in callback port $CBPORT is free (no Inspector squatting it)"
fi

# --- GitHub via the gateway ----------------------------------------------
if grep -q "host: api.github.com:443" gateway/config.yaml; then
  o "GitHub routed through the gateway (backendAuth holds the token)"
  grep -q "clear_env: true" gateway/config.yaml \
    && o "publish MCP target runs with clear_env -- no GITHUB_TOKEN in it" \
    || w "publish target has no clear_env -- it inherits GITHUB_TOKEN from the gateway"
  if grep -c "keyHash" gateway/config.yaml | grep -qv "^1$"; then
    o "GitHub routes are guarded by the virtual key (inbound), not open on :3004"
  else
    w ":3004 has NO inbound guard -- anything on this laptop can act as you on GitHub"
  fi
  if grep -q "host: uploads.github.com:443" gateway/config.yaml; then
    o "uploads.github.com routed too (release assets go to a different host)"
  else
    w "no uploads.github.com route -- the release ASSET upload will not be authenticated"
  fi
else
  w "publish talks to api.github.com directly and holds the token itself"
fi

# --- virtual key + budgets (1.5.0) ---------------------------------------
# ^    apiKey: -- the POLICY at llm.policies, not the provider
# credentials at llm.models[].params.apiKey, which are indented deeper.
if grep -q "^    apiKey:" gateway/config.yaml; then
  if [ -z "${AGW_VIRTUAL_KEY:-}" ]; then
    w "config has an apiKey policy but AGW_VIRTUAL_KEY is unset -- the agents"
    w "  will send the old 'agentgateway' placeholder, which is now an INVALID"
    w "  key and gets rejected. Set it in .env."
  else
    want=$(grep -o "sha256:[0-9a-fA-F]\{64\}" gateway/config.yaml | head -1 | cut -d: -f2 | tr 'A-Z' 'a-z')
    have=$(printf '%s' "$AGW_VIRTUAL_KEY" | { shasum -a 256 2>/dev/null || sha256sum; } | cut -d' ' -f1)
    if [ -n "$want" ] && [ "$want" = "$have" ]; then
      o "virtual key matches the keyHash in config.yaml"
    else
      w "AGW_VIRTUAL_KEY does NOT match the keyHash in gateway/config.yaml."
      w "  fix:  printf '%s' \"\$AGW_VIRTUAL_KEY\" | shasum -a 256"
    fi
  fi
  grep -q "unit: USD" gateway/config.yaml && o "USD budget configured" \
    || w "apiKey policy present but no USD budget"
  # A live tripwire is a demo prop, not something to walk on stage with by
  # accident -- it blocks the NEXT call after it trips.
  if grep -qE "^ *- name: tripwire" gateway/config.yaml; then
    amt=$(grep -A3 "^ *- name: tripwire" gateway/config.yaml | grep -m1 "amount:" | awk '{print $2}')
    if [ "${amt:-0}" -lt 10000 ] 2>/dev/null; then
      w "TRIPWIRE budget is armed at ${amt} tokens -- it WILL block mid-demo"
    else
      o "tripwire budget present, set high (${amt} tokens) -- will not fire"
    fi
  else
    o "tripwire budget is commented out"
  fi
  if grep -q "mode: strict" gateway/config.yaml; then
    o "apiKey mode: strict -- nothing reaches the models without the virtual key"
  else
    w "apiKey mode is not strict -- unkeyed requests are allowed AND unbudgeted"
  fi
fi
[ -n "${GEMINI_API_KEY:-}" ] && o "GEMINI_API_KEY set" || w "GEMINI_API_KEY missing"
[ -n "${OPENAI_API_KEY:-}" ] && o "OPENAI_API_KEY set (failover armed)" || w "no OPENAI_API_KEY -- Director has no fallback"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  who=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user)
  case "$who" in
    200) push=$(curl -s -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/${GITHUB_REPO:-}" | grep -o '"push": *true' || true)
         [ -n "$push" ] && o "GitHub token valid and can write to ${GITHUB_REPO:-?}" \
                        || w "token is VALID but cannot write to ${GITHUB_REPO:-?} -- needs Contents + Pull requests = Read and write" ;;
    401) w "GitHub token REJECTED (401) -- expired, revoked, or mistyped. Regenerate it." ;;
    *)   w "GitHub /user returned $who" ;;
  esac
else
  w "GITHUB_TOKEN missing -- publishing will fail"
fi
say -v '?' 2>/dev/null | grep -qi "en_" && o "English voice available" || w "no English voice for `say`"
say -v '?' 2>/dev/null | grep -qi "ja_JP" && o "Japanese voice available" || w "no Japanese voice (optional)"
curl -sf "http://localhost:${PREVIEW_PORT:-8888}/healthz" | grep -q '"has_frame": *true' && o "viewfinder live with a frame" || w "viewfinder not ready -- logs/viewfinder.log"
command -v ffmpeg >/dev/null && o "ffmpeg present (credits)" || w "no ffmpeg -- credits will fail. brew install ffmpeg"
CAST="${CAST_FILE:-./cast}"    # the file is `cast`, with no extension
n=$(grep -vcE "^[[:space:]]*(#|$)" "$CAST" 2>/dev/null || echo 0)
[ "$n" -gt 0 ] && o "$CAST has $n names" || w "$CAST missing or empty -- no credits"
ls fallback/*.jpg fallback/*.png >/dev/null 2>&1 && o "fallback photos present" || w "no fallback photos in ./fallback -- camera failure = dead demo"
[ -f backup/demo-recording.mp4 ] && o "backup recording present" || w "NO BACKUP RECORDING. Record one."
if [ -n "${GITHUB_REPO:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
  p=$(curl -s -m 15 -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/$GITHUB_REPO/rulesets")
  echo "$p" | grep -q '"id"' && w "rulesets exist on $GITHUB_REPO -- check they don't require approvals (you cannot approve your own PR)" || o "no rulesets blocking self-merge"
fi
echo
echo "  also, by hand:"
echo "    - wifi OFF, run the whole thing once"
echo "    - QR on the slide resolves (scan it on cellular)"
echo "    - screen mirroring set, UI window sized for the back row"
echo
