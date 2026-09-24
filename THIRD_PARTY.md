# Third-party components

The YT-Downloader **source code** is licensed under MIT. The prebuilt AppImage also contains separate third-party programs and runtime libraries. Those components retain their own licenses and are not relicensed under YT-Downloader's MIT license.

Because the AppImage build intentionally downloads current upstream releases, exact component versions can change between YT-Downloader builds. The AppImage includes a generated `COMPONENT_VERSIONS.txt` file under `usr/share/doc/yt-downloader/` so a particular build can be identified.

## Bundled components

### yt-dlp standalone Linux executable

Upstream: https://github.com/yt-dlp/yt-dlp

The build downloads the official `yt-dlp_linux` release executable. yt-dlp's source repository is primarily released under the Unlicense, but upstream explicitly notes that its **PyInstaller-bundled executables contain third-party GPLv3+ code and the combined executable is GPLv3+**. Upstream also publishes `THIRD_PARTY_LICENSES.txt` for the bundled executable.

Upstream licensing information:
- https://github.com/yt-dlp/yt-dlp#license
- https://github.com/yt-dlp/yt-dlp/blob/master/THIRD_PARTY_LICENSES.txt

### Deno

Upstream: https://github.com/denoland/deno

The build downloads the official x86_64 Linux Deno executable. Deno is distributed under the MIT License; bundled dependencies may carry their own notices as documented upstream.

### FFmpeg / ffprobe

FFmpeg upstream: https://ffmpeg.org/

Build source: https://github.com/BtbN/FFmpeg-Builds

YT-Downloader intentionally downloads the `lgpl-shared` BtbN build. FFmpeg is normally LGPL 2.1-or-later, while enabling certain optional GPL components changes FFmpeg's licensing to GPL. The selected build is the LGPL shared variant; consult the exact upstream build/source information for the release being distributed.

FFmpeg legal/licensing information:
- https://ffmpeg.org/legal.html

### Python runtime

The PyInstaller-frozen GUI includes a Python runtime. Python is distributed under the Python Software Foundation License and related historical licenses.

Upstream licensing information:
- https://docs.python.org/3/license.html

### PySide6 / Qt for Python / Qt

Upstream: https://doc.qt.io/qtforpython-6/

The AppImage uses the community PySide6/Qt for Python distribution installed from PyPI. Qt for Python is offered under LGPLv3/GPLv3 and the Qt commercial license. The community build used here retains its applicable Qt/PySide6 license terms.

### PyInstaller runtime

Upstream: https://pyinstaller.org/

PyInstaller is GPL-licensed with a special exception that permits generated application bundles to be distributed under the application's own license, subject to the licenses of the bundled dependencies. Some PyInstaller files are Apache-2.0 licensed as documented upstream.

### certifi / Mozilla CA bundle

Upstream: https://github.com/certifi/python-certifi

The frozen GUI includes certifi so HTTPS update checks have a portable CA certificate store. certifi declares MPL-2.0; its CA bundle is derived from Mozilla's root certificate data and carries the corresponding notices in the upstream package.

### GLVND / EGL / OpenGL loader libraries

The AppImage copies a small set of generic GL/EGL loader libraries from the Ubuntu build environment so Qt can start reliably. Those libraries retain their upstream/system licenses; they are not part of the MIT-licensed YT-Downloader source code.

## Release packaging notes

The AppImage contains:

- `LICENSE-YT-Downloader` — YT-Downloader's MIT license;
- `THIRD_PARTY.md` — this notice;
- `PRIVACY.md` — application data/network summary;
- `COMPONENT_VERSIONS.txt` — versions reported by the concrete tools/runtime used for that build.

This file is an attribution/licensing overview, not a substitute for the complete upstream license texts and source-availability obligations that may apply to a specific third-party component. Before calling a release "stable", the exact generated AppImage should still receive a final license-compliance review.
