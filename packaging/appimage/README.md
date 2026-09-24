# AppImage packaging

The repository builds a self-contained **x86_64 Linux AppImage** with:

```bash
bash ./scripts/build-appimage.sh
```

The build freezes the Python/PySide6 GUI with PyInstaller and bundles separate executables for:

- yt-dlp
- FFmpeg / ffprobe (`lgpl-shared` build)
- Deno (JavaScript runtime used by yt-dlp for YouTube)

It also bundles the CA certificate data required by the in-app GitHub updater and the generic GL/EGL loader libraries needed by the frozen Qt runtime.

`appimagetool` converts the assembled AppDir into a type-2 AppImage.

## Portable data

When executed as an AppImage, the runtime provides the `APPIMAGE` environment variable. YT-Downloader resolves the real AppImage path and stores writable data beside it:

```text
YT-Downloader/
├── YT-Downloader-0.x.x-x86_64.AppImage
├── README.txt
└── data/
    ├── config/
    ├── state/
    ├── tools/
    ├── cache/
    │   └── cookies/
    └── integration/
```

Replacing only the `.AppImage` leaves user settings/history intact. Deleting the containing folder removes the portable application data, unless the user separately created desktop/menu shortcuts.

## Runtime integration

The frozen GUI sanitizes PyInstaller's `LD_LIBRARY_PATH` before launching external tools. This is important for host helpers such as KWallet/DBus utilities used by browser-cookie extraction: system helpers must load the host distribution's libraries rather than the AppImage's Qt/runtime libraries.

FFmpeg uses its own private bundled shared-library path, scoped only to the FFmpeg/ffprobe wrappers.

## Licensing metadata

The build installs YT-Downloader's MIT license, `THIRD_PARTY.md`, `PRIVACY.md` and a generated `COMPONENT_VERSIONS.txt` into `usr/share/doc/yt-downloader/` inside the AppImage.

See the repository-root `THIRD_PARTY.md` for the third-party licensing overview.

## GitHub Actions and Releases

`.github/workflows/appimage.yml` builds the same AppImage on Ubuntu 22.04 and uploads it as a workflow artifact on relevant pushes to `main` or on a manual workflow dispatch.

After the AppImage is built, `scripts/build-portable-appimage-release.sh` creates a ready-to-extract portable ZIP with a stable top-level `YT-Downloader/` folder.

Tags matching `v*` publish a GitHub Release containing:

- `YT-Downloader-<version>-Linux-x86_64-portable.zip`
- the portable ZIP `.sha256` checksum
- `YT-Downloader-<version>-x86_64.AppImage`
- the AppImage `.sha256` checksum

The in-app updater reads the latest GitHub Release, replaces the AppImage using the new release filename and preserves neighboring portable data.

The AppImage is currently a public beta. Test across multiple distributions and desktop environments before declaring a stable release.
