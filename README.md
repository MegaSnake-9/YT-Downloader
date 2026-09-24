# YT-Downloader

YT-Downloader is a desktop GUI for **yt-dlp**, built with **Python + PySide6/Qt**. It focuses on convenient audio/video downloading, playlists, format selection, presets, metadata, thumbnails, download history and a portable Linux AppImage workflow.

> **Status:** public beta. The current prebuilt release targets **Linux x86_64**. Development and most testing currently happen on Arch Linux + KDE Plasma, with broader distro/desktop testing still in progress.

**YT-Downloader is an independent project and is not affiliated with, endorsed by, or sponsored by YouTube or Google.**

## Download

The recommended Linux build is the latest x86_64 AppImage from **GitHub Releases**:

https://github.com/MegaSnake-9/YT-Downloader/releases/latest

After downloading it, make the file executable and run it:

```bash
chmod +x YT-Downloader-*-x86_64.AppImage
./YT-Downloader-*-x86_64.AppImage
```

No traditional installation is required. From the application settings you can optionally create shortcuts in the desktop environment's application menu and/or on the desktop.

## Highlights

- Audio-only and video downloads
- Playlist support with item selection/skipping
- M4A/AAC, Opus, MP3 and other yt-dlp/FFmpeg-backed formats
- Standard/DRC source-audio selection for M4A/AAC and Opus
- Metadata and embedded artwork
- Browser-cookie support for content that requires a signed-in session
- Support for higher-quality audio streams available to an authenticated YouTube Music Premium account
- Saved presets
- Queue, current log and download history
- Error descriptions and stored error logs
- English and Polish UI
- Portable application data stored beside the AppImage
- In-app update checks using GitHub Releases

## Portable layout

When the AppImage runs, YT-Downloader stores its writable data beside the AppImage instead of scattering it across the home directory:

```text
YT-Downloader/
├── YT-Downloader-0.x.x-x86_64.AppImage
└── data/
    ├── config/
    ├── state/
    ├── tools/
    └── cache/
```

Updating replaces only the AppImage and adopts the filename of the new GitHub Release. The `data/` directory is preserved, so settings, history and other portable state remain in place.

Deleting the containing folder removes the portable application data as well. Shortcuts created separately in the desktop/application menu can be removed from YT-Downloader settings before deleting the folder.

## Supported systems

The current public build is intended for reasonably modern **x86_64 Linux distributions** using glibc. It is designed to be desktop-environment independent and should work on KDE Plasma, GNOME and other common Linux desktops, but it is still a beta and has not yet been validated on every distribution.

Current limitations:

- x86_64 only; there is no ARM64 AppImage yet
- Alpine/musl and unusual immutable/Nix-style environments may require extra work
- browser-cookie integration depends partly on the host desktop/keyring and browser setup
- some systems may require FUSE support for normal AppImage mounting; AppImage extract-and-run remains an alternative

## Browser cookies and account access

No login is required for normal public downloads. For private playlists or higher-quality streams available to a signed-in account, YT-Downloader can ask yt-dlp to use cookies from a browser profile.

YT-Downloader does not operate a project-owned backend service and does not include analytics/telemetry. Authentication material is handled locally and passed to the bundled/host tooling that performs requests to the target service. The target service will naturally receive authentication cookies when they are used for authenticated requests.

Using account cookies with unofficial third-party tools may carry account or service-policy risks. Use this feature only with accounts and content you are authorized to access.

See [`PRIVACY.md`](PRIVACY.md) for a more detailed data-flow summary.

## Updates

The AppImage checks the repository's latest **GitHub Release**. When a newer x86_64 AppImage is available, YT-Downloader can replace the current AppImage, rename it to the new release filename and leave the neighboring `data/` directory untouched.

Release builds also publish a SHA-256 checksum next to the AppImage.

## Running from source

### Requirements

- Python 3.11+
- PySide6 / Qt 6
- certifi
- yt-dlp
- FFmpeg / ffprobe
- AtomicParsley is recommended for some thumbnail workflows

On Arch Linux, for example:

```bash
sudo pacman -S python pyside6 yt-dlp ffmpeg atomicparsley
```

Then:

```bash
git clone https://github.com/MegaSnake-9/YT-Downloader.git
cd YT-Downloader
python src/yt_downloader.py
```

## Building the Linux portable preview

```bash
bash ./scripts/build-portable-preview.sh
```

The generated ZIP is placed in `dist/` and is intentionally ignored by Git.

## Building the AppImage

```bash
bash ./scripts/build-appimage.sh
```

GitHub Actions also builds the x86_64 AppImage automatically on relevant pushes to `main`. Tags matching `v*` publish a GitHub Release containing the AppImage and its SHA-256 checksum.

See [`packaging/appimage/README.md`](packaging/appimage/README.md).

## Repository layout

```text
src/                      application source
assets/                   application icons
packaging/linux-portable/ portable-preview helpers
packaging/appimage/        AppImage packaging scaffold
scripts/                   build/development scripts
.github/workflows/         GitHub Actions
```

## Privacy

YT-Downloader does not include project-owned analytics or telemetry. Local settings/state remain in the portable `data/` directory when using the AppImage. Network access is performed for the actual download services used by yt-dlp and for GitHub Release update checks.

See [`PRIVACY.md`](PRIVACY.md).

## License

The **YT-Downloader source code** is licensed under the MIT License — see [`LICENSE`](LICENSE).

The prebuilt AppImage is a distribution that also contains separately licensed third-party software. Those components are **not relicensed under MIT**. See [`THIRD_PARTY.md`](THIRD_PARTY.md) for the bundled components and their upstream licensing information.

## Legal note

YT-Downloader is a graphical front end around third-party downloading/media tools. Users are responsible for complying with the terms of the services they access and for downloading or processing only content they are permitted to use.

The project does not grant rights to third-party media and does not provide a license to content downloaded through supported services.

## Support the project

If you find YT-Downloader useful, starring the repository, reporting reproducible bugs and testing the AppImage on additional Linux distributions are all helpful.
