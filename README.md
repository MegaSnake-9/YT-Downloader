# YT-Downloader

YT-Downloader is a desktop GUI for **yt-dlp** built with **Python + PySide6/Qt**. It is focused on convenient audio/video downloading, playlists, format selection, presets, metadata, thumbnails, download history and a portable data layout.

> **Current status:** early public-development / beta. The current Linux portable build is a preview of the future AppImage packaging and is being actively tested on Arch Linux + KDE Plasma.

## Highlights

- Audio-only and video downloads
- Playlist support with item selection/skipping
- M4A/AAC, MP3 and other yt-dlp/FFmpeg-backed formats
- Metadata and embedded artwork
- Browser-cookie support for content that requires a signed-in YouTube session
- Saved presets
- Queue, current log and download history
- Error descriptions and stored error logs
- Polish and English UI
- Portable data layout: settings/history can live next to the application instead of being scattered through the home directory

## Portable layout

The planned universal Linux release uses this layout:

```text
YT-Downloader/
├── YT-Downloader.AppImage
└── data/
    ├── config/
    ├── state/
    ├── tools/
    └── cache/
```

Updating the application will replace only `YT-Downloader.AppImage`; the `data/` directory is left untouched.

The current **Linux Portable Preview** uses the same data model but still relies on system-installed Python/PySide6/yt-dlp/FFmpeg.

## Running from source

### Requirements

- Python 3.11+
- PySide6 / Qt 6
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

## Building the current portable preview

```bash
./scripts/build-portable-preview.sh
```

The generated ZIP is placed in `dist/` and is intentionally ignored by Git.

## AppImage

A self-contained AppImage is planned. The application-side portable path handling is already present, while the final bundling of Python, Qt/PySide6, yt-dlp, FFmpeg and runtime libraries is still being prepared.

See [`packaging/appimage/README.md`](packaging/appimage/README.md).

## Repository layout

```text
src/                    application source
assets/                 application icons
packaging/linux-portable/ portable-preview helpers
packaging/appimage/      AppImage packaging scaffold
scripts/                 build/development scripts
.github/workflows/       GitHub Actions
```

## Development status

The project is currently being developed primarily on Arch Linux + KDE Plasma. The goal is to keep the application itself desktop-environment independent and provide a universal AppImage that can be tested on KDE, GNOME and other Linux desktops.

## License

MIT — see [`LICENSE`](LICENSE).

## Legal note

YT-Downloader is a graphical front end around third-party tools. Users are responsible for complying with the terms of the services they access and for downloading only content they are permitted to use.
