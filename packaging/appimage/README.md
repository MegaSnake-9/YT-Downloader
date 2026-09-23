# AppImage packaging

This directory is the scaffold for the final self-contained AppImage.

The application already supports the planned portable layout. When started from an AppImage, it reads the `APPIMAGE` environment variable and stores user data next to the AppImage in:

```text
YT-Downloader/
├── YT-Downloader.AppImage
└── data/
    ├── config/
    ├── state/
    ├── tools/
    └── cache/
```

The final builder still needs to bundle Python, PySide6/Qt, yt-dlp, FFmpeg and the remaining runtime libraries into the AppDir. Do not publish a file as an AppImage until it is produced by a real AppImage builder and tested on more than one Linux distribution/desktop environment.
