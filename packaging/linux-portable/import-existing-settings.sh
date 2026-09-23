#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$ROOT/data/config" "$ROOT/data/state"
OLD_CFG="$HOME/.config/yt-downloader/config.json"
OLD_STATE="$HOME/.local/state/yt-downloader"
if [[ -f "$OLD_CFG" ]]; then
    cp -a "$OLD_CFG" "$ROOT/data/config/config.json"
    echo "Skopiowano config.json"
fi
if [[ -d "$OLD_STATE" ]]; then
    cp -a "$OLD_STATE/." "$ROOT/data/state/"
    echo "Skopiowano stan/historię/statystyki"
fi
if [[ ! -f "$OLD_CFG" && ! -d "$OLD_STATE" ]]; then
    echo "Nie znaleziono starej konfiguracji YT-Downloadera."
fi
