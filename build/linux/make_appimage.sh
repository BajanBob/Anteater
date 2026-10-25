#!/usr/bin/env bash
# Wrap the PyInstaller binary in an AppImage.
#
#   bash build/linux/make_appimage.sh dist/anteater-linux-x86_64
#
# Output: dist/Anteater-<arch>.AppImage. Downloads appimagetool on first use.
# Running the AppImage with no arguments opens the dashboard; any arguments are
# passed to anteater (e.g. ./Anteater-x86_64.AppImage selftest --no-radio).

set -euo pipefail

BIN="${1:?usage: make_appimage.sh <path-to-anteater-binary>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARCH="${ARCH:-$(uname -m)}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

APPDIR="$WORK/Anteater.AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

install -m 755 "$BIN" "$APPDIR/usr/bin/anteater"
cp "$ROOT/build/icons/anteater.png" "$APPDIR/anteater.png"
cp "$ROOT/build/icons/anteater.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/anteater.png"

cat > "$APPDIR/anteater.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Anteater
Comment=Passive RF surveillance detection
Exec=anteater
Icon=anteater
Terminal=true
Categories=Network;Security;
DESKTOP

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
if [ "$#" -eq 0 ]; then
  exec "$HERE/usr/bin/anteater" ui
fi
exec "$HERE/usr/bin/anteater" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

TOOL="$WORK/appimagetool"
URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
echo ">> fetching appimagetool"
curl -fsSL "$URL" -o "$TOOL"
chmod +x "$TOOL"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/Anteater-${ARCH}.AppImage"
# --appimage-extract-and-run: works on CI runners without FUSE
ARCH="$ARCH" "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"
echo ">> built $OUT"
