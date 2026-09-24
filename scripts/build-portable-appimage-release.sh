#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="${1:-$REPO_ROOT/dist}"

APPIMAGE="$(find "$DIST" -maxdepth 1 -type f -name 'YT-Downloader-*-x86_64.AppImage' -print -quit)"
if [[ -z "$APPIMAGE" ]]; then
  echo "No YT-Downloader x86_64 AppImage found in: $DIST" >&2
  exit 1
fi

APPIMAGE_NAME="$(basename "$APPIMAGE")"
VERSION="$(python3 - <<'PY' "$REPO_ROOT/src/yt_downloader.py"
import re, sys
text = open(sys.argv[1], encoding='utf-8').read()
m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)', text, re.M)
if not m:
    raise SystemExit('VERSION not found')
print(m.group(1))
PY
)"

PACKAGE_NAME="YT-Downloader-${VERSION}-Linux-x86_64-portable"
STAGE_PARENT="$DIST/.portable-release"
STAGE="$STAGE_PARENT/YT-Downloader"
ZIP_PATH="$DIST/$PACKAGE_NAME.zip"
CHECKSUM_PATH="$ZIP_PATH.sha256"

rm -rf "$STAGE_PARENT"
mkdir -p \
  "$STAGE/data/config" \
  "$STAGE/data/state" \
  "$STAGE/data/tools" \
  "$STAGE/data/cache/cookies" \
  "$STAGE/data/integration"

cp -p "$APPIMAGE" "$STAGE/$APPIMAGE_NAME"
chmod +x "$STAGE/$APPIMAGE_NAME"

cat > "$STAGE/README.txt" <<README
YT-Downloader ${VERSION} — Linux x86_64 portable

1. Run: ${APPIMAGE_NAME}
2. YT-Downloader keeps its writable data in the neighboring data/ directory.
3. Optional desktop/application-menu shortcuts can be created from Settings.
4. In-app updates replace the AppImage while preserving data/.
5. To remove the portable app, remove this YT-Downloader folder. If you created
   shortcuts, remove them from Settings first.

If your desktop does not launch AppImages directly, make the AppImage executable
and run it from a terminal:

  chmod +x ${APPIMAGE_NAME}
  ./${APPIMAGE_NAME}

Project: https://github.com/MegaSnake-9/YT-Downloader
README

# Keep the intended portable directories visible even in tools that discard
# empty directories while repacking the archive later.
printf '' > "$STAGE/data/config/.keep"
printf '' > "$STAGE/data/state/.keep"
printf '' > "$STAGE/data/tools/.keep"
printf '' > "$STAGE/data/cache/.keep"
printf '' > "$STAGE/data/cache/cookies/.keep"
printf '' > "$STAGE/data/integration/.keep"

rm -f "$ZIP_PATH" "$CHECKSUM_PATH"
(
  cd "$STAGE_PARENT"
  zip -qr "$ZIP_PATH" YT-Downloader
)
(
  cd "$DIST"
  sha256sum "$(basename "$ZIP_PATH")" > "$(basename "$CHECKSUM_PATH")"
)

rm -rf "$STAGE_PARENT"
echo "Built: $ZIP_PATH"
echo "Checksum: $CHECKSUM_PATH"
