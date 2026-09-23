# Third-party components bundled in the AppImage

The YT-Downloader AppImage is an aggregate that includes separate third-party
executables. They are not relicensed under YT-Downloader's MIT license.

- **yt-dlp** — official standalone Linux executable from
  https://github.com/yt-dlp/yt-dlp/releases — Unlicense and bundled component
  licenses; see the upstream repository for the exact release sources and
  notices.
- **Deno** — official Linux executable from
  https://github.com/denoland/deno/releases — MIT license; source and notices
  are available in the upstream repository.
- **FFmpeg / ffprobe** — LGPL shared build from
  https://github.com/BtbN/FFmpeg-Builds — FFmpeg is licensed under LGPL 2.1+
  for the selected LGPL build. Exact corresponding build sources and build
  scripts are available from the upstream FFmpeg and BtbN repositories.
- **Python / PySide6 / Qt / PyInstaller runtime files** — bundled by
  PyInstaller. These retain their respective upstream licenses.

Release packaging should retain this file. Before a public stable release,
review the exact license files generated/bundled by the build and include any
additional notices required by the specific versions used.
