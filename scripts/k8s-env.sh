#!/usr/bin/env bash
# .env -> the `imagine-env` ConfigMap the MCP servers (namespace `mcp`) read
# their settings from.
#
#   scripts/k8s-env.sh             apply it (scripts/k8s-up.sh runs this)
#   scripts/k8s-env.sh --diff      show what would change, apply nothing
#   scripts/k8s-env.sh --restart   apply it, then restart the Deployments that
#                                  read it (a pod reads its env only at start)
#
# Never `kubectl create configmap --from-env-file=.env` by hand: kubectl keeps a
# value exactly as written, so `KEY="a b"` arrives WITH its quotes and
# `KEY=a  # note` WITH its comment. This reads .env the way bash does.
#
# Secrets and the laptop-only URLs are left out: the provider keys and
# GITHUB_TOKEN go in the provider-keys Secret instead.
set -euo pipefail
cd "$(dirname "$0")/.."

CTX=kind-${KIND_CLUSTER:-kind}
NS=mcp
k() { kubectl --context "$CTX" "$@"; }

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
python3 - "$tmp" <<'EOF'
import re, sys
drop = {"GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN",
        "AGW_LLM", "AGW_MCP", "SCOUT_A2A", "DP_A2A", "PREVIEW_BROWSER",
        "PHOTO_INBOX_KEY"}
out = []
for line in open(".env", encoding="utf-8"):
    m = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
    if not m or m.group(1) in drop:
        continue
    # Read the value the way bash does when scripts source .env:
    #   KEY="a b"  -> a b        (quotes are not part of the value)
    #   KEY=a  # x -> a          (a trailing comment is not either)
    #   KEY=       -> ""         (kept: empty means "off", e.g. INTRO_JA=)
    v = m.group(2).strip()
    q = re.match(r"""^(["'])(.*)\1(\s+#.*)?$""", v)
    v = q.group(2) if q else re.split(r"\s+#", v, maxsplit=1)[0].strip()
    out.append(f"{m.group(1)}={v}")
open(sys.argv[1], "w").write("\n".join(out) + "\n")
EOF

render() { k -n "$NS" create configmap imagine-env --from-env-file="$tmp" --dry-run=client -o yaml; }

if [ "${1:-}" = --diff ]; then
  # kubectl diff exits 1 when there are differences; that is not an error here.
  render | k diff -f - || true
  exit 0
fi

k create namespace "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null
render | k apply -f - >/dev/null
echo "   imagine-env: $(grep -c . "$tmp") settings from .env"

if [ "${1:-}" = --restart ]; then
  users=$(k -n "$NS" get deploy -o json | python3 -c '
import json, sys
for d in json.load(sys.stdin)["items"]:
    for c in d["spec"]["template"]["spec"]["containers"]:
        if any((e.get("configMapRef") or {}).get("name") == "imagine-env" for e in c.get("envFrom", [])):
            print(d["metadata"]["name"]); break')
  for d in $users; do k -n "$NS" rollout restart deploy/"$d" >/dev/null; done
  for d in $users; do k -n "$NS" rollout status deploy/"$d" --timeout=180s >/dev/null && echo "   restarted $d"; done
fi
