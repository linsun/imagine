#!/usr/bin/env bash
cd "$(dirname "$0")/.."
for f in .pids/*; do
  [ -e "$f" ] || continue
  pid=$(cat "$f")
  kill "$pid" 2>/dev/null && echo "stopped $(basename "$f") ($pid)"
  rm -f "$f"
done
