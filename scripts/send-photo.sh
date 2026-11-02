#!/usr/bin/env bash
# Send a photo from this Mac to the photo inbox -- the same request the phone's
# Shortcut makes. The Director picks it up on "use my latest photo".
#
#   scripts/send-photo.sh                 newest camera/drone photo in $PHOTO_DIR
#   scripts/send-photo.sh path/to.jpg     that file
#   scripts/send-photo.sh --watch         send each new photo that lands in
#                                         $PHOTO_DIR from now on (e.g. AirDrop)
#
# Only files NAMED like camera or drone photos are picked automatically
# (IMG_1234.HEIC, DJI_0042.JPG, dji_fly_..., PXL_...), so a screenshot, a
# diagram or a scanned document sitting in Downloads is never sent by
# accident. Name a file explicitly to send anything else.
#
# Env: PHOTO_DIR (default ~/Downloads), PHOTO_MATCH (extended regex, case-
#      insensitive), INBOX_URL (default http://localhost:3005/upload),
#      PHOTO_INBOX_KEY (default sk-imagine-photos, as in .env).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

PHOTO_DIR=${PHOTO_DIR:-$HOME/Downloads}
PHOTO_MATCH=${PHOTO_MATCH:-'^(IMG_|DJI_|PXL_).*|^dji_.*_photo.*'}
INBOX_URL=${INBOX_URL:-http://localhost:3005/upload}
PHOTO_INBOX_KEY=${PHOTO_INBOX_KEY:-sk-imagine-photos}

is_photo() {  # name
  echo "$1" | grep -qiE '\.(jpe?g|heic|heif|png|dng)$' &&
    echo "$1" | grep -qiE "$PHOTO_MATCH"
}

newest() {
  ls -t "$PHOTO_DIR" 2>/dev/null | while read -r f; do
    is_photo "$f" && { echo "$PHOTO_DIR/$f"; break; }
  done
}

send() {  # path
  local f=$1 type
  case "$(echo "${f##*.}" | tr '[:upper:]' '[:lower:]')" in
    heic|heif) type=image/heic ;; png) type=image/png ;; *) type=image/jpeg ;;
  esac
  echo "sending $(basename "$f") ($(( $(stat -f %z "$f") / 1024 )) KB) -> $INBOX_URL"
  curl -sS -f -X POST "$INBOX_URL" \
    -H "Authorization: Bearer $PHOTO_INBOX_KEY" \
    -H "Content-Type: $type" \
    --data-binary @"$f"
  echo
}

if [ "${1:-}" = "--watch" ]; then
  echo "watching $PHOTO_DIR for new photos (ctrl-c to stop)"
  touch "${TMPDIR:-/tmp}/send-photo.since"
  while true; do
    # New since the last pass; wait until the file stops growing (AirDrop
    # writes it in pieces) before sending.
    find "$PHOTO_DIR" -maxdepth 1 -type f -newer "${TMPDIR:-/tmp}/send-photo.since" 2>/dev/null |
      while read -r f; do
        is_photo "$(basename "$f")" || continue
        s1=$(stat -f %z "$f"); sleep 1; s2=$(stat -f %z "$f")
        [ "$s1" = "$s2" ] && send "$f" || true
      done
    touch "${TMPDIR:-/tmp}/send-photo.since"
    sleep 2
  done
fi

f=${1:-$(newest)}
[ -n "$f" ] || { echo "no camera/drone photo in $PHOTO_DIR (name one explicitly)"; exit 1; }
[ -f "$f" ] || { echo "no such file: $f"; exit 1; }
send "$f"
