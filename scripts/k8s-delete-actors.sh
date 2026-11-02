#!/usr/bin/env bash
# Empty an atespace (default: imagine, the old namespace's): every actor,
# whatever its state, with its egress policy, then every actor template.
# Lists them and asks first; -y skips the ask.
#
#   scripts/k8s-delete-actors.sh              # atespace imagine
#   scripts/k8s-delete-actors.sh default -y   # atespace default, no prompt
#   scripts/k8s-delete-actors.sh --actors-only
#
# Delete the kagent Agents in that namespace first, or kagent puts new
# templates back, and starts a new session actor per conversation.
set -uo pipefail
CTX=kind-${KIND_CLUSTER:-kind}
# The kubectl-ate on PATH, unless ATE_BIN says otherwise: it must match the
# cluster's Substrate version, or listing fails with "Unimplemented".
ATE_BIN=${ATE_BIN:-kubectl-ate}
ate() { "$ATE_BIN" --context "$CTX" "$@"; }

ATESPACE=default
YES=
TEMPLATES=1
for a in "$@"; do
  case "$a" in
    -y|--yes) YES=1 ;;
    --actors-only) TEMPLATES= ;;
    -*) echo "usage: $0 [atespace] [-y] [--actors-only]"; exit 2 ;;
    *) ATESPACE=$a ;;
  esac
done

# names <actors|actor-templates>: one name per line.
names() {
  local json
  json=$(ate get "$1" -a "$ATESPACE" -o json 2>/dev/null) || {
    echo "could not list $1 in atespace $ATESPACE:" >&2; ate get "$1" -a "$ATESPACE" >&2; return 1; }
  printf '%s' "$json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for x in d.get("actors") or d.get("actorTemplates") or []:
    print(x["metadata"]["name"])'
}
count() { [ -z "$1" ] && echo 0 || printf '%s\n' "$1" | wc -l | tr -d ' '; }

actors=$(names actors) || exit 1
templates=
[ -n "$TEMPLATES" ] && { templates=$(names actor-templates) || exit 1; }
na=$(count "$actors"); nt=$(count "$templates")

if [ "$na" -eq 0 ] && [ "$nt" -eq 0 ]; then
  echo "atespace $ATESPACE is already empty"; exit 0
fi

[ "$na" -gt 0 ] && { ate get actors -a "$ATESPACE" 2>/dev/null; echo; }
[ "$nt" -gt 0 ] && { ate get actor-templates -a "$ATESPACE" 2>/dev/null; echo; }
what="$na actors (and their egress policies)"
[ -n "$TEMPLATES" ] && what="$what and $nt actor templates"
if [ -z "$YES" ]; then
  read -r -p "delete $what from atespace $ATESPACE? [y/N] " ok
  [[ "$ok" =~ ^[Yy] ]] || { echo "nothing deleted"; exit 0; }
fi

failed=0
policies=0
# The egress policy first: it belongs to the actor, and most actors have none.
if [ "$na" -gt 0 ]; then
  while read -r name; do
    if out=$(ate delete egress-policy "$name" -a "$ATESPACE" 2>&1); then
      echo "deleted egress policy $name"; policies=$((policies + 1))
    elif ! grep -qi "not found" <<< "$out"; then
      echo "FAILED  egress policy $name: $(grep -v '^Warning' <<< "$out" | tail -1)"
      failed=$((failed + 1))
    fi
    if ate delete actor "$name" -a "$ATESPACE" --any-state >/dev/null 2>&1; then
      echo "deleted actor $name"
    else
      echo "FAILED  actor $name"; failed=$((failed + 1))
    fi
  done <<< "$actors"
fi

# Then the templates: the server removes each one's golden actor and snapshot.
if [ "$nt" -gt 0 ]; then
  while read -r name; do
    if ate delete actor-template "$name" -a "$ATESPACE" >/dev/null 2>&1; then
      echo "deleted template $name"
    else
      echo "FAILED  template $name"; failed=$((failed + 1))
    fi
  done <<< "$templates"
fi

echo "atespace $ATESPACE: $na actors, $policies egress policies, $nt templates; $failed failures"
[ "$failed" -eq 0 ]
