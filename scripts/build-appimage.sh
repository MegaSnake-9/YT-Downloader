#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="$ROOT/build/appimage"
APPDIR="$BUILD_ROOT/YT-Downloader.AppDir"
PYI_DIST="$BUILD_ROOT/pyinstaller-dist"
PYI_WORK="$BUILD_ROOT/pyinstaller-work"
VENV="$BUILD_ROOT/venv"
DOWNLOADS="$BUILD_ROOT/downloads"
OUT_DIR="$ROOT/dist"

VERSION="${VERSION:-$(python3 - <<'PY'
import tomllib
from pathlib import Path
with Path('pyproject.toml').open('rb') as f:
    print(tomllib.load(f)['project']['version'])
PY
)}"
ARCH="${ARCH:-x86_64}"

if [[ "$ARCH" != "x86_64" ]]; then
    echo "This first AppImage builder currently supports x86_64 only." >&2
    exit 2
fi

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v unzip >/dev/null || { echo "unzip is required" >&2; exit 2; }
command -v tar >/dev/null || { echo "tar is required" >&2; exit 2; }

rm -rf "$BUILD_ROOT"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/512x512/apps" "$DOWNLOADS" "$OUT_DIR"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip wheel
"$VENV/bin/python" -m pip install 'PyInstaller>=6.15,<7' 'PySide6>=6.7,<7' 'certifi>=2024.8.30'

# Freeze the GUI and Qt/PySide6 into a relocatable onedir application.
"$VENV/bin/pyinstaller" \
    --noconfirm \
    --clean \
    --windowed \
    --onedir \
    --name yt-downloader \
    --distpath "$PYI_DIST" \
    --workpath "$PYI_WORK" \
    --specpath "$BUILD_ROOT" \
    --add-data "$ROOT/assets/yt-downloader.png:." \
    --collect-data certifi \
    "$ROOT/src/yt_downloader.py"

cp -a "$PYI_DIST/yt-downloader" "$APPDIR/usr/lib/yt-downloader"

# QtGui from the PySide6 wheel links to the GLVND/EGL loader.  Many desktop
# systems already provide these libraries, but a portable AppImage must not
# assume that.  Bundle the generic loaders while still letting them discover
# the host GPU vendor/driver at runtime.
GL_DIR="$APPDIR/usr/lib/yt-downloader/_internal"
mkdir -p "$GL_DIR"
find_system_lib() {
    local name="$1"
    ldconfig -p | awk -v lib="$name" '$1 == lib && $NF ~ /x86_64-linux-gnu/ { print $NF; exit }'
}
for lib in libEGL.so.1 libGL.so.1 libGLX.so.0 libOpenGL.so.0 libGLdispatch.so.0; do
    src="$(find_system_lib "$lib")"
    [[ -n "$src" && -f "$src" ]] || { echo "Required GL/EGL library not found: $lib" >&2; exit 4; }
    cp -L "$src" "$GL_DIR/$lib"
done

# Official yt-dlp standalone executable. It includes the matching yt-dlp-ejs
# component; Deno below provides the JS runtime required for full YouTube use.
curl --fail --location --retry 3 \
    -o "$APPDIR/usr/bin/yt-dlp" \
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux"
chmod +x "$APPDIR/usr/bin/yt-dlp"

# Deno is yt-dlp's recommended JavaScript runtime and is enabled by default.
curl --fail --location --retry 3 \
    -o "$DOWNLOADS/deno.zip" \
    "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip"
unzip -q "$DOWNLOADS/deno.zip" -d "$DOWNLOADS/deno"
install -m 0755 "$DOWNLOADS/deno/deno" "$APPDIR/usr/bin/deno"

# Bundle a shared LGPL FFmpeg build. Keeping FFmpeg as separate shared binaries
# makes the AppImage self-contained without linking it into the MIT GUI.
FFMPEG_ARCHIVE="$DOWNLOADS/ffmpeg.tar.xz"
curl --fail --location --retry 3 \
    -o "$FFMPEG_ARCHIVE" \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-lgpl-shared.tar.xz"
