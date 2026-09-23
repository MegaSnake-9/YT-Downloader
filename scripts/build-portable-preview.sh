#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 - <<'PY' "$REPO_ROOT/src/yt_downloader.py"
import re, sys
text=open(sys.argv[1], encoding='utf-8').read()
m=re.search(r'^VERSION\s*=\s*["\']([^"\']+)', text, re.M)
if not m:
    raise SystemExit('VERSION not found')
print(m.group(1))
PY
)"
NAME="YT-Downloader-v${VERSION}-Linux-Portable-Preview"
DIST="$REPO_ROOT/dist"
OUT="$DIST/$NAME"
rm -rf "$OUT"
mkdir -p "$OUT/app" "$OUT/data/config" "$OUT/data/state" "$OUT/data/tools" "$OUT/data/cache"
cp "$REPO_ROOT/src/yt_downloader.py" "$OUT/app/yt_downloader.py"
cp "$REPO_ROOT/assets/yt-downloader.png" "$OUT/app/yt-downloader.png"
cp "$REPO_ROOT/packaging/linux-portable/launcher.sh" "$OUT/YT-Downloader"
cp "$REPO_ROOT/packaging/linux-portable/create-shortcuts.sh" "$OUT/Create shortcuts.sh"
cp "$REPO_ROOT/packaging/linux-portable/remove-shortcuts.sh" "$OUT/Remove shortcuts.sh"
cp "$REPO_ROOT/packaging/linux-portable/import-existing-settings.sh" "$OUT/Import existing settings.sh"
printf '' > "$OUT/data/config/.keep"
printf '' > "$OUT/data/state/.keep"
printf '' > "$OUT/data/tools/.keep"
printf '' > "$OUT/data/cache/.keep"
cat > "$OUT/README.txt" <<README
YT-Downloader v${VERSION} — Linux Portable Preview

Run: ./YT-Downloader

All user data is stored in the data/ directory next to the launcher.
This preview still requires system Python 3, PySide6, yt-dlp and FFmpeg.
The planned AppImage will bundle the runtime while keeping the same external data/ layout.
README
chmod +x "$OUT/YT-Downloader" "$OUT/"*.sh
(
  cd "$DIST"
  rm -f "$NAME.zip"
  zip -qr "$NAME.zip" "$NAME"
)
echo "Built: $DIST/$NAME.zip"
