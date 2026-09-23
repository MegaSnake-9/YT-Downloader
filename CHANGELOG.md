# Changelog

## 0.4.32

- Added the first real x86_64 AppImage build pipeline.
- Added GitHub Actions AppImage artifacts.
- AppImage bundles PySide6/Qt, yt-dlp, FFmpeg/ffprobe and Deno.
- Preserved portable `data/` beside the AppImage.
- Added third-party component notices.

## 0.4.31

- Added Linux portable-preview data layout (`data/config`, `data/state`, `data/tools`, `data/cache`).
- Portable mode no longer stores its configuration/state in the usual XDG user directories.
- Removed the experimental KWin window-position integration; window size persistence remains.
- Added optional shortcut creation/removal helpers for the portable build.
- Added an importer for existing classic Linux settings/state.

Earlier 0.4.x builds were developed iteratively before the public Git repository was created.
