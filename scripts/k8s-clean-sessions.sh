#!/usr/bin/env bash
# Clear out old kagent session actors, and the templates nothing uses any more.
# kagent does neither: every conversation (A2A contextId) is a session actor,
# pinned to the template it started on, and every new agent image makes a new
# template. Old ones pile up, and a stale one left RUNNING holds a worker.
#
# Deletes, after listing them and asking (-y skips the ask):
#   - SUSPENDED session actors on an OLD template (not the newest for its
#     agent), with their egress policies. With --all, every suspended session
#     actor -- then click "new session" in the studio.
#   - with --running, also RUNNING session actors on an old template: the
#     stale ones that hold a worker ("no worker has room for the actor").
#   - templates that are not the newest for their agent and that no remaining
#     actor uses.
# Never touches the newest template of each agent or the studio, nor a RUNNING
# actor without --running (it lists the stale ones instead).
#
#   scripts/k8s-clean-sessions.sh             # atespace default
#   scripts/k8s-clean-sessions.sh --running   # and the stale running ones
#   scripts/k8s-clean-sessions.sh --all -y
set -uo pipefail
CTX=kind-${KIND_CLUSTER:-kind}
ATE_BIN=${ATE_BIN:-kubectl-ate}
ate() { "$ATE_BIN" --context "$CTX" "$@" 2> >(grep -v '^Warning: no --token-file' >&2); }

ATESPACE=default
YES=
ALL=
RUNNING=
for a in "$@"; do
  case "$a" in
    -y|--yes) YES=1 ;;
    --all) ALL=1 ;;
    --running) RUNNING=1 ;;
    -*) echo "usage: $0 [atespace] [--all] [--running] [-y]"; exit 2 ;;
    *) ATESPACE=$a ;;
  esac
done

actors=$(ate get actors -a "$ATESPACE" -o json) || { echo "could not list actors in $ATESPACE"; exit 1; }
templates=$(ate get actor-templates -a "$ATESPACE" -o json) || { echo "could not list templates in $ATESPACE"; exit 1; }

# One line per thing to do: "actor <name> <template>", "template <name>",
# or "running <name> <template>" (stale, reported only).
plan=$(ALL="$ALL" RUNNING="$RUNNING" python3 -c '
import json, os, sys
actors = json.loads(sys.argv[1]).get("actors") or []
templates = json.loads(sys.argv[2]).get("actorTemplates") or []

# kagent names templates <agent>-<hash>; the newest per agent is current.
def agent(name): return name.rsplit("-", 1)[0] if "-" in name else name
newest = {}
for t in templates:
    m = t["metadata"]
    a = agent(m["name"])
    if a not in newest or m.get("createTime", "") > newest[a][0]:
        newest[a] = (m.get("createTime", ""), m["name"])
current = {n for _, n in newest.values()}

keep_templates = set(current)
for x in actors:
    name = x["metadata"]["name"]
    tmpl = (x.get("actorTemplate") or {}).get("name", "")
    state = (x.get("status") or {}).get("state", "")
    old = tmpl not in current
    if not name.startswith("session-"):
        keep_templates.add(tmpl)
    elif state == "ACTOR_STATE_SUSPENDED" and (old or os.environ["ALL"]):
        print("actor", name, tmpl)
    elif state == "ACTOR_STATE_RUNNING" and old and os.environ["RUNNING"]:
        print("actor", name, tmpl, "running")
    else:
        keep_templates.add(tmpl)
        if old and state == "ACTOR_STATE_RUNNING":
            print("running", name, tmpl)
for t in templates:
    n = t["metadata"]["name"]
    if n not in keep_templates:
        print("template", n)
' "$actors" "$templates")

del_actors=$(awk '$1=="actor"' <<< "$plan")
del_templates=$(awk '$1=="template"{print $2}' <<< "$plan")
stale=$(awk '$1=="running"' <<< "$plan")
na=$(grep -c . <<< "$del_actors"); nt=$(grep -c . <<< "$del_templates")

if [ -n "$stale" ]; then
  echo "Still RUNNING on an old template (left alone; each holds a worker; --running deletes them):"
  while read -r _ name tmpl; do
    echo "  $name  ($tmpl)"
    echo "    kubectl ate --context $CTX suspend actor $name -a $ATESPACE"
  done <<< "$stale"
  echo
fi

if [ "$na" -eq 0 ] && [ "$nt" -eq 0 ]; then
  echo "atespace $ATESPACE: no old session actors or templates to delete"; exit 0
fi
[ "$na" -gt 0 ] && { echo "Session actors:"; awk '{print "  " $2 "  (" $3 ")" ($4 ? "  RUNNING" : "")}' <<< "$del_actors"; }
[ "$nt" -gt 0 ] && { echo "Templates nothing uses (not the newest for their agent):"; sed 's/^/  /' <<< "$del_templates"; }
echo
if [ -z "$YES" ]; then
  read -r -p "delete $na actors and $nt templates from atespace $ATESPACE? [y/N] " ok
  [[ "$ok" =~ ^[Yy] ]] || { echo "nothing deleted"; exit 0; }
fi

failed=0
if [ "$na" -gt 0 ]; then
  while read -r _ name _ running; do
    # Suspend a running one first, so it lets go of its worker cleanly.
    [ -n "$running" ] && ate suspend actor "$name" -a "$ATESPACE" >/dev/null 2>&1 \
      && echo "suspended actor $name"
    # The egress policy belongs to the actor; most actors have none.
    if out=$(ate delete egress-policy "$name" -a "$ATESPACE" 2>&1); then
      echo "deleted egress policy $name"
    elif ! grep -qi "not found" <<< "$out"; then
      echo "FAILED  egress policy $name: $(tail -1 <<< "$out")"; failed=$((failed + 1))
    fi
    if ate delete actor "$name" -a "$ATESPACE" >/dev/null 2>&1; then
      echo "deleted actor $name"
    else
      echo "FAILED  actor $name"; failed=$((failed + 1))
    fi
  done <<< "$del_actors"
fi
# Then the templates: the server removes each one's golden actor and snapshot.
if [ "$nt" -gt 0 ]; then
  while read -r name; do
    if ate delete actor-template "$name" -a "$ATESPACE" >/dev/null 2>&1; then
      echo "deleted template $name"
    else
      echo "FAILED  template $name"; failed=$((failed + 1))
    fi
  done <<< "$del_templates"
fi
[ -n "$ALL" ] && [ "$na" -gt 0 ] && echo "Click \"new session\" in the studio: its conversation's actors may be gone."
echo "atespace $ATESPACE: $na actors, $nt templates; $failed failures"
[ "$failed" -eq 0 ]
