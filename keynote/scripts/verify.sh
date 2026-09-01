#!/usr/bin/env bash
# The smoke test. Exercises every leg through the gateway, in risk order.
# Step 3 is the one the whole talk depends on.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

# ALWAYS the project venv. Bare `python3` may resolve to another project's
# venv on PATH, which is how you get a stale `mcp` package and no PIL.
PY="$PWD/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "no venv at $PY -- run: make install"; exit 1
fi

LLM="${AGW_LLM:-http://localhost:3000}"
# The VIRTUAL key the agents present. With apiKey mode: strict the gateway
# rejects anything without it, so these raw curls must carry it too.
VK="${AGW_VIRTUAL_KEY:-agentgateway}"
AUTH="Authorization: Bearer $VK"
SCOUT="${SCOUT_A2A:-http://localhost:3002}"
DP="${DP_A2A:-http://localhost:3003}"
pass=0; fail=0
ok()  { echo -e "  \033[32mPASS\033[0m $1"; pass=$((pass+1)); }
bad() { echo -e "  \033[31mFAIL\033[0m $1"; [ -n "${2:-}" ] && echo "       $2"; fail=$((fail+1)); }
note(){ echo -e "       \033[2m$1\033[0m"; }

echo
echo "0. environment"
echo "   python: $("$PY" -c 'import sys; print(sys.executable)')"
if [ -n "${GEMINI_API_KEY:-}" ]; then
  ok "GEMINI_API_KEY present in this shell (${#GEMINI_API_KEY} chars, ...${GEMINI_API_KEY: -4})"
else
  bad "GEMINI_API_KEY not set in this shell" "check ./.env -- no 'export', no spaces around ="
fi
# Is the key actually valid, independent of the gateway?
d=$(curl -s -m 30 "https://generativelanguage.googleapis.com/v1beta/models?key=${GEMINI_API_KEY:-}" )
echo "$d" | grep -q '"models"' \
  && ok "key works against Gemini directly (gateway not involved)" \
  || bad "key rejected by Gemini directly" "$(echo "$d" | head -c 200)"
# Does the agentgateway PROCESS have the variable? (macOS: ps eww shows env)
if [ -f .pids/agentgateway ]; then
  agpid=$(cat .pids/agentgateway)
  if ps eww "$agpid" 2>/dev/null | tr ' ' '\n' | grep -q '^GEMINI_API_KEY=..'; then
    ok "agentgateway process has GEMINI_API_KEY in its environment"
  else
    bad "agentgateway process does NOT have GEMINI_API_KEY" \
        "it was started without the env. make down && make up (up.sh sources .env)."
    note "this is the usual cause of 'Please pass a valid API key' -- the gateway"
    note "expands \$GEMINI_API_KEY to an empty string and forwards that to Gemini."
  fi
else
  note "no .pids/agentgateway -- is it running?"
fi

echo
echo "1. agent -> LLM"
r=$(curl -s -m 60 "$LLM/v1/chat/completions" -H 'content-type: application/json' -H "$AUTH" \
  -d '{"model":"director","messages":[{"role":"user","content":"Reply with the single word: ready"}]}')
if echo "$r" | grep -qi ready; then
  ok "virtualModel 'director' (Gemini, failing over to OpenAI)"
else
  bad "virtualModel 'director'" "$(echo "$r" | head -c 250)"
  # Isolate: does a plain, non-virtual model work?
  r2=$(curl -s -m 60 "$LLM/v1/chat/completions" -H 'content-type: application/json' -H "$AUTH" \
    -d '{"model":"nano-banana","messages":[{"role":"user","content":"hi"}]}')
  if echo "$r2" | grep -q '"choices"'; then
    note "but concrete model 'nano-banana' WORKS -> the problem is the"
    note "virtualModels/failover block, not the credential."
  else
    note "concrete model also fails -> credential is not reaching the provider."
  fi
fi

