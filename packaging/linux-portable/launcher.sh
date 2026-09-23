#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export YT_DOWNLOADER_PORTABLE_ROOT="$ROOT"
export YT_DOWNLOADER_PORTABLE=1

if ! command -v python3 >/dev/null 2>&1; then
    printf 'YT-Downloader: python3 is missing.\n' >&2
    exit 1
fi
if ! python3 -c 'import PySide6' >/dev/null 2>&1; then
    printf 'YT-Downloader: PySide6 is missing.\n' >&2
    printf 'This is the portable preview. The final AppImage will bundle its runtime.\n' >&2
    exit 1
fi
exec python3 "$ROOT/app/yt_downloader.py" "$@"
