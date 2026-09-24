# Changelog

## 0.4.41

- Added Standard/DRC source-audio variants for M4A/AAC and Opus.
- Added DRC-to-standard fallback status when a requested DRC stream is unavailable.
- Marked approximate M4A/Opus bitrates with `~`.
- Added a Variant column to Check formats and made summary prefer non-DRC streams.
- Fresh installs now default to English and use the refined UI geometry/row limits.
- Switched folder selection to the desktop-native directory picker.
- Replaced Arch-specific updating with GitHub Releases/AppImage updating.
- Enlarged the main Add to queue / Queue / History / Log section titles.

## 0.4.40

- Fix `Open default folder` in AppImage by using host `xdg-open`/`gio` with a sanitized subprocess environment.
- Make `Download all` and `Stop` match the larger primary `Add` action and right-side spacing.
- Add portable Linux shortcut controls in Settings for the application menu and desktop, plus shortcut removal.
- Translate Settings `Save` / `Cancel` explicitly instead of relying on bundled Qt translations.
- Rework the non-native folder picker into a left-to-right tile grid that wraps by rows and scrolls vertically.
- Make yt-dlp command preview side-effect free; typing `Echo` no longer creates `E`, `Ec`, `Ech`, `Echo` folders.
- Create the destination directory only when a real download starts.
- Extend the AppImage self-test to guard against destination creation during command preview.

## 0.4.39

- Fix AppImage/KWallet integration at the PyInstaller subprocess boundary.
- Restore `LD_LIBRARY_PATH_ORIG` for yt-dlp and other external subprocesses, as recommended by PyInstaller.
- Remove the AppImage kwallet-query/dbus-send wrappers; host desktop helpers are now used directly.
- Scope bundled FFmpeg libraries to FFmpeg/ffprobe wrappers instead of the whole GUI process.
- Bundle EGL/GL loader libraries inside the PyInstaller runtime directory.
- Extend the AppImage self-test to verify subprocess environment sanitization.


## 0.4.38

- Fix AppImage host-helper wrappers for `kwallet-query` and `dbus-send`.
- Prefer absolute host tool paths and prevent wrappers from resolving to themselves.
- Run host KDE/DBus helpers without AppImage `LD_LIBRARY_PATH` contamination.
- Keep successful downloads successful even when yt-dlp only emits cookie/keyring warnings.

## 0.4.37

- Restored true yt-dlp automatic keyring selection on Linux instead of forcing KWallet 6.
- Added AppImage host-environment wrappers for `kwallet-query` and `dbus-send` so KDE cookie decryption uses host libraries.
- Kept explicit KWallet 6/5/legacy and GNOME Keyring choices for manual override.
- Updated AppImage self-tests for automatic and explicit keyring behavior.

## 0.4.36

- Resolve Linux Chromium cookie keyrings explicitly in portable/AppImage builds (including KWallet 6 on Plasma 6).
- Add KWallet 6 and KWallet 5 choices to Settings.
- Keep format-check errors concise instead of displaying yt-dlp JSON output.
- Improve E06 diagnostics for browser-cookie/keyring failures.

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
