#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
ICON_SOURCE='desktop/assets/chat-rescue-icon.png'
ICONSET='build/ChatRescue.iconset'
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  twice=$((size * 2))
  /usr/bin/sips -z "$twice" "$twice" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$ICONSET" -o build/ChatRescue.icns
