# Changelog

## 0.4.35

- Bundle GLVND/EGL loader libraries required by PySide6/Qt in AppImage.
- Run AppImage runtime self-test using Qt offscreen mode in GitHub Actions.

- Fixed a runtime regression in the portable/AppImage cookie path that caused `NameError: _manual_cookie_enabled is not defined`.
- Restored browser-cookie, manual-cookie and Windows extension cookie helper functions.
- Added a non-GUI AppImage runtime self-test so missing runtime helpers and bundled tools fail CI instead of producing a green broken build.


## 0.4.33

- Centered all QGroupBox titles explicitly so the layout is consistent in AppImage and across Qt desktop styles.

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
