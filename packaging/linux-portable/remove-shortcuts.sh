#!/usr/bin/env bash
set -euo pipefail
MENU_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
rm -f "$MENU_DIR/yt-downloader-portable.desktop"
DESKTOP_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
[[ -n "$DESKTOP_DIR" ]] || DESKTOP_DIR="$HOME/Desktop"
rm -f "$DESKTOP_DIR/YT-Downloader.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$MENU_DIR" >/dev/null 2>&1 || true
printf 'Usunięto skróty YT-Downloadera.\n'
