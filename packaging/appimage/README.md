# AppImage packaging

The repository can now build a real x86_64 AppImage with:

```bash
./scripts/build-appimage.sh
```

The build freezes the Python/PySide6 GUI with PyInstaller and bundles separate
executables for:

- yt-dlp
- FFmpeg / ffprobe (LGPL shared build)
- Deno (JavaScript runtime used by yt-dlp for YouTube)

`appimagetool` then converts the assembled AppDir into a type-2 AppImage.

## Portable data

When executed as an AppImage, the runtime provides the `APPIMAGE` environment
variable. YT-Downloader uses the real AppImage path and stores writable data
beside it:

```text
YT-Downloader/
├── YT-Downloader-0.x.x-x86_64.AppImage
└── data/
    ├── config/
    ├── state/
    ├── tools/
    └── cache/
```

Replacing only the `.AppImage` therefore leaves user settings/history intact.
Deleting the entire containing folder removes the application and its portable
data, unless the user separately created desktop/menu shortcuts.

## GitHub Actions

`.github/workflows/appimage.yml` builds the same AppImage on Ubuntu 22.04 and
uploads it as a workflow artifact on relevant pushes to `main` or on a manual
workflow dispatch.

This is still an early Linux-universal build. Test it on multiple distributions
and desktop environments before calling it a stable release.