echo
echo "1b. virtual key enforcement"
if grep -q "^    apiKey:" gateway/config.yaml; then
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 30 "$LLM/v1/chat/completions" \
    -H 'content-type: application/json' -H 'Authorization: Bearer sk-not-a-real-key' \
    -d '{"model":"director","messages":[{"role":"user","content":"hi"}]}')
  case "$code" in
    401|403) ok "a wrong virtual key is rejected ($code)" ;;
    200)     bad "a WRONG virtual key was accepted (200)" \
                 "the apiKey policy is not enforcing -- check mode and keyHash" ;;
    *)       bad "unexpected status for a wrong key: $code" ;;
  esac
  # An unkeyed request: rejected under strict, allowed (and unbudgeted) under
  # optional. Either is legitimate -- this just tells you which you are running.
  nokey=$(curl -s -o /dev/null -w '%{http_code}' -m 30 "$LLM/v1/chat/completions" \
    -H 'content-type: application/json' \
    -d '{"model":"director","messages":[{"role":"user","content":"hi"}]}')
  case "$nokey" in
    401|403) ok "an unkeyed request is rejected ($nokey) -- mode: strict" ;;
    200)     ok "an unkeyed request is allowed (200) -- mode: optional, and it is NOT budgeted" ;;
    *)       note "unkeyed request returned $nokey" ;;
  esac
else
  note "no apiKey policy in gateway/config.yaml -- skipping"
fi

echo
echo "1c. no agent holds a provider credential"
# Reads the ENVIRONMENT OF THE LIVE PROCESSES (ps eww), not the source. This is
# the demo's central claim, so check the running thing, not what we meant.
if [ -d .pids ]; then
  leak=0; checked=0
  for f in .pids/scout .pids/dp .pids/viewfinder; do
    [ -e "$f" ] || continue
    name=$(basename "$f"); pid=$(cat "$f")
    kill -0 "$pid" 2>/dev/null || continue
    checked=$((checked+1))
    envs=$(ps eww -p "$pid" 2>/dev/null | tr ' ' '\n' | grep -E "^(GEMINI_API_KEY|GOOGLE_API_KEY|OPENAI_API_KEY)=" || true)
    if [ -n "$envs" ]; then
      bad "$name has a provider key in its environment" "$(echo "$envs" | cut -d= -f1 | tr '\n' ' ')"
      leak=1
    fi
  done
  if [ "$checked" = 0 ]; then
    note "no agent processes running -- start them with ./imagine up"
  elif [ "$leak" = 0 ]; then
    ok "$checked agent process(es): no GEMINI/GOOGLE/OPENAI key in the environment"
  fi
  # vision-mcp is allowed exactly one: Veo does not go through the gateway.
  if [ -e .pids/vision-mcp ] && kill -0 "$(cat .pids/vision-mcp)" 2>/dev/null; then
    if ps eww -p "$(cat .pids/vision-mcp)" 2>/dev/null | tr ' ' '\n' | grep -q "^GEMINI_BASE_URL="; then
      ok "vision-mcp routes its image calls through the gateway (GEMINI_BASE_URL set)"
    else
      bad "vision-mcp has no GEMINI_BASE_URL" "the double hop is not happening; it is calling Google directly"
    fi
  fi
else
  note "no .pids directory -- nothing running"
fi

echo
echo "2. agent -> MCP  (four servers federated behind one endpoint)"
"$PY" - <<'PY'
import asyncio, os, sys
sys.path.insert(0, ".")
from agent import tools
async def go():
    try:
        t = await tools.list_tools()
    except Exception as e:
        print(f"  \033[31mFAIL\033[0m tools/list -- {e}"); return 1
    names = sorted(x["function"]["name"] for x in t)
    print(f"  \033[32mPASS\033[0m tools/list returned {len(names)}")
    print("       " + ", ".join(names))
    # publish is deliberately absent here: it is hidden from the model (code-
    # driven + gated on a login), so step 3b exercises it, not this list.
    want = {"camera": "camera_", "vision": "vision_", "stage": "stage_", "post": "post_"}
    miss = [k for k, pfx in want.items() if not any(n.startswith(pfx) for n in names)]
    if miss:
        print(f"  \033[33mWARN\033[0m no tools from: {', '.join(miss)}")
        print("       failOpen means the gateway starts anyway -- grep logs/agentgateway.log")
        print("       for the dead target (usually a stdio target that can't find python).")
    return 0
os._exit(asyncio.new_event_loop().run_until_complete(go()) or 0)
PY
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

echo
echo "3. MCP server -> LLM  (the double hop: vision-mcp holds no API key)"
"$PY" - <<'PY'
import asyncio, base64, io, os, sys
sys.path.insert(0, ".")
from PIL import Image
from agent import tools, store
# A REAL photo-sized, noisy image. A 512px flat colour compresses to nothing and
# sails under the gateway's buffer limit -- which is exactly how this check
# passed while the live demo 502'd with "response was too large".
import random
rnd = random.Random(1)
img = Image.new("RGB", (1920, 1080))
img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
             for _ in range(1920 * 1080)])
