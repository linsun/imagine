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
  grep -qE "^ *- name: tripwire" gateway/config.yaml \
    && w "the TRIPWIRE budget is UNCOMMENTED -- calls will 429 once it trips" \
    || o "tripwire budget is commented out"
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
n=$(grep -vcE "^[[:space:]]*(#|$)" cast.txt 2>/dev/null || echo 0); [ "$n" -gt 0 ] && o "cast.txt has $n names" || w "cast.txt empty -- no credits"
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