mkdir -p "$DOWNLOADS/ffmpeg"
tar -xJf "$FFMPEG_ARCHIVE" -C "$DOWNLOADS/ffmpeg"
FFMPEG_ROOT="$(find "$DOWNLOADS/ffmpeg" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[[ -n "$FFMPEG_ROOT" ]] || { echo "Could not locate extracted FFmpeg directory" >&2; exit 3; }
mkdir -p "$APPDIR/usr/lib/ffmpeg/bin" "$APPDIR/usr/lib/ffmpeg/lib"
install -m 0755 "$FFMPEG_ROOT/bin/ffmpeg" "$APPDIR/usr/lib/ffmpeg/bin/ffmpeg"
install -m 0755 "$FFMPEG_ROOT/bin/ffprobe" "$APPDIR/usr/lib/ffmpeg/bin/ffprobe"
cp -a "$FFMPEG_ROOT/lib/." "$APPDIR/usr/lib/ffmpeg/lib/"

# FFmpeg is bundled, but its shared-library path must apply only to FFmpeg.
# Keeping it out of the GUI's global LD_LIBRARY_PATH is essential: yt-dlp
# launches host tools such as kwallet-query/dbus-send for Chromium cookies.
for tool in ffmpeg ffprobe; do
    cat > "$APPDIR/usr/bin/$tool" <<EOF
#!/usr/bin/env bash
set -euo pipefail
APPDIR="\$(cd -- "\$(dirname -- "\${BASH_SOURCE[0]}")/../.." && pwd)"
export LD_LIBRARY_PATH="\$APPDIR/usr/lib/ffmpeg/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "\$APPDIR/usr/lib/ffmpeg/bin/$tool" "\$@"
EOF
    chmod +x "$APPDIR/usr/bin/$tool"
done

# Do not bundle or shadow kwallet-query/dbus-send. They are desktop integration
# helpers and must come from the host distribution. The frozen GUI sanitizes
# LD_LIBRARY_PATH before launching yt-dlp so those host helpers load host libs.

# AppDir metadata and entrypoint.
install -m 0755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -m 0644 "$ROOT/packaging/appimage/yt-downloader.desktop" "$APPDIR/yt-downloader.desktop"
install -m 0644 "$ROOT/packaging/appimage/yt-downloader.desktop" "$APPDIR/usr/share/applications/yt-downloader.desktop"
install -m 0644 "$ROOT/assets/yt-downloader.png" "$APPDIR/yt-downloader.png"
install -m 0644 "$ROOT/assets/icons/yt-downloader-512.png" \
    "$APPDIR/usr/share/icons/hicolor/512x512/apps/yt-downloader.png"

# License/source pointers for third-party binaries bundled in the image.
mkdir -p "$APPDIR/usr/share/doc/yt-downloader"
install -m 0644 "$ROOT/LICENSE" "$APPDIR/usr/share/doc/yt-downloader/LICENSE-YT-Downloader"
install -m 0644 "$ROOT/THIRD_PARTY.md" "$APPDIR/usr/share/doc/yt-downloader/THIRD_PARTY.md"

# Build a real type-2 AppImage with the current official appimagetool.
APPIMAGETOOL="$DOWNLOADS/appimagetool-x86_64.AppImage"
curl --fail --location --retry 3 \
    -o "$APPIMAGETOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x "$APPIMAGETOOL"

OUTPUT="$OUT_DIR/YT-Downloader-${VERSION}-x86_64.AppImage"
rm -f "$OUTPUT"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 VERSION="$VERSION" \
    "$APPIMAGETOOL" "$APPDIR" "$OUTPUT"
chmod +x "$OUTPUT"

printf '\nBuilt: %s\n' "$OUTPUT"
ls -lh "$OUTPUT"