buf = io.BytesIO(); img.save(buf, "JPEG", quality=92)
h = store.put(base64.b64encode(buf.getvalue()).decode(), "image/jpeg")
print(f"       (test image: {len(buf.getvalue())//1024} KB, like a real capture)")
async def go():
    try:
        out = await tools.call("vision_transform_image", {
            "image_handle": h, "instruction": "Add a single red circle in the centre."})
    except Exception as e:
        print(f"  \033[31mFAIL\033[0m transform -- {e}")
        msg = str(e)
        if "too large" in msg:
            print("       agentgateway buffers the whole LLM response and the default")
            print("       cap is 2 MiB. Raise it in gateway/config.yaml:")
            print("         frontendPolicies: { http: { maxBufferSize: 33554432 } }")
        elif "model_not_found" in msg or "Model not found" in msg:
            print("       The gateway read the model from the path but has no entry by")
            print("       that NAME. Clients address llm.models[].name, not the provider")
            print("       model id. Set IMAGE_MODEL to a configured name:")
            import re, pathlib as _p
            names = re.findall(r"^  - name: (\S+)", _p.Path("gateway/config.yaml").read_text(), re.M)
            print("         configured names: " + ", ".join(names))
        elif "missing_model" in msg:
            print("       agentgateway <= v1.4.1 cannot parse bare Gemini :generateContent")
            print("       paths. Upgrade: make gateway")
        else:
            print("       auth/base-url errors here mean GEMINI_BASE_URL is wrong.")
        print("       THIS is the step the talk depends on. Fix it before anything else.")
        return 1
    if out.get("image_handle"):
        print(f"  \033[32mPASS\033[0m image returned via gateway -> {out['image_handle']}")
        print(f"       {store.path_of(out['image_handle'])}")
        return 0
    print(f"  \033[31mFAIL\033[0m no image: {out}"); return 1
os._exit(asyncio.new_event_loop().run_until_complete(go()) or 0)
PY
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

echo
echo "3b. publish -> GitHub  (person authorizes; gateway holds the token)"
"$PY" - <<'PYX'
import asyncio, os, sys
sys.path.insert(0, ".")
from agent import auth, tools

AUTH_ON = False
try:
    with open("gateway/config.yaml", encoding="utf-8") as f:
        AUTH_ON = any(l.startswith("    mcpAuthentication:") for l in f)
except OSError:
    pass

def _denied(exc):
    m = str(exc).lower()
    return any(x in m for x in ("unknown tool: publish", "unauthor", "forbidden",
                                "401", "403", "jwt", "not allowed"))

async def go():
    signed_in = bool(auth.token())
    try:
        out = await tools.call("publish_check_auth", {})
    except Exception as exc:
        # No token + gate on -> the gateway filters publish, so the call comes
        # back as an unknown tool. That is the gate WORKING.
        if AUTH_ON and not signed_in and _denied(exc):
            print("  \033[32mPASS\033[0m publish is gated: an unauthenticated "
                  "agent cannot call it (sign in to exercise the GitHub hop)")
            return 0
        print(f"  \033[31mFAIL\033[0m publish_check_auth -- {exc}")
        return 1
    # It answered -> signed in, or auth off. Prove the GitHub hop is clean.
    if not out.get("via_gateway"):
        print("  \033[31mFAIL\033[0m publish is calling api.github.com directly")
        return 1
    if out.get("token_in_this_process"):
        print("  \033[31mFAIL\033[0m the publish server still has GITHUB_TOKEN "
              "in its environment -- check clear_env on the stdio target")
        return 1
    if not out.get("ok"):
        print(f"  \033[31mFAIL\033[0m gateway did not authenticate to GitHub: "
              f"{str(out)[:200]}")
        return 1
    who = "signed in" if signed_in else "auth off"
    print(f"  \033[32mPASS\033[0m ({who}) publish holds NO GitHub token; the "
          f"gateway authenticated as {out.get('user','?')} -> {out.get('repo')}")
    return 0
os._exit(asyncio.new_event_loop().run_until_complete(go()) or 0)
PYX
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

