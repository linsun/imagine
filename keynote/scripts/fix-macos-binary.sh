#!/usr/bin/env bash
# Diagnose and fix a macOS binary that dies with "killed".
#
# On Apple Silicon, macOS SIGKILLs any binary whose code signature is missing
# or invalid -- including a perfectly good binary you downloaded with curl.
# It looks like a crash. It is not; it is Gatekeeper.
#
#   ./scripts/fix-macos-binary.sh                             # ./bin/agentgateway
#   ./scripts/fix-macos-binary.sh /usr/local/bin/agentgateway
set -uo pipefail
BIN="${1:-./bin/agentgateway}"

echo
echo "checking $BIN"
if [ ! -e "$BIN" ]; then echo "  not found"; exit 1; fi

echo
echo "1. is it actually a binary?"
file "$BIN"
sz=$(wc -c < "$BIN" | tr -d ' ')
echo "   size: $sz bytes"
if [ "$sz" -lt 1000000 ]; then
  echo "   ^^ suspiciously small. If \`file\` says HTML or ASCII text, the download"
  echo "      returned a 404 page. Re-download; do not bother signing it."
fi
case "$(file -b "$BIN")" in
  *HTML*|*ASCII*|*text*) echo "   -> NOT a binary. Bad download. Stop here."; exit 1 ;;
esac

echo
echo "2. architecture (yours is $(uname -m))"
lipo -archs "$BIN" 2>/dev/null || echo "   (lipo could not read it)"

echo
echo "3. quarantine attribute"
if xattr -l "$BIN" 2>/dev/null | grep -q quarantine; then
  echo "   PRESENT -- will be cleared"
else
  echo "   none"
fi

echo
echo "4. code signature"
codesign -dv --verbose=2 "$BIN" 2>&1 | sed 's/^/   /' || true

echo
echo "applying fixes..."
sudo xattr -cr "$BIN" 2>/dev/null || xattr -cr "$BIN" 2>/dev/null || true
echo "  cleared extended attributes"
if sudo codesign --force --sign - "$BIN" 2>/dev/null || codesign --force --sign - "$BIN" 2>/dev/null; then
  echo "  ad-hoc signed"
else
  echo "  !! codesign failed -- is Xcode command line tools installed?"
  echo "     xcode-select --install"
fi

echo
echo "5. does it run now?"
if "$BIN" --version 2>&1 | head -3; then
  echo
  echo "  fixed."
else
  echo
  echo "  still failing. Look at the actual kill reason:"
  echo "    log show --predicate 'eventMessage CONTAINS \"agentgateway\"' --last 5m --info | tail -30"
  echo "  and check the asset really matches $(uname -m):"
  echo "    https://github.com/agentgateway/agentgateway/releases/tag/v1.5.0-beta.1"
fi
echo
