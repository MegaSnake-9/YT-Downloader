# Privacy and local data

YT-Downloader is a desktop application. The project does not operate its own backend service and the application source does not include project-owned analytics or telemetry.

## Local application data

In AppImage/portable mode, writable application data is stored beside the AppImage under `data/`, including settings, queue/history state and caches. This makes the application state easy to inspect, back up or remove.

## Browser cookies

If browser-cookie authentication is enabled, YT-Downloader delegates browser-cookie handling to yt-dlp and related host keyring/browser mechanisms. Depending on the selected browser and platform, temporary/local cookie data may be created as part of that workflow.

Cookies are authentication credentials. Do not post them in GitHub issues, logs or screenshots. When cookies are used for authenticated downloads, yt-dlp sends the relevant authentication data to the target service as part of those requests.

## Network access

Network requests can include:

- requests made by yt-dlp to the media service being accessed;
- requests made by FFmpeg when a selected workflow requires network media access;
- GitHub API/Release requests used by the in-app update checker;
- downloads of third-party build dependencies during AppImage creation in GitHub Actions or a local build environment.

YT-Downloader does not proxy downloads through a project-owned server.

## Logs and bug reports

Logs may contain URLs, file paths, video identifiers, browser/profile information or other contextual data. Before posting a log publicly, remove cookies, authorization tokens, private URLs and other sensitive information.