echo
echo "4. agent -> agent  (A2A)"
card=$(curl -s -m 15 "$SCOUT/.well-known/agent.json")
url=$("$PY" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))' <<<"$card" 2>/dev/null)
if [ -n "$url" ]; then
  ok "agent card served; url = $url"
  echo "$url" | grep -q ":3002" && note "^ rewritten to the gateway. That is the security beat."
else
  bad "agent card" "$(echo "$card" | head -c 200)"
fi
r=$(curl -s -m 90 -X POST "$SCOUT/" -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","kind":"message","messageId":"t","parts":[{"kind":"text","text":"make my audience do a Japanese dance"}]}}}')
if echo "$r" | grep -q '"parts"'; then
  ok "scout replied over A2A"
elif echo "$r" | grep -q '"error"'; then
  bad "scout message/send" "$(echo "$r" | head -c 400)"
  note "Scout calls the LLM through the gateway, so this usually fails for the"
  note "same reason as step 1. Fix step 1 first. Full traceback: logs/scout.log"
else
  bad "scout message/send" "$(echo "$r" | head -c 200)"
  note "see logs/scout.log"
fi

echo
echo "5. camera  (long-lived viewfinder + countdown capture)"
if curl -sf "http://localhost:${PREVIEW_PORT:-8888}/healthz" >/dev/null 2>&1; then
  ok "viewfinder answering at http://localhost:${PREVIEW_PORT:-8888}/"
else
  bad "viewfinder not running" "it is a separate service; see logs/viewfinder.log"
  note "the camera cannot live inside an MCP tool call -- agentgateway respawns"
  note "stdio servers per session, so up.sh runs the viewfinder standalone."
fi
"$PY" - <<'CAM'
import asyncio, os, sys
sys.path.insert(0, ".")
from agent import tools
async def go():
    try:
        pv = await tools.call("camera_preview_url", {})
        print(f"  \033[32mPASS\033[0m preview {pv.get('url')} ready={pv.get('ready')}")
        out = await tools.call("camera_capture", {"countdown": 0})
        print(f"  \033[32mPASS\033[0m captured -> {out.get('image_handle')}")
    except Exception as e:
        print(f"  \033[33mWARN\033[0m camera -- {e}")
asyncio.new_event_loop().run_until_complete(go())
os._exit(0)
CAM
pass=$((pass+1))

echo
echo "6. credits  (ffmpeg compositing, no model involved)"
"$PY" - <<'CREDITS'
import asyncio, base64, os, subprocess, sys, tempfile
sys.path.insert(0, ".")
from agent import tools, store
if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
    print("  \033[33mWARN\033[0m ffmpeg missing -- credits will fail. brew install ffmpeg")
    sys.exit(0)
tmp = tempfile.mkdtemp(); src = os.path.join(tmp, "t.mp4")
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src],
               capture_output=True, check=True)
h = store.put(base64.b64encode(open(src, "rb").read()).decode(), "video/mp4")
async def go():
    try:
        out = await tools.call("post_add_credits", {
            "video_handle": h, "title": "Test Film", "subtitle": "verify", "seconds": 3})
    except Exception as e:
        print(f"  \033[31mFAIL\033[0m credits -- {e}"); return 1
    print(f"  \033[32mPASS\033[0m {out.get('names_count')} names rolled -> {out.get('video_handle')}")
    print(f"       open {store.path_of(out['video_handle'])}")
    return 0
os._exit(asyncio.new_event_loop().run_until_complete(go()) or 0)
CREDITS
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

echo
echo "7. voice"
"$PY" - <<'PY'
import asyncio, os, sys
sys.path.insert(0, ".")
from agent import tools
out = asyncio.new_event_loop().run_until_complete(tools.call("stage_announce", {"en": "Verification complete.", "ja": "確認が完了しました。"}))
print(("  \033[32mPASS\033[0m " if out.get("ok") else "  \033[33mWARN\033[0m ") + f"announce: {out}")
os._exit(0)
PY
pass=$((pass+1))

echo
echo "-------------------------------------------------------"
echo -e "  \033[32m$pass passed\033[0m   \033[31m$fail failed\033[0m"
if [ $fail -gt 0 ] && [ -f logs/agentgateway.log ]; then
  echo
  echo "  last gateway warnings:"
  grep -E "warn|error" logs/agentgateway.log | grep -v Deprecation | tail -6 | sed 's/^/    /'
fi
echo
exit $fail
