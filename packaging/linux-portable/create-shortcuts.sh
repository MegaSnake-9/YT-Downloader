#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$ROOT/YT-Downloader.AppImage"
[[ -f "$TARGET" ]] || TARGET="$ROOT/YT-Downloader"
ICON="$ROOT/app/yt-downloader.png"
MENU_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
MENU_FILE="$MENU_DIR/yt-downloader-portable.desktop"
mkdir -p "$MENU_DIR"

write_desktop() {
    local file="$1"
    cat > "$file" <<EOF
[Desktop Entry]
Type=Application
Name=YT-Downloader
Comment=Portable YouTube downloader
Exec="$TARGET"
Icon=$ICON
Terminal=false
Categories=AudioVideo;Network;
StartupNotify=true
EOF
    chmod +x "$file"
}

write_desktop "$MENU_FILE"

DESKTOP_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
[[ -n "$DESKTOP_DIR" ]] || DESKTOP_DIR="$HOME/Desktop"
if [[ -d "$DESKTOP_DIR" ]]; then
    write_desktop "$DESKTOP_DIR/YT-Downloader.desktop"
fi

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$MENU_DIR" >/dev/null 2>&1 || true
printf 'Utworzono skrót w menu aplikacji'
[[ -d "$DESKTOP_DIR" ]] && printf ' oraz na pulpicie'
printf '.\n'
