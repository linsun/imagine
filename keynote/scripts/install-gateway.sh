#!/usr/bin/env bash
# Install a PINNED agentgateway into ./bin, not /usr/local/bin.
#
# Project-local on purpose: the demo then depends on a specific binary you can
# diff, roll back by deleting, and carry to the venue -- not on whatever the
# system happens to have. up.sh prefers ./bin/agentgateway over PATH.
#
#   ./scripts/install-gateway.sh                  # default pinned version
#   ./scripts/install-gateway.sh v1.4.1           # roll back
set -euo pipefail
cd "$(dirname "$0")/.."

VER="${1:-v1.5.0-beta.1}"
mkdir -p bin

case "$(uname -s)" in
  Darwin) OS=darwin ;;
  Linux)  OS=linux ;;
  *) echo "unsupported OS: $(uname -s)"; exit 1 ;;
esac
case "$(uname -m)" in
  arm64|aarch64) ARCH=arm64 ;;
  x86_64|amd64)  ARCH=amd64 ;;
  *) echo "unsupported arch: $(uname -m)"; exit 1 ;;
esac
ASSET="agentgateway-${OS}-${ARCH}"
URL="https://github.com/agentgateway/agentgateway/releases/download/${VER}/${ASSET}"

echo "installing ${VER} (${ASSET}) -> ./bin/agentgateway"
if [ -x bin/agentgateway ]; then
  cp bin/agentgateway "bin/agentgateway.prev" && echo "  kept previous as bin/agentgateway.prev"
fi

tmp=$(mktemp)
if ! curl -fsSL "$URL" -o "$tmp"; then
  echo
  echo "download failed: $URL"
  echo "check the asset name on the release page:"
  echo "  https://github.com/agentgateway/agentgateway/releases/tag/${VER}"
  echo "then either pass a different tag, or use the official installer:"
  echo "  curl -sL https://agentgateway.dev/install | \\"
  echo "    AGENTGATEWAY_INSTALL_DIR=\"\$PWD/bin\" bash -s -- --version ${VER}"
  rm -f "$tmp"; exit 1
fi
mv "$tmp" bin/agentgateway
chmod +x bin/agentgateway

# Sanity-check the download before trusting it -- a 404 lands as an HTML file
# that will fail in confusing ways later.
case "$(file -b bin/agentgateway)" in
  *HTML*|*ASCII*|*text*)
    echo "download was not a binary (probably a 404 page). Check the asset name at"
    echo "  https://github.com/agentgateway/agentgateway/releases/tag/${VER}"
    exit 1 ;;
esac

# macOS: clear quarantine AND ad-hoc sign. On Apple Silicon an unsigned or
# invalidly-signed binary is SIGKILLed on exec -- it just prints "killed",
# which reads like a crash but is Gatekeeper.
if [ "$OS" = darwin ]; then
  xattr -cr bin/agentgateway 2>/dev/null || true
  codesign --force --sign - bin/agentgateway 2>/dev/null \
    || echo "  note: codesign failed; if it dies with 'killed', run ./scripts/fix-macos-binary.sh"
fi

echo
echo -n "  installed: "; ./bin/agentgateway --version 2>&1 | head -2
echo
echo "next:  make down && make up && make verify"
echo "roll back:  ./scripts/install-gateway.sh v1.4.1"
