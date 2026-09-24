#!/usr/bin/env python3
import hashlib
import ssl
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import threading
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass, asdict, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    import certifi
except ImportError:
    certifi = None

from PySide6.QtCore import QByteArray, QEvent, QObject, QThread, Signal, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QFrame, QHeaderView, QInputDialog, QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QPlainTextEdit, QProgressBar, QScrollArea, QSizePolicy, QSpinBox, QStyle, QStyleOptionComboBox, QStylePainter, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget
)

APP_NAME = "YT-Downloader"
VERSION = "0.4.42"
CONTROL_HEIGHT = 28
GITHUB_REPO = "MegaSnake-9/YT-Downloader"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"


class NoWheelComboBox(QComboBox):
    """Combo box with stable text inset and no accidental wheel changes.

    KDE/Qt styles can ignore or visually swallow QSS ``padding-left`` on the
    closed combo-box label. Drawing the label a few pixels further inside
    makes the left breathing room deterministic in every combo used by the
    application while leaving the native frame and arrow untouched.
    """
    TEXT_INSET = 8

    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        painter = QStylePainter(self)

        frame_opt = QStyleOptionComboBox()
        self.initStyleOption(frame_opt)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, frame_opt)

        label_opt = QStyleOptionComboBox()
        self.initStyleOption(label_opt)
        label_opt.rect = label_opt.rect.adjusted(self.TEXT_INSET, 0, 0, 0)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, label_opt)


class NoWheelSpinBox(QSpinBox):
    """Spin box that does not change value from wheel scrolling."""
    def wheelEvent(self, event):
        event.ignore()


class CenteredDisplaySpinBox(NoWheelSpinBox):
    """Compact spin box with a visually centered value and matching caret.

    Qt lays out the QSpinBox editor separately from the up/down button column,
    so its native text/caret cannot be made to visually match a value centered
    over the tile by alignment alone.  The real editor remains active for
    keyboard input, but its painting is covered by an opaque overlay.  The
    overlay shows the current editor text and a custom blinking caret computed
    from the exact same centered text geometry.  This keeps the number and
    caret together regardless of the KDE/Qt style's native spin-button width.
    """

    # Compared with 0.4.27 the visible value is shifted another ~2 px away
    # from the arrow column.
    OVERLAY_RIGHT_COMPENSATION = 16

    def __init__(self, parent=None):
        super().__init__(parent)

        self._value_overlay = QLabel(self)
        self._value_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        # Opaque base background is intentional: it hides Qt's native caret
        # underneath.  We draw our own caret on top, using the same geometry
        # as the visible value.
        self._value_overlay.setStyleSheet(
            "QLabel { background-color: palette(base); border: none; "
            "padding: 0px; margin: 0px; }"
        )

        self._caret_overlay = QFrame(self)
        self._caret_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._caret_overlay.setStyleSheet(
            "QFrame { background-color: palette(text); border: none; }"
        )
        self._caret_overlay.setFixedWidth(1)
        self._caret_on = True
        self._caret_timer = QTimer(self)
        self._caret_timer.setInterval(530)
        self._caret_timer.timeout.connect(self._blink_caret)
        self._caret_timer.start()

        editor = self.lineEdit()
        if editor is not None:
            editor.textChanged.connect(self._sync_overlays)
            editor.cursorPositionChanged.connect(self._sync_overlays)
            editor.selectionChanged.connect(self._sync_overlays)
            editor.installEventFilter(self)

        self.valueChanged.connect(self._sync_overlays)
        self._sync_overlays()

    def _overlay_rect(self):
        # Nakładka dochodzi pionowo do samej krawędzi kontrolki, żeby przykryć
        # także ostatni piksel natywnego kursora QLineEdit. Poziomo nadal
        # zostawiamy prawą kolumnę natywnych strzałek.
        return self.rect().adjusted(1, 0, -self.OVERLAY_RIGHT_COMPENSATION, 0)

    def _display_text(self):
        editor = self.lineEdit()
        if editor is not None:
            text = editor.text()
            if text:
                return text
        return self.textFromValue(self.value())

    def _sync_overlays(self, *_args):
        text = self._display_text()
        rect = self._overlay_rect()
        self._value_overlay.setText(text)
        self._value_overlay.setGeometry(rect)
        self._value_overlay.raise_()

        editor = self.lineEdit()
        focused = bool(editor is not None and editor.hasFocus())
        if not focused or not self._caret_on:
            self._caret_overlay.hide()
            return

        # Calculate the caret from exactly the same horizontally centered
        # string that QLabel displays.  It therefore sits directly beside the
        # visible digit(s), not at Qt's hidden native editor coordinates.
        fm = self._value_overlay.fontMetrics()
        text_width = fm.horizontalAdvance(text)
        text_x = rect.x() + max(0, (rect.width() - text_width) // 2)
        cursor_pos = max(0, min(editor.cursorPosition(), len(text)))
        caret_x = text_x + fm.horizontalAdvance(text[:cursor_pos])

        caret_h = max(10, fm.height() - 2)
        caret_y = rect.y() + max(0, (rect.height() - caret_h) // 2)
        self._caret_overlay.setGeometry(caret_x, caret_y, 1, caret_h)
        self._caret_overlay.show()
        self._caret_overlay.raise_()

    def _blink_caret(self):
        editor = self.lineEdit()
        if editor is None or not editor.hasFocus():
            self._caret_on = True
            self._caret_overlay.hide()
            return
        self._caret_on = not self._caret_on
        self._sync_overlays()

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
        ):
            self._caret_on = True
            QTimer.singleShot(0, self._sync_overlays)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlays()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_overlays()

def apply_uniform_control_metrics(root):
    """Give ordinary interactive controls exactly the same outer height."""
    for cls in (QPushButton, QComboBox, QLineEdit, QSpinBox):
        for widget in root.findChildren(cls):
            widget.setFixedHeight(CONTROL_HEIGHT)
    for widget in root.findChildren(QCheckBox):
        widget.setFixedHeight(CONTROL_HEIGHT)


def apply_uniform_dialog_buttons(root):
    """Give buttons in every Qt dialog the same 28 px height as the app."""
    if root is None:
        return
    for button in root.findChildren(QPushButton):
        button.setFixedHeight(CONTROL_HEIGHT)


def polish_message_box(box):
    """Normalize message-box controls and, especially, its buttons."""
    apply_uniform_dialog_buttons(box)


class DialogButtonMetricsFilter(QObject):
    """Normalize buttons in all dialogs, including static Qt convenience ones."""
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QDialog):
            QTimer.singleShot(0, lambda w=watched: apply_uniform_dialog_buttons(w))
        return super().eventFilter(watched, event)


APP_ICON = Path(__file__).resolve().with_name("yt-downloader.png")

# Portable Linux/AppImage mode keeps every writable YT-Downloader file next
# to the launcher/AppImage in one data/ directory. Nothing is written to
# ~/.config or ~/.local in this mode. APPIMAGE is provided by the AppImage
# runtime; YT_DOWNLOADER_PORTABLE_ROOT is used by the unpacked preview.
APP_ROOT = Path(__file__).resolve().parent
_APPIMAGE_PATH = os.environ.get("APPIMAGE", "").strip()
_PORTABLE_ROOT_ENV = os.environ.get("YT_DOWNLOADER_PORTABLE_ROOT", "").strip()

# Windows keeps its existing portable/non-portable behaviour. Linux becomes
# portable when started from an AppImage or through our portable launcher.
_PORTABLE_MARKERS = (
    APP_ROOT / "portable-first-run.ps1",
    APP_ROOT / "Start YT-Downloader Portable.bat",
)
if os.name == "nt":
    PORTABLE = (
        os.environ.get("YT_DOWNLOADER_PORTABLE") == "1"
        or any(marker.exists() for marker in _PORTABLE_MARKERS)
    )
else:
    PORTABLE = bool(_APPIMAGE_PATH or _PORTABLE_ROOT_ENV)

if os.name == "nt":
    if PORTABLE:
        PORTABLE_ROOT = Path(
            os.environ.get("YT_DOWNLOADER_PORTABLE_ROOT", str(APP_ROOT))
        ).resolve()
        DATA_ROOT = PORTABLE_ROOT / "data"
        CFG_DIR = DATA_ROOT / "config"
        CFG_FILE = CFG_DIR / "config.json"
        OLD_CFG = CFG_DIR / "muzyka-downloader.json"
        STATE_DIR = DATA_ROOT / "state"
        CACHE_DIR = DATA_ROOT / "cache"
        TOOLS_DIR = DATA_ROOT / "tools"
        _tool_dirs = [TOOLS_DIR, PORTABLE_ROOT / "tools"]
    else:
        _appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        _localappdata = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        CFG_DIR = _appdata / "YT-Downloader"
        CFG_FILE = CFG_DIR / "config.json"
        OLD_CFG = _appdata / "Muzyka-Downloader" / "config.json"
        STATE_DIR = _localappdata / "YT-Downloader" / "state"
        CACHE_DIR = STATE_DIR
        TOOLS_DIR = _localappdata / "YT-Downloader" / "tools"
        _tool_dirs = [
            _localappdata / "Microsoft" / "WinGet" / "Links",
            TOOLS_DIR,
        ]

    _path_parts = [str(x) for x in _tool_dirs if x]
    if os.environ.get("PATH"):
        _path_parts.append(os.environ["PATH"])
    os.environ["PATH"] = os.pathsep.join(_path_parts)
else:
    if PORTABLE:
        if _PORTABLE_ROOT_ENV:
            PORTABLE_ROOT = Path(_PORTABLE_ROOT_ENV).expanduser().resolve()
        else:
            # APPIMAGE points to the actual .AppImage file, not its mounted
            # internal /tmp path. This keeps data beside the file even after
            # updates or when the whole folder is moved.
            PORTABLE_ROOT = Path(_APPIMAGE_PATH).expanduser().resolve().parent
        DATA_ROOT = PORTABLE_ROOT / "data"
        CFG_DIR = DATA_ROOT / "config"
        CFG_FILE = CFG_DIR / "config.json"
        OLD_CFG = CFG_DIR / "muzyka-downloader.json"
        STATE_DIR = DATA_ROOT / "state"
        CACHE_DIR = DATA_ROOT / "cache"
        TOOLS_DIR = DATA_ROOT / "tools"
        _tool_dirs = [TOOLS_DIR]
        _path_parts = [str(TOOLS_DIR)]
        if os.environ.get("PATH"):
            _path_parts.append(os.environ["PATH"])
        os.environ["PATH"] = os.pathsep.join(_path_parts)
    else:
        PORTABLE_ROOT = None
        DATA_ROOT = None
        CFG_DIR = Path.home() / ".config" / "yt-downloader"
        CFG_FILE = CFG_DIR / "config.json"
        OLD_CFG = Path.home() / ".config" / "muzyka-downloader" / "config.json"
        STATE_DIR = Path.home() / ".local" / "state" / "yt-downloader"
        CACHE_DIR = STATE_DIR
        TOOLS_DIR = Path.home() / ".local" / "share" / "yt-downloader" / "tools"

STATS_DB = STATE_DIR / "statistics.sqlite3"
QUEUE_FILE = STATE_DIR / "queue.json"
COOKIE_CACHE_DIR = CACHE_DIR / "cookies"
MANUAL_COOKIE_FILE = COOKIE_CACHE_DIR / "manual-youtube.txt"
EXTENSION_COOKIE_FILE = COOKIE_CACHE_DIR / "brave-extension-youtube.txt"
BRAVE_BRIDGE_HOST = "127.0.0.1"
BRAVE_BRIDGE_PORT = 43821
BRAVE_EXTENSION_ID = "mlilfckbjfndlhbfmogbfehgmnbnlpep"
BRAVE_EXTENSION_ORIGIN = f"chrome-extension://{BRAVE_EXTENSION_ID}"

# Keep command-line helper windows hidden when the GUI runs through pythonw.exe.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

def external_subprocess_env():
    """Environment for command-line tools launched by the frozen GUI.

    PyInstaller prepends its bundled library directory to LD_LIBRARY_PATH on
    Linux.  That is required by the frozen GUI itself, but system helpers
    launched later by yt-dlp (notably kwallet-query and dbus-send) must use
    the host distribution's libraries.  PyInstaller preserves the original
    value in LD_LIBRARY_PATH_ORIG; restore it for external subprocess trees.
    """
    env = os.environ.copy()
    if os.name != "nt" and getattr(sys, "frozen", False):
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is not None:
            if original:
                env["LD_LIBRARY_PATH"] = original
            else:
                env.pop("LD_LIBRARY_PATH", None)
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env

def tool_executable(name):
    """Return an executable path, preferring portable data/tools."""
    exe_name = name
    if os.name == "nt" and not exe_name.lower().endswith(".exe"):
        exe_name += ".exe"
    if PORTABLE:
        candidates = [TOOLS_DIR / exe_name]
        # Backward compatibility with the old Windows portable layout.
        if os.name == "nt":
            candidates.append(PORTABLE_ROOT / "tools" / exe_name)
        for bundled in candidates:
            if bundled.is_file():
                return str(bundled)
    return shutil.which(name) or shutil.which(exe_name) or name

def tool_available(name):
    resolved = tool_executable(name)
    p = Path(resolved)
    if p.is_absolute():
        return p.is_file()
    return shutil.which(resolved) is not None



def ensure_portable_dirs():
    if not PORTABLE:
        return
    for directory in (CFG_DIR, STATE_DIR, CACHE_DIR, TOOLS_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

def xdg_user_dir(name, fallback):
    """Read ~/.config/user-dirs.dirs without requiring xdg-user-dirs."""
    cfg = Path.home() / ".config" / "user-dirs.dirs"
    key = f"XDG_{name}_DIR"

    try:
        if cfg.exists():
            for raw in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line.startswith(key + "="):
                    continue

                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                value = value.replace("$HOME", str(Path.home()))
                value = os.path.expandvars(value)
                return Path(value).expanduser()
    except Exception:
        pass

    return Path.home() / fallback


# Dla nowej instalacji ogólnego YT-Downloadera lepszym miejscem jest
# Pobrane/Downloads. Istniejąca konfiguracja użytkownika nadal ma pierwszeństwo.
DEFAULT_DIR = str((Path.home() / "Downloads" / "YT-Downloader") if os.name == "nt" else (xdg_user_dir("DOWNLOAD", "Downloads") / "YT-Downloader"))

TR = {
    "pl": {
        "settings": "⚙ Ustawienia",
        "add_queue": "Dodaj do kolejki",
        "link": "Link:",
        "paste": "Wklej",
        "mode": "Tryb:",
        "playlist_checked": "Playlista — zaznacz, aby pobrać całą",
        "track_checked": "Utwór — zaznacz, aby pobrać tylko jeden",
        "download": "Pobierz:",
        "format": "Format:",
        "quality": "Jakość:",
        "path": "Ścieżka:",
        "show_path": "Pokaż ścieżkę…",
        "playlist": "Playlista:",
        "select": "Wybierz",
        "omit": "Pomiń",
        "numbers": "Numery:",
        "numbers_hint": "np. 1,3,5-7",
        "add": "Dodaj",
        "queue": "Kolejka",
        "col_mode": "Tryb",
        "col_media": "Media",
        "col_format": "Format",
        "col_quality": "Jakość",
        "col_path": "Ścieżka",
        "col_link": "Link",
        "col_status": "Status",
        "remove_selected": "Usuń zaznaczone",
        "clear_queue": "Wyczyść kolejkę",
        "open_default": "Otwórz domyślny folder",
        "stop": "Przerwij",
        "download_all": "Pobierz wszystko",
        "current_log": "Log bieżący",
        "audio_only": "Tylko audio",
        "video_only": "Tylko obraz",
        "audio_video": "Obraz + audio",
        "original": "Najlepszy",
        "automatic": "Automatyczny",
        "best": "Najlepsza",
        "waiting": "Oczekuje",
        "downloading": "Pobieranie…",
        "ready": "Gotowe",
        "stopped": "Przerwano",
        "track_movie": "Utwór / film",
        "playlist_word": "Playlista",
        "choose_word": "wybierz",
        "omit_word": "pomiń",
        "copy_link": "Kopiuj link",
        "copy_path": "Kopiuj ścieżkę",
        "show_error_desc": "Pokaż opis błędu",
        "show_log": "Pokaż log błędu",
        "error": "Błąd",
        "warning": "Ostrz.",
        "no_log": "(brak logu)",
        "invalid_url": "Wklej poprawny adres URL.",
        "cannot_add": "Nie można dodać",
        "empty_queue": "Kolejka jest pusta.",
        "missing_tools": "Brak yt-dlp lub ffmpeg.",
        "thread_closing": "Poprzedni wątek jeszcze się kończy.",
        "stop_requested": "Żądanie przerwania wysłane…",
        "queue_stopped": "Kolejka przerwana.",
        "queue_done": "Kolejka zakończona.",
        "close_running": "Trwa pobieranie. Przerwać i zamknąć?",
        "download_ok": "Pobrano poprawnie.",
        "download_stopped": "Pobieranie przerwane.",
        "checking_playlist": "Sprawdzam dostęp do playlisty…",
        "retry_wait": "Chwilowy błąd. Ponawiam za {seconds} s…",
        "retry_attempt": "Ponowienie {attempt}/{max_attempts}: {what}",
        "retry_playlist_items": "elementy playlisty: {items}",
        "retry_item": "bieżący materiał",
        "retry_success": "Ponowienie zakończone powodzeniem.",
        "m4a_best_fallback": "YouTube Music nie zwrócił materiału poprawnie — próbuję zwykłego adresu YouTube.",
        "retry_exhausted": "Automatyczne ponowienia nie rozwiązały problemu.",
        "settings_title": "Ustawienia — YT-Downloader",
        "language": "Język:",
        "polish": "Polski",
        "english": "English",
        "default_folder": "Domyślny folder zapisu:",
        "default_subfolder": "Domyślny podkatalog:",
        "optional": "opcjonalnie",
        "choose": "Wybierz…",
        "default_playlist": "Domyślnie traktuj link jako playlistę",
        "cookies_group": "Dostęp do prywatnych materiałów / cookies",
        "browser": "Przeglądarka:",
        "profile": "Profil:",
        "choose_profile": "Wybierz profil…",
        "keyring": "Magazyn kluczy:",
        "no_login": "Bez logowania",
        "chromium_ext_browser": "Rozszerzenie Chromium (zalecane Windows)",
        "chromium_ext_setup_title": "Rozszerzenie Chromium — YT-Downloader",
        "chromium_ext_setup_text": "1. Otwórz stronę rozszerzeń swojej przeglądarki:\n   Brave: brave://extensions\n   Chrome/Chromium: chrome://extensions\n   Edge: edge://extensions\n   Vivaldi: vivaldi://extensions\n   Opera / Opera GX: opera://extensions\n\n2. Włącz Tryb dewelopera.\n3. Kliknij „Załaduj rozpakowane”.\n4. Wskaż folder „YT-Downloader-Chromium”, który jest widoczny w otwartym folderze Eksploratora.\n5. Uruchom YT-Downloader i kliknij w rozszerzeniu „Synchronizuj teraz”.\n6. W ustawieniach YT-Downloadera wybierz „Rozszerzenie Chromium (zalecane Windows)”.\n\nRozszerzenie może zostać zainstalowane na stałe — wtedy będzie automatycznie odświeżało cookies YouTube. Po jednorazowej synchronizacji można je usunąć, ale zapisane cookies z czasem mogą wygasnąć lub zostać zmienione przez YouTube.",
        "auto": "Automatycznie",
        "kwallet6": "KWallet 6",
        "kwallet5": "KWallet 5",
        "kwallet_legacy": "KWallet (starszy)",
        "cookie_linux_read_failed": "Nie udało się odczytać cookies z {browser} przez {keyring}. Sprawdź, czy magazyn kluczy jest odblokowany i czy przeglądarka używa tego samego magazynu.",
        "cookie_linux_auto_failed": "Nie udało się odczytać cookies z {browser} przy automatycznym wyborze magazynu (wykryto: {detected}). Szczegóły techniczne są dostępne w logu błędu.",
        "opera_custom": "Opera GX / niestandardowy profil Opera",
        "cookies_note": "Bez logowania jest domyślne. Dla prywatnej playlisty lub YouTube Music Premium wybierz przeglądarkę zalogowaną do YouTube. Na Linuxie yt-dlp odczytuje cookies bezpośrednio z profilu przeglądarki. Opera GX korzysta z mechanizmu Opera i wymaga ręcznego wskazania profilu.",
        "brave_ext": "Rozszerzenie Chromium:",
        "brave_ext_ready": "Połączono — {count} cookies, ostatnia synchronizacja: {time}",
        "brave_ext_waiting": "Brak synchronizacji. Uruchom YT-Downloader, załaduj rozszerzenie w wybranej przeglądarce Chromium i kliknij „Synchronizuj teraz”.",
        "brave_ext_install": "Otwórz folder + instrukcję",
        "brave_ext_refresh": "Odśwież status",
        "brave_ext_open_failed": "Nie udało się otworzyć folderu rozszerzenia: {error}",
        "cookie_file": "Plik cookies.txt:",
        "cookie_file_none": "Brak zaimportowanego pliku",
        "cookie_file_ready": "Zaimportowano cookies YouTube",
        "cookie_file_import": "Importuj cookies.txt…",
        "cookie_file_remove": "Usuń",
        "cookie_file_dialog": "Wybierz cookies.txt (format Netscape)",
        "cookie_file_bad": "Plik cookies.txt jest nieprawidłowy albo nie zawiera cookies dla youtube.com.",
        "cookie_file_imported": "Zaimportowano cookies YouTube. Program będzie używał ich zamiast bezpośredniego odczytu z przeglądarki.",
        "files": "Pliki",
        "embed_meta": "Osadzaj metadane",
        "embed_thumb": "Osadzaj okładkę/miniaturę w audio",
        "no_overwrite": "Nie nadpisuj istniejących plików",
        "opera_profile_required": "Dla Opera GX wskaż katalog profilu.",
        "default_folder_empty": "Domyślny folder nie może być pusty.",
        "settings_error": "Błąd ustawień",
        "choose_folder": "Wybierz folder",
        "choose_destination": "Wybierz folder docelowy",
        "choose_profile_dialog": "Wybierz profil",
        "audio_quality_tip": "M4A/Opus pobierają źródłowy strumień YouTube bez zbędnego ponownego kodowania. Podane bitrate są wartościami przybliżonymi.",
        "mp3_quality_tip": "MP3: VBR używa poziomów LAME V0/V2/V4, a tryb Bitrate ustawia 320/256/192/128 kb/s. Ponowne kodowanie nie poprawi jakości źródła z YouTube.",
        "m4a_quality_tip": "M4A/AAC: Premium/high (~256 kb/s) wymaga dostępu Premium/cookies. Standard to około 128 kb/s. Wariant DRC ma fallback do standardowego strumienia, jeśli DRC dla wybranej jakości nie istnieje.",
        "premium_256": "Premium / high (~256 kb/s)",
        "standard_128": "Standard (~128 kb/s)",
        "audio_variant": "Wariant:",
        "audio_variant_standard": "Standardowy",
        "audio_variant_drc": "DRC",
        "drc_fallback_status": "Gotowe • standard (brak DRC)",
        "drc_fallback_desc": "Wybrano DRC, ale dla tej jakości DRC nie było dostępne — pobrano standardowy strumień.",
        "format_variant": "Wariant",
        "format_variant_standard": "Standardowy",
        "format_variant_drc": "DRC",
        "format_checker": "Sprawdź formaty:",
        "format_checker_paste_hint": "wklej link do sprawdzenia",
        "check_formats": "Sprawdź formaty",
        "checking_formats": "Sprawdzam…",
        "format_check_title": "Dostępne formaty — YT-Downloader",
        "format_check_failed": "Nie udało się sprawdzić formatów.",
        "format_check_audio": "Tylko audio",
        "format_check_video": "Tylko wideo",
        "format_check_av": "Wideo + audio",
        "format_check_source": "Materiał: {title}",
        "format_check_m4a": "Najlepsze M4A/AAC: {quality}",
        "format_check_opus": "Najlepszy Opus: {quality}",
        "format_check_video_max": "Najlepsze wideo: {quality}",
        "format_check_none": "brak",
        "format_check_merge_note": "Tabela pokazuje surowe formaty audio i wideo dostępne w YouTube. Kliknij nagłówek kolumny, aby sortować; PPM na wierszu pozwala użyć jego ID w zaawansowanym selektorze -f.",
        "format_check_used_music": "Dla audio sprawdzono także YouTube Music, aby wykryć strumienie Premium.",
        "variant": "Dokładny wariant:",
        "variant_auto": "Automatycznie / reguły powyżej",
        "variant_hint": "Najpierw kliknij „Sprawdź formaty”, aby wczytać dokładne warianty/ID dla tego linku.",
        "variant_exact_tip": "Dokładny wariant z analizy formatu. Po włączeniu zastępuje inteligentne reguły wyboru.",
        "exact_variant_toggle": "Użyj dokładnego wariantu/ID",
        "video_rules": "Obraz",
        "audio_rules": "Audio",
        "container": "Kontener:",
        "resolution": "Rozdzielczość:",
        "fps": "FPS:",
        "codec": "Kodek:",
        "video_tier": "Wariant jakości:",
        "video_bitrate": "Bitrate:",
        "auto_choice": "Automatycznie",
        "best_available": "Najlepszy dostępny",
        "standard_video": "Standard",
        "premium_video": "Premium / enhanced",
        "h264": "H.264 / AVC",
        "vp9": "VP9",
        "av1": "AV1",
        "hevc": "H.265 / HEVC",
        "audio_format": "Format:",
        "audio_bitrate": "Bitrate:",
        "audio_mode": "Tryb:",
        "audio_mode_vbr": "VBR",
        "audio_mode_bitrate": "Bitrate",
        "mp3_v0": "V0 — najlepsza",
        "mp3_v2": "V2",
        "mp3_v4": "V4",
        "advanced_title": "Zaawansowane yt-dlp",
        "advanced_selector": "Selektor formatu (-f):",
        "advanced_selector_hint": "Puste = wybór z kafelków. Własny -f zastępuje automatyczny wybór formatu.",
        "advanced_args": "Dodatkowe argumenty:",
        "advanced_args_hint": "Opcjonalne argumenty yt-dlp, np. --remux-video mkv. URL, katalog, cookies i kolejkę nadal obsługuje program.",
        "advanced_preview": "Podgląd komendy:",
        "advanced_copy": "Kopiuj",
        "advanced_bad_args": "Niepoprawne dodatkowe argumenty yt-dlp — sprawdź cudzysłowy.",
        "format_pick_id": "Użyj ID w -f",
        "format_add_id": "Dodaj ID do -f",
        "smart_rules_hint": "Po „Sprawdź formaty” listy zawężają się do kombinacji rzeczywiście dostępnych dla materiału. Dla playlist są traktowane jako reguły dobierane osobno do każdego filmu.",
        "bitrate_single_only": "Dokładny bitrate jest używany tylko dla pojedynczego, wcześniej sprawdzonego filmu. Dla playlist pozostaje Automatycznie.",
        "format_check_container": "Kontener",
        "video_quality_tip": "Najlepsza wybiera najwyższą dostępną jakość. Pozostałe wartości ustawiają maksymalną wysokość obrazu.",
        "numbers_bad": 'Numery podaj np. "1,3,5-7,10".',
        "range_bad": "Błędny zakres: {x}",
        "numbers_start": "Numery zaczynają się od 1.",
        "no_items_after_omit": "Po pominięciu numerów nie został żaden element.",
        "e01": "Nieprawidłowy albo nieobsługiwany link.",
        "e02": "Materiał wymaga logowania albo playlista jest prywatna/niedostępna bez cookies.",
        "e03": "Materiał jest niedostępny.",
        "e04": "Wybrany format lub jakość nie jest dostępna.",
        "e05": "Problem z połączeniem sieciowym.",
        "e06": "Nie udało się odczytać cookies z przeglądarki.",
        "cookie_locked_windows": "Windows blokuje bazę cookies przeglądarki {browser}. Zamknij ją całkowicie (także procesy działające w tle) i spróbuj ponownie.",
        "cookie_dpapi_windows": "{browser} używa szyfrowania cookies Chromium App-Bound/DPAPI. Samo zamknięcie przeglądarki tego nie naprawi. W Ustawieniach użyj „Rozszerzenia Chromium”, załaduj je w swojej przeglądarce, uruchom YT-Downloader i zsynchronizuj cookies. Możesz też użyć Firefox lub ręcznego cookies.txt.",
        "e07": "Błąd FFmpeg lub obróbki pliku.",
        "e08": "Nie można zapisać pliku.",
        "e99": "Nieznany błąd yt-dlp/FFmpeg.",
        "playlist_no_access": "Nie udało się uzyskać dostępu do playlisty. Jeśli jest prywatna, wybierz cookies z zalogowanej przeglądarki.",
        "not_playlist": "Link nie został rozpoznany jako dostępna playlista.",
        "app_unknown": "Nieznany błąd aplikacji.",
        "error_hint": "PPM → Pokaż opis / Pokaż log",
        "warning_hint": "PPM → Pokaż opis / Pokaż log",
        "partial_playlist": "Playlista została pobrana częściowo. Liczba elementów zakończonych błędem: {n}. Pozostałe dostępne elementy pobrano poprawnie.",
        "queue_extended": "Dodano nowe pozycje podczas pobierania — kontynuuję kolejkę.",
        "remove_from_queue": "Usuń z kolejki",
        "remove_waiting_only": "Podczas pobierania można usuwać tylko pozycje ze statusem Oczekuje.",
        "stats_downloaded": "Pobrano materiału: {done}",
        "stats_work": "Czas: {elapsed}",
        "stats_eta": "Pozostało: {eta}",
        "stats_eta_calc": "obliczanie…",
        "stats_eta_more": "{eta} (+{n} do analizy)",
        "stats_idle_eta": "—",
        "save": "Zapisz",
        "cancel": "Anuluj",
        "open": "Otwórz",
        "close": "Zamknij",
        "shortcuts_group": "Skróty — Linux portable",
        "shortcuts_note": "Opcjonalna integracja. Program i dane nadal pozostają w folderze portable.",
        "shortcut_menu": "Dodaj do menu aplikacji",
        "shortcut_desktop": "Dodaj skrót na pulpit",
        "shortcut_remove": "Usuń skróty",
        "shortcut_menu_created": "Dodano skrót do menu aplikacji.",
        "shortcut_desktop_created": "Dodano skrót na pulpit.",
        "shortcut_removed": "Usunięto skróty YT-Downloadera.",
        "shortcut_failed": "Nie udało się zmienić skrótów: {error}",
        "profile_hint": "opcjonalny katalog profilu; wymagany dla Opera GX",
        "path_hint": "podkatalog albo pełna ścieżka",
        "interface_group": "Interfejs",
        "theme": "Motyw:",
        "theme_system": "Zgodny z systemem",
        "theme_light": "Jasny",
        "theme_dark": "Ciemny",
        "show_history": "Wyświetlaj historię pobierania",
        "confirm_clear_history": "Pytaj o potwierdzenie przed usunięciem historii",
        "show_current_log": "Wyświetlaj bieżący log",
        "show_presets": "Wyświetlaj presety",
        "show_advanced": "Wyświetlaj zaawansowane yt-dlp",
        "panel_rows_title": "Widoczne rzędy segmentów",
        "panel_rows_min": "Min.",
        "panel_rows_max": "Maks.",
        "panel_rows_queue": "Kolejka",
        "panel_rows_history": "Historia",
        "panel_rows_log": "Log",
        "panel_rows_hint": "Segment może się kurczyć do minimum i rosnąć maksymalnie do podanej liczby rzędów.",
        "panel_rows_error": "Dla segmentu „{panel}” wartość minimalna nie może być większa od maksymalnej.",
        "preset": "Preset:",
        "preset_custom": "Własne",
        "preset_save_as": "Zapisz jako…",
        "preset_delete": "Usuń preset",
        "preset_name": "Nazwa presetu",
        "preset_name_prompt": "Podaj nazwę nowego presetu:",
        "preset_overwrite": "Preset o tej nazwie już istnieje. Nadpisać go?",
        "preset_delete_confirm": "Usunąć preset „{name}”?",
        "preset_music_m4a": "Muzyka M4A",
        "preset_music_mp3": "Muzyka MP3",
        "preset_video_1080": "Film 1080p MP4",
        "preset_video_best": "Film — najlepsza jakość",
        "preset_original": "Najlepszy / bez konwersji",
        "subtitles": "Napisy:",
        "sub_none": "Brak",
        "sub_manual": "Zwykłe",
        "sub_auto": "Automatyczne",
        "sub_fallback": "Zwykłe, a jeśli brak → automatyczne",
        "sub_language": "Język:",
        "sub_polish": "Polski",
        "sub_english": "Angielski",
        "sub_output": "Zapis:",
        "sub_separate": "Osobny plik",
        "sub_embed": "Osadź w filmie",
        "sub_both": "Osadź + osobny plik",
        "history": "Historia pobierania",
        "history_date": "Data",
        "history_mode": "Tryb",
        "history_result": "Wynik",
        "history_time": "Czas",
        "refresh": "Odśwież",
        "clear_history": "Wyczyść historię",
        "clear_history_confirm": "Usunąć zapisaną historię pobierania?",
        "history_empty": "Brak zapisanej historii pobierania.",
        "queue_restored": "Przywrócono zapisaną kolejkę: {n} pozycji.",
        "updates_group": "Aktualizacje",
        "check_components": "Sprawdź aktualizacje",
        "update_components": "Aktualizuj z GitHuba…",
        "current_version": "Bieżąca wersja: {version}",
        "latest_version": "Najnowsza wersja: {version}",
        "update_available": "Dostępna jest wersja {version}.",
        "update_none": "Masz najnowszą wersję ({version}).",
        "update_no_release": "Nie znaleziono publicznego wydania GitHub. Aktualizacje z programu zaczną działać po opublikowaniu Release.",
        "update_not_appimage": "Automatyczna podmiana programu działa w wersji AppImage. Otworzono stronę wydań GitHub.",
        "update_confirm": "Pobrać i zainstalować YT-Downloader {version}? Folder data/ z ustawieniami i historią nie zostanie zmieniony.",
        "update_downloaded": "Zainstalowano wersję {version}. Zamknij i uruchom ponownie YT-Downloader.",
        "update_failed": "Aktualizacja nie powiodła się: {error}",
    },
    "en": {
        "settings": "⚙ Settings",
        "add_queue": "Add to queue",
        "link": "Link:",
        "paste": "Paste",
        "mode": "Mode:",
        "playlist_checked": "Playlist — check to download the whole playlist",
        "track_checked": "Single item — check to download only one",
        "download": "Download:",
        "format": "Format:",
        "quality": "Quality:",
        "path": "Path:",
        "show_path": "Browse…",
        "playlist": "Playlist:",
        "select": "Select",
        "omit": "Skip",
        "numbers": "Numbers:",
        "numbers_hint": "e.g. 1,3,5-7",
        "add": "Add",
        "queue": "Queue",
        "col_mode": "Mode",
        "col_media": "Media",
        "col_format": "Format",
        "col_quality": "Quality",
        "col_path": "Path",
        "col_link": "Link",
        "col_status": "Status",
        "remove_selected": "Remove selected",
        "clear_queue": "Clear queue",
        "open_default": "Open default folder",
        "stop": "Stop",
        "download_all": "Download all",
        "current_log": "Current log",
        "audio_only": "Audio only",
        "video_only": "Video only",
        "audio_video": "Video + audio",
        "original": "Best",
        "automatic": "Automatic",
        "best": "Best",
        "waiting": "Waiting",
        "downloading": "Downloading…",
        "ready": "Done",
        "stopped": "Stopped",
        "track_movie": "Single item",
        "playlist_word": "Playlist",
        "choose_word": "select",
        "omit_word": "skip",
        "copy_link": "Copy link",
        "copy_path": "Copy path",
        "show_error_desc": "Show error description",
        "show_log": "Show error log",
        "error": "Error",
        "warning": "Warn.",
        "no_log": "(no log)",
        "invalid_url": "Paste a valid URL.",
        "cannot_add": "Cannot add",
        "empty_queue": "The queue is empty.",
        "missing_tools": "yt-dlp or ffmpeg is missing.",
        "thread_closing": "The previous worker thread is still closing.",
        "stop_requested": "Stop request sent…",
        "queue_stopped": "Queue stopped.",
        "queue_done": "Queue finished.",
        "close_running": "A download is running. Stop it and close?",
        "download_ok": "Downloaded successfully.",
        "download_stopped": "Download stopped.",
        "checking_playlist": "Checking playlist access…",
        "retry_wait": "Temporary error. Retrying in {seconds} s…",
        "retry_attempt": "Retry {attempt}/{max_attempts}: {what}",
        "retry_playlist_items": "playlist items: {items}",
        "retry_item": "current item",
        "retry_success": "Retry completed successfully.",
        "m4a_best_fallback": "YouTube Music did not return the item correctly — trying the regular YouTube URL.",
        "retry_exhausted": "Automatic retries did not resolve the problem.",
        "settings_title": "Settings — YT-Downloader",
        "language": "Language:",
        "polish": "Polski",
        "english": "English",
        "default_folder": "Default download folder:",
        "default_subfolder": "Default subfolder:",
        "optional": "optional",
        "choose": "Choose…",
        "default_playlist": "Treat links as playlists by default",
        "cookies_group": "Private content access / cookies",
        "browser": "Browser:",
        "profile": "Profile:",
        "choose_profile": "Choose profile…",
        "keyring": "Keyring:",
        "no_login": "No login",
        "chromium_ext_browser": "Chromium extension (recommended on Windows)",
        "chromium_ext_setup_title": "Chromium extension — YT-Downloader",
        "chromium_ext_setup_text": "1. Open your browser extension page:\n   Brave: brave://extensions\n   Chrome/Chromium: chrome://extensions\n   Edge: edge://extensions\n   Vivaldi: vivaldi://extensions\n   Opera / Opera GX: opera://extensions\n\n2. Enable Developer mode.\n3. Click “Load unpacked”.\n4. Select the “YT-Downloader-Chromium” folder shown in the Explorer window.\n5. Start YT-Downloader and click “Sync now” in the extension.\n6. In YT-Downloader settings choose “Chromium extension (recommended on Windows)”.\n\nYou can keep the extension installed so it refreshes YouTube cookies automatically. You may remove it after one successful sync, but the saved cookies can later expire or be rotated by YouTube.",
        "auto": "Automatic",
        "kwallet6": "KWallet 6",
        "kwallet5": "KWallet 5",
        "kwallet_legacy": "KWallet (legacy)",
        "cookie_linux_read_failed": "Could not read cookies from {browser} using {keyring}. Check that the keyring is unlocked and that the browser uses the same keyring.",
        "cookie_linux_auto_failed": "Could not read cookies from {browser} with automatic keyring selection (detected: {detected}). Technical details are available in the error log.",
        "opera_custom": "Opera GX / custom Opera profile",
        "cookies_note": "No login is the default. For a private playlist or YouTube Music Premium, select a browser signed in to YouTube. On Linux, yt-dlp reads cookies directly from the browser profile. Opera GX uses Opera handling and requires a manually selected profile.",
        "brave_ext": "Chromium extension:",
        "brave_ext_ready": "Connected — {count} cookies, last sync: {time}",
        "brave_ext_waiting": "No sync yet. Start YT-Downloader, load the extension in your Chromium browser and click “Sync now”.",
        "brave_ext_install": "Open folder + instructions",
        "brave_ext_refresh": "Refresh status",
        "brave_ext_open_failed": "Could not open the extension folder: {error}",
        "cookie_file": "cookies.txt file:",
        "cookie_file_none": "No imported file",
        "cookie_file_ready": "YouTube cookies imported",
        "cookie_file_import": "Import cookies.txt…",
        "cookie_file_remove": "Remove",
        "cookie_file_dialog": "Choose cookies.txt (Netscape format)",
        "cookie_file_bad": "The cookies.txt file is invalid or contains no youtube.com cookies.",
        "cookie_file_imported": "YouTube cookies were imported. The app will use them instead of reading the browser directly.",
        "files": "Files",
        "embed_meta": "Embed metadata",
        "embed_thumb": "Embed thumbnail/cover in audio",
        "no_overwrite": "Do not overwrite existing files",
        "opera_profile_required": "For Opera GX, select a profile directory.",
        "default_folder_empty": "The default folder cannot be empty.",
        "settings_error": "Settings error",
        "choose_folder": "Choose folder",
        "choose_destination": "Choose destination folder",
        "choose_profile_dialog": "Choose profile",
        "audio_quality_tip": "M4A/Opus use the original YouTube audio stream without unnecessary re-encoding. Shown bitrates are approximate.",
        "mp3_quality_tip": "MP3: VBR uses LAME V0/V2/V4 quality levels, while Bitrate sets 320/256/192/128 kb/s. Re-encoding cannot improve the YouTube source quality.",
        "m4a_quality_tip": "M4A/AAC: Premium/high (~256 kb/s) requires Premium access/cookies. Standard is about 128 kb/s. DRC falls back to the standard stream when DRC is unavailable for the selected quality.",
        "premium_256": "Premium / high (~256 kb/s)",
        "standard_128": "Standard (~128 kb/s)",
        "audio_variant": "Variant:",
        "audio_variant_standard": "Standard",
        "audio_variant_drc": "DRC",
        "drc_fallback_status": "Done • standard (DRC unavailable)",
        "drc_fallback_desc": "DRC was requested, but DRC was unavailable for this quality — the standard stream was downloaded.",
        "format_variant": "Variant",
        "format_variant_standard": "Standard",
        "format_variant_drc": "DRC",
        "format_checker": "Check formats:",
        "format_checker_paste_hint": "paste a link to inspect",
        "check_formats": "Check formats",
        "checking_formats": "Checking…",
        "format_check_title": "Available formats — YT-Downloader",
        "format_check_failed": "Could not inspect available formats.",
        "format_check_audio": "Audio only",
        "format_check_video": "Video only",
        "format_check_av": "Video + audio",
        "format_check_source": "Item: {title}",
        "format_check_m4a": "Best M4A/AAC: {quality}",
        "format_check_opus": "Best Opus: {quality}",
        "format_check_video_max": "Best video: {quality}",
        "format_check_none": "none",
        "format_check_merge_note": "The tables show raw audio and video formats available from YouTube. Click a column header to sort; right-click a row to use its ID in the advanced -f selector.",
        "format_check_used_music": "YouTube Music was also checked for audio so Premium streams can be detected.",
        "variant": "Exact variant:",
        "variant_auto": "Automatic / rules above",
        "variant_hint": "Click “Check formats” first to load the exact variants/IDs for this link.",
        "variant_exact_tip": "Exact variant from the format analysis. When enabled it overrides the smart selection rules.",
        "exact_variant_toggle": "Use exact variant/ID",
        "video_rules": "Video",
        "audio_rules": "Audio",
        "container": "Container:",
        "resolution": "Resolution:",
        "fps": "FPS:",
        "codec": "Codec:",
        "video_tier": "Quality tier:",
        "video_bitrate": "Bitrate:",
        "auto_choice": "Automatic",
        "best_available": "Best available",
        "standard_video": "Standard",
        "premium_video": "Premium / enhanced",
        "h264": "H.264 / AVC",
        "vp9": "VP9",
        "av1": "AV1",
        "hevc": "H.265 / HEVC",
        "audio_format": "Format:",
        "audio_bitrate": "Bitrate:",
        "audio_mode": "Mode:",
        "audio_mode_vbr": "VBR",
        "audio_mode_bitrate": "Bitrate",
        "mp3_v0": "V0 — best",
        "mp3_v2": "V2",
        "mp3_v4": "V4",
        "advanced_title": "Advanced yt-dlp",
        "advanced_selector": "Format selector (-f):",
        "advanced_selector_hint": "Empty = use the tiles. A custom -f overrides automatic format selection.",
        "advanced_args": "Additional arguments:",
        "advanced_args_hint": "Optional yt-dlp arguments, e.g. --remux-video mkv. The app still controls the URL, destination, cookies and queue.",
        "advanced_preview": "Command preview:",
        "advanced_copy": "Copy",
        "advanced_bad_args": "Invalid additional yt-dlp arguments — check quotation marks.",
        "format_pick_id": "Use ID in -f",
        "format_add_id": "Add ID to -f",
        "smart_rules_hint": "After “Check formats”, the lists are narrowed to combinations actually available for the material. For playlists they act as rules resolved separately for each video.",
        "bitrate_single_only": "Exact bitrate is used only for a single, previously checked video. For playlists it stays Automatic.",
        "format_check_container": "Container",
        "video_quality_tip": "Best selects the highest available quality. Other values set the maximum video height.",
        "numbers_bad": 'Enter numbers like "1,3,5-7,10".',
        "range_bad": "Invalid range: {x}",
        "numbers_start": "Item numbers start at 1.",
        "no_items_after_omit": "No items remain after skipping the selected numbers.",
        "e01": "Invalid or unsupported URL.",
        "e02": "The item requires login, or the playlist is private/unavailable without cookies.",
        "e03": "The item is unavailable.",
        "e04": "The selected format or quality is unavailable.",
        "e05": "Network connection problem.",
        "e06": "Could not read cookies from the browser.",
        "cookie_locked_windows": "Windows is locking the {browser} cookie database. Fully close the browser (including background processes) and try again.",
        "cookie_dpapi_windows": "{browser} is using Chromium App-Bound/DPAPI cookie encryption. Closing the browser will not fix it. In Settings, use the Chromium extension, load it in your browser, start YT-Downloader and sync cookies. Firefox or a manual cookies.txt file can also be used.",
        "e07": "FFmpeg or post-processing error.",
        "e08": "Could not save the file.",
        "e99": "Unknown yt-dlp/FFmpeg error.",
        "playlist_no_access": "Could not access the playlist. If it is private, select cookies from a browser signed in to YouTube.",
        "not_playlist": "The link was not recognized as an accessible playlist.",
        "app_unknown": "Unknown application error.",
        "error_hint": "Right-click → Show description / Show log",
        "warning_hint": "Right-click → Show description / Show log",
        "partial_playlist": "The playlist was downloaded partially. Number of items that ended with an error: {n}. The remaining available items were downloaded successfully.",
        "queue_extended": "New items were added during downloading — continuing the queue.",
        "remove_from_queue": "Remove from queue",
        "remove_waiting_only": "While downloading, only items with Waiting status can be removed.",
        "stats_downloaded": "Media downloaded: {done}",
        "stats_work": "Time: {elapsed}",
        "stats_eta": "Remaining: {eta}",
        "stats_eta_calc": "calculating…",
        "stats_eta_more": "{eta} (+{n} to analyze)",
        "stats_idle_eta": "—",
        "save": "Save",
        "cancel": "Cancel",
        "open": "Open",
        "close": "Close",
        "shortcuts_group": "Shortcuts — Linux portable",
        "shortcuts_note": "Optional integration. The app and its data still stay in the portable folder.",
        "shortcut_menu": "Add to application menu",
        "shortcut_desktop": "Add desktop shortcut",
        "shortcut_remove": "Remove shortcuts",
        "shortcut_menu_created": "Application-menu shortcut created.",
        "shortcut_desktop_created": "Desktop shortcut created.",
        "shortcut_removed": "YT-Downloader shortcuts removed.",
        "shortcut_failed": "Could not change shortcuts: {error}",
        "profile_hint": "optional profile directory; required for Opera GX",
        "path_hint": "subfolder or absolute path",
        "interface_group": "Interface",
        "theme": "Theme:",
        "theme_system": "Follow system",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "show_history": "Show download history",
        "confirm_clear_history": "Ask for confirmation before clearing history",
        "show_current_log": "Show current log",
        "show_presets": "Show presets",
        "show_advanced": "Show advanced yt-dlp",
        "panel_rows_title": "Visible panel rows",
        "panel_rows_min": "Min.",
        "panel_rows_max": "Max.",
        "panel_rows_queue": "Queue",
        "panel_rows_history": "History",
        "panel_rows_log": "Log",
        "panel_rows_hint": "A panel can shrink to the minimum and grow only up to the configured maximum number of rows.",
        "panel_rows_error": "For the “{panel}” panel, the minimum value cannot be greater than the maximum.",
        "preset": "Preset:",
        "preset_custom": "Custom",
        "preset_save_as": "Save as…",
        "preset_delete": "Delete preset",
        "preset_name": "Preset name",
        "preset_name_prompt": "Enter a name for the new preset:",
        "preset_overwrite": "A preset with this name already exists. Overwrite it?",
        "preset_delete_confirm": "Delete preset “{name}”?",
        "preset_music_m4a": "Music M4A",
        "preset_music_mp3": "Music MP3",
        "preset_video_1080": "Video 1080p MP4",
        "preset_video_best": "Video — best quality",
        "preset_original": "Best / no conversion",
        "subtitles": "Subtitles:",
        "sub_none": "None",
        "sub_manual": "Manual",
        "sub_auto": "Automatic",
        "sub_fallback": "Manual, otherwise → automatic",
        "sub_language": "Language:",
        "sub_polish": "Polish",
        "sub_english": "English",
        "sub_output": "Save:",
        "sub_separate": "Separate file",
        "sub_embed": "Embed in video",
        "sub_both": "Embed + separate file",
        "history": "Download history",
        "history_date": "Date",
        "history_mode": "Mode",
        "history_result": "Result",
        "history_time": "Time",
        "refresh": "Refresh",
        "clear_history": "Clear history",
        "clear_history_confirm": "Delete saved download history?",
        "history_empty": "No saved download history.",
        "queue_restored": "Restored saved queue: {n} items.",
        "updates_group": "Updates",
        "check_components": "Check for updates",
        "update_components": "Update from GitHub…",
        "current_version": "Current version: {version}",
        "latest_version": "Latest version: {version}",
        "update_available": "Version {version} is available.",
        "update_none": "You already have the latest version ({version}).",
        "update_no_release": "No public GitHub Release was found. In-app updates will work after a Release is published.",
        "update_not_appimage": "Automatic replacement is available for the AppImage build. The GitHub Releases page was opened.",
        "update_confirm": "Download and install YT-Downloader {version}? Your data/ folder with settings and history will not be changed.",
        "update_downloaded": "Version {version} was installed. Close and reopen YT-Downloader.",
        "update_failed": "Update failed: {error}",
    },
}

def tr(lang, key, **kwargs):
    text = TR.get(lang, TR["pl"]).get(key, key)
    return text.format(**kwargs) if kwargs else text


def choose_directory_dialog(parent, title, start_dir, lang):
    """Use a host-native folder chooser whenever possible.

    On KDE prefer kdialog, on GNOME-like desktops prefer zenity when present.
    This lets the AppImage use the desktop's own file chooser instead of the
    bundled Qt dialog.  Fall back to QFileDialog if no host helper exists.
    """
    start = str(Path(start_dir).expanduser())

    if os.name != "nt":
        desktop = (
            (os.environ.get("XDG_CURRENT_DESKTOP") or "") + " " +
            (os.environ.get("DESKTOP_SESSION") or "")
        ).lower()

        candidates = []
        if "kde" in desktop or "plasma" in desktop:
            candidates.append(("kdialog", ["--getexistingdirectory", start, "--title", title]))
        if any(x in desktop for x in ("gnome", "cinnamon", "mate", "xfce")):
            filename = start.rstrip("/") + "/"
            candidates.append(("zenity", ["--file-selection", "--directory", f"--filename={filename}", f"--title={title}"]))
        candidates.extend([
            ("kdialog", ["--getexistingdirectory", start, "--title", title]),
            ("zenity", ["--file-selection", "--directory", f"--filename={start.rstrip('/')}/", f"--title={title}"]),
        ])

        seen = set()
        for tool, args in candidates:
            if tool in seen:
                continue
            seen.add(tool)
            exe = shutil.which(tool)
            if not exe:
                continue
            try:
                proc = subprocess.run(
                    [exe, *args],
                    text=True,
                    capture_output=True,
                    env=external_subprocess_env(),
                    creationflags=NO_WINDOW,
                )
                if proc.returncode == 0:
                    selected = (proc.stdout or "").strip()
                    if selected:
                        return selected
                if proc.returncode in (1, 255):
                    return ""
            except Exception:
                pass

    options = QFileDialog.Option.ShowDirsOnly
    return QFileDialog.getExistingDirectory(
        parent, title, start, options=options
    )


def open_local_folder(path):
    """Open a folder through the host desktop, including from AppImage."""
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(target))
        return True

    env = external_subprocess_env()
    host_path = env.get("PATH", os.environ.get("PATH", ""))
    for name, args in (("xdg-open", [str(target)]), ("gio", ["open", str(target)])):
        exe = shutil.which(name, path=host_path)
        if not exe:
            continue
        try:
            subprocess.Popen(
                [exe, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
            return True
        except Exception:
            pass
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def _portable_launcher_path():
    """Return a stable portable launcher/AppImage path for .desktop files."""
    if os.name == "nt":
        return None
    if _APPIMAGE_PATH:
        p = Path(_APPIMAGE_PATH).expanduser().resolve()
        if p.is_file():
            return p
    if _PORTABLE_ROOT_ENV:
        root = Path(_PORTABLE_ROOT_ENV).expanduser().resolve()
        for name in ("YT-Downloader.AppImage", "YT-Downloader"):
            p = root / name
            if p.is_file():
                return p
    return None


def _desktop_directory():
    env = external_subprocess_env()
    host_path = env.get("PATH", os.environ.get("PATH", ""))
    xdg_user_dir = shutil.which("xdg-user-dir", path=host_path)
    if xdg_user_dir:
        try:
            proc = subprocess.run(
                [xdg_user_dir, "DESKTOP"],
                capture_output=True,
                text=True,
                timeout=3,
                env=env,
            )
            value = (proc.stdout or "").strip()
            if proc.returncode == 0 and value:
                return Path(value).expanduser()
        except Exception:
            pass
    for candidate in (Path.home() / "Pulpit", Path.home() / "Desktop"):
        if candidate.is_dir():
            return candidate
    return Path.home() / "Desktop"


def _desktop_exec_quote(value):
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{text}"'


def _portable_shortcut_targets():
    return {
        "menu": Path.home() / ".local" / "share" / "applications" / "yt-downloader.desktop",
        "desktop": _desktop_directory() / "YT-Downloader.desktop",
    }


def create_portable_shortcut(kind):
    if os.name == "nt":
        raise RuntimeError("Linux shortcuts are not available on Windows")
    launcher = _portable_launcher_path()
    if launcher is None:
        raise RuntimeError("portable AppImage/launcher path is unavailable")
    if DATA_ROOT is None:
        raise RuntimeError("portable data directory is unavailable")

    integration_dir = DATA_ROOT / "integration"
    integration_dir.mkdir(parents=True, exist_ok=True)
    icon_target = integration_dir / "yt-downloader.png"
    if APP_ICON.is_file():
        shutil.copy2(APP_ICON, icon_target)

    targets = _portable_shortcut_targets()
    if kind not in targets:
        raise ValueError(kind)
    target = targets[kind]
    target.parent.mkdir(parents=True, exist_ok=True)
    desktop = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=YT-Downloader",
        "Comment=GUI downloader for yt-dlp",
        f"Exec={_desktop_exec_quote(launcher)}",
        f"Icon={icon_target}",
        "Terminal=false",
        "Categories=AudioVideo;Network;",
        "StartupNotify=true",
        "",
    ])
    target.write_text(desktop, encoding="utf-8")
    target.chmod(0o755)
    return target


def remove_portable_shortcuts():
    for target in _portable_shortcut_targets().values():
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
    if DATA_ROOT is not None:
        integration_dir = DATA_ROOT / "integration"
        try:
            (integration_dir / "yt-downloader.png").unlink(missing_ok=True)
            integration_dir.rmdir()
        except Exception:
            pass

BROWSERS = [("none", "no_login")]
if os.name == "nt":
    BROWSERS.append(("chromium-extension", "chromium_ext_browser"))
BROWSERS += [
    ("brave", "Brave"),
    ("firefox", "Firefox"),
    ("chrome", "Google Chrome"),
    ("chromium", "Chromium"),
    ("edge", "Microsoft Edge"),
    ("opera", "Opera"),
    ("opera-gx", "opera_custom"),
    ("vivaldi", "Vivaldi"),
]
KEYRINGS = [("", "auto")] if os.name == "nt" else [
    ("", "auto"),
    ("kwallet6", "kwallet6"),
    ("kwallet5", "kwallet5"),
    ("kwallet", "kwallet_legacy"),
    ("gnomekeyring", "GNOME Keyring"),
    ("basictext", "Basic text"),
]

AUDIO_FORMAT_IDS = ["original", "m4a", "opus", "mp3", "flac"]
VIDEO_FORMAT_IDS = ["original", "mp4", "webm", "mkv"]
AV_FORMAT_IDS = ["auto", "mp4", "mkv", "webm"]
VIDEO_QUALITY_IDS = ["best", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
AUDIO_QUALITY_IDS = ["best", "256", "128"]
MP3_VBR_IDS = ["v0", "v2", "v4"]
MP3_BITRATE_IDS = ["320", "256", "192", "128"]
M4A_QUALITY_IDS = ["best", "256", "128"]
AUDIO_VARIANT_IDS = ["standard", "drc"]
ITEM_RE = re.compile(r"^[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*$")

def format_label(lang, media, fmt):
    if fmt == "original":
        return tr(lang, "original")
    if fmt == "auto":
        return tr(lang, "automatic")
    if fmt == "m4a":
        return "M4A / AAC"
    if fmt == "opus":
        return "Opus"
    if fmt == "mp3":
        return "MP3"
    if fmt == "flac":
        return "FLAC"
    return fmt.upper()

def quality_label(lang, media, q):
    if q == "best":
        return tr(lang, "best")
    if media == "audio" and q in MP3_VBR_IDS:
        return q.upper()
    if media == "audio":
        return f"{q} kb/s"
    return q

def media_label(lang, media):
    return {
        "audio": tr(lang, "audio_only"),
        "video": tr(lang, "video_only"),
        "av": tr(lang, "audio_video"),
    }[media]

class AppError(Exception):
    def __init__(self, code, desc, details=""):
        super().__init__(desc)
        self.code = code
        self.desc = desc
        self.details = details

FACTORY_PRESETS = [
    {"id": "factory-music-m4a", "name_key": "preset_music_m4a",
     "media": "audio", "fmt": "m4a", "quality": "best", "audio_variant": "standard",
     "subtitle_mode": "none", "subtitle_lang": "pl", "subtitle_output": "separate"},
    {"id": "factory-music-mp3", "name_key": "preset_music_mp3",
     "media": "audio", "fmt": "mp3", "quality": "v0", "audio_quality_mode": "vbr",
     "subtitle_mode": "none", "subtitle_lang": "pl", "subtitle_output": "separate"},
    {"id": "factory-video-1080", "name_key": "preset_video_1080",
     "media": "av", "fmt": "mp4", "quality": "1080",
     "smart_video_container": "mp4", "smart_video_resolution": "1080",
     "smart_video_fps": "auto", "smart_video_codec": "h264",
     "smart_video_tier": "auto", "smart_video_bitrate": "auto",
     "smart_audio_format": "m4a", "smart_audio_quality": "best", "smart_audio_variant": "standard",
     "subtitle_mode": "none", "subtitle_lang": "pl", "subtitle_output": "separate"},
    {"id": "factory-video-best", "name_key": "preset_video_best",
     "media": "av", "fmt": "auto", "quality": "best",
     "smart_video_container": "auto", "smart_video_resolution": "auto",
     "smart_video_fps": "auto", "smart_video_codec": "auto",
     "smart_video_tier": "auto", "smart_video_bitrate": "auto",
     "smart_audio_format": "m4a", "smart_audio_quality": "best", "smart_audio_variant": "standard",
     "subtitle_mode": "none", "subtitle_lang": "pl", "subtitle_output": "separate"},
    {"id": "factory-original", "name_key": "preset_original",
     "media": "audio", "fmt": "original", "quality": "best",
     "subtitle_mode": "none", "subtitle_lang": "pl", "subtitle_output": "separate"},
]


def fresh_factory_presets():
    return [dict(x) for x in FACTORY_PRESETS]


def preset_display_name(preset, lang):
    key = preset.get("name_key")
    if key and key in TR.get(lang, {}):
        return tr(lang, key)
    return str(preset.get("name") or preset.get("id") or "Preset")


@dataclass
class Item:
    playlist: bool
    destination: str
    url: str
    media: str
    fmt: str
    quality: str
    selection: str = ""
    numbers: str = ""
    subtitle_mode: str = "none"
    subtitle_lang: str = "pl"
    subtitle_output: str = "separate"
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    duration_s: float = 0.0
    entries_count: int = 1
    exact_selector: str = ""
    exact_label: str = ""
    exact_merge: str = ""
    exact_use_music: bool = False
    smart_video_container: str = "auto"
    smart_video_resolution: str = "auto"
    smart_video_fps: str = "auto"
    smart_video_codec: str = "auto"
    smart_video_tier: str = "auto"
    smart_video_bitrate: str = "auto"
    smart_audio_format: str = "auto"
    smart_audio_quality: str = "best"
    smart_audio_variant: str = "standard"
    audio_quality_mode: str = "vbr"
    audio_variant: str = "standard"
    advanced_selector: str = ""
    advanced_args: str = ""


def profile_key(it):
    return (it.media, it.fmt, it.quality)


def human_time(seconds):
    try:
        seconds = max(0, int(round(float(seconds))))
    except Exception:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min {s:02d} s"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"



def minimum_table_height(table, rows=3):
    """Wysokość tabeli odpowiadająca nagłówkowi i wskazanej liczbie wierszy."""
    try:
        header_h = max(22, table.horizontalHeader().sizeHint().height())
    except Exception:
        header_h = 24
    try:
        row_h = max(22, table.verticalHeader().defaultSectionSize())
    except Exception:
        row_h = 28
    try:
        frame = max(0, table.frameWidth()) * 2
    except Exception:
        frame = 2
    return int(header_h + row_h * max(1, int(rows)) + frame + 6)


def text_rows_height(editor, rows=3):
    """Przybliżona wysokość QPlainTextEdit dla podanej liczby widocznych linii."""
    try:
        line_h = max(16, editor.fontMetrics().lineSpacing())
    except Exception:
        line_h = 20
    try:
        frame = max(0, editor.frameWidth()) * 2
    except Exception:
        frame = 2
    # Kilka pikseli zapasu na margines dokumentu i antyaliasing fontu.
    return int(line_h * max(1, int(rows)) + frame + 10)



class StatsDB:
    MAX_HISTORY = 2000

    def __init__(self, path=STATS_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ratio_cache = {}
        self._init_db()

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def _init_db(self):
        with self.connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    media TEXT NOT NULL,
                    fmt TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    duration REAL NOT NULL,
                    elapsed REAL NOT NULL
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS aggregate (
                    media TEXT NOT NULL,
                    fmt TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    samples INTEGER NOT NULL DEFAULT 0,
                    total_duration REAL NOT NULL DEFAULT 0,
                    total_elapsed REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(media, fmt, quality)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    playlist INTEGER NOT NULL,
                    media TEXT NOT NULL,
                    fmt TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    status TEXT NOT NULL,
                    code TEXT NOT NULL,
                    duration REAL NOT NULL DEFAULT 0,
                    elapsed REAL NOT NULL DEFAULT 0,
                    error_desc TEXT NOT NULL DEFAULT '',
                    error_log TEXT NOT NULL DEFAULT ''
                )"""
            )
            # Database migration for histories created by 0.3.x / 0.4.x.
            cols = {row[1] for row in con.execute("PRAGMA table_info(download_history)")}
            if "error_desc" not in cols:
                con.execute("ALTER TABLE download_history ADD COLUMN error_desc TEXT NOT NULL DEFAULT ''")
            if "error_log" not in cols:
                con.execute("ALTER TABLE download_history ADD COLUMN error_log TEXT NOT NULL DEFAULT ''")

    def record(self, it, duration, elapsed):
        duration = float(duration or 0)
        elapsed = float(elapsed or 0)
        if duration < 20 or elapsed < 0.5:
            return
        media, fmt, quality = profile_key(it)
        self._ratio_cache.clear()
        with self.connect() as con:
            con.execute(
                "INSERT INTO history(ts,media,fmt,quality,duration,elapsed) VALUES(?,?,?,?,?,?)",
                (int(time.time()), media, fmt, quality, duration, elapsed),
            )
            con.execute(
                """INSERT INTO aggregate(media,fmt,quality,samples,total_duration,total_elapsed)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(media,fmt,quality) DO UPDATE SET
                     samples=samples+1,
                     total_duration=total_duration+excluded.total_duration,
                     total_elapsed=total_elapsed+excluded.total_elapsed""",
                (media, fmt, quality, 1, duration, elapsed),
            )
            con.execute(
                "DELETE FROM history WHERE id NOT IN "
                "(SELECT id FROM history ORDER BY id DESC LIMIT ?)",
                (self.MAX_HISTORY,),
            )
        # Detailed history is capped; aggregate statistics stay tiny.
        try:
            if self.path.stat().st_size > 10 * 1024 * 1024:
                with self.connect() as con:
                    con.execute("VACUUM")
        except Exception:
            pass

    def record_download(self, it, status, code, duration, elapsed, destination=None, error_desc="", error_log=""):
        with self.connect() as con:
            con.execute(
                """INSERT INTO download_history(
                    ts,url,destination,playlist,media,fmt,quality,status,code,duration,elapsed,error_desc,error_log
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(time.time()), it.url, destination or it.destination, 1 if it.playlist else 0,
                    it.media, it.fmt, it.quality, status, code or "",
                    float(duration or 0), float(elapsed or 0), error_desc or "", error_log or "",
                ),
            )
            con.execute(
                "DELETE FROM download_history WHERE id NOT IN "
                "(SELECT id FROM download_history ORDER BY id DESC LIMIT 2000)"
            )

    def download_history(self, limit=500):
        with self.connect() as con:
            return con.execute(
                """SELECT id,ts,url,destination,playlist,media,fmt,quality,status,code,duration,elapsed,error_desc,error_log
                   FROM download_history ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()

    def clear_download_history(self):
        with self.connect() as con:
            con.execute("DELETE FROM download_history")

    def ratio(self, it):
        media, fmt, quality = profile_key(it)
        key = (media, fmt, quality)
        if key in self._ratio_cache:
            return self._ratio_cache[key]
        try:
            with self.connect() as con:
                rows = con.execute(
                    "SELECT duration,elapsed FROM history "
                    "WHERE media=? AND fmt=? AND quality=? "
                    "ORDER BY id DESC LIMIT 30",
                    (media, fmt, quality),
                ).fetchall()
                ratios = [e / d for d, e in rows if d and e and d > 0]
                if ratios:
                    ratios.sort()
                    mid = len(ratios) // 2
                    if len(ratios) % 2:
                        value = ratios[mid]
                    else:
                        value = (ratios[mid - 1] + ratios[mid]) / 2
                    self._ratio_cache[key] = value
                    return value

                row = con.execute(
                    "SELECT SUM(total_duration),SUM(total_elapsed) FROM aggregate WHERE media=?",
                    (media,),
                ).fetchone()
                if row and row[0] and row[1] and row[0] > 0:
                    value = row[1] / row[0]
                    self._ratio_cache[key] = value
                    return value
        except Exception:
            return None
        self._ratio_cache[key] = None
        return None


def migrate_cfg(cfg):
    fmap = {
        "Oryginalny — bez konwersji": "original",
        "Oryginalny": "original",
        "Original": "original",
        "Najlepszy": "original",
        "Best": "original",
        "Automatyczny": "auto",
        "Automatic": "auto",
        "M4A / AAC": "m4a",
        "Opus": "opus",
        "MP3": "mp3",
        "FLAC": "flac",
        "WebM": "webm",
        "MP4": "mp4",
        "MKV": "mkv",
    }
    qmap = {
        "Najlepsza": "best",
        "Best": "best",
        "320 kb/s": "320",
        "256 kb/s": "256",
        "~256 kb/s": "256",
        "Premium / high (~256 kb/s)": "256",
        "192 kb/s": "192",
        "128 kb/s": "128",
        "~128 kb/s": "128",
        "Standard (~128 kb/s)": "128",
    }
    for k in ("last_format_audio", "last_format_video", "last_format_av"):
        if cfg.get(k) in fmap:
            cfg[k] = fmap[cfg[k]]
    for k in ("last_audio_quality", "last_video_quality"):
        if cfg.get(k) in qmap:
            cfg[k] = qmap[cfg[k]]
    return cfg

def load_cfg():
    cfg = dict(
        language="en",
        default_download_dir=DEFAULT_DIR,
        default_subfolder="",
        default_playlist=False,
        cookies_browser="none",
        cookies_profile="",
        cookies_keyring="",
        cookies_file_enabled=False,
        embed_metadata=True,
        embed_thumbnail=True,
        no_overwrites=True,
        show_history=True,
        confirm_clear_history=False,
        show_log=True,
        show_presets=True,
        show_advanced=True,
        theme="system",
        window_width=697,
        window_height=932,
        settings_width=638,
        settings_height=752,
        queue_rows_min=4,
        queue_rows_max=5,
        history_rows_min=3,
        history_rows_max=4,
        log_rows_min=3,
        log_rows_max=4,
        last_subtitle_mode="none",
        last_subtitle_lang="en",
        last_subtitle_output="separate",
        presets_initialized=False,
        presets=[],
        last_media="av",
        last_format_audio="m4a",
        last_format_video="original",
        last_format_av="auto",
        last_audio_quality="best",
        last_audio_variant="standard",
        last_video_quality="best",
        smart_video_container="auto",
        smart_video_resolution="auto",
        smart_video_fps="auto",
        smart_video_codec="auto",
        smart_video_tier="auto",
        smart_video_bitrate="auto",
        smart_av_audio_format="m4a",
        smart_av_audio_quality="best",
        smart_av_audio_variant="standard",
        last_mp3_quality_mode="vbr",
        last_mp3_vbr="v0",
        last_mp3_bitrate="320",
        advanced_selector="",
        advanced_args="",
        advanced_expanded=False,
    )
    src = CFG_FILE if CFG_FILE.exists() else OLD_CFG
    try:
        if src.exists():
            d = json.loads(src.read_text(encoding="utf-8"))
            if "library_root" in d and "default_download_dir" not in d:
                d["default_download_dir"] = d["library_root"]
            cfg.update(d)
    except Exception:
        pass
    cfg = migrate_cfg(cfg)
    if not cfg.get("presets_initialized", False):
        cfg["presets"] = fresh_factory_presets()
        cfg["presets_initialized"] = True
    if not isinstance(cfg.get("presets"), list):
        cfg["presets"] = []
    # Keep factory presets current without touching user-created presets.
    factories = {p["id"]: p for p in fresh_factory_presets()}
    for i, preset in enumerate(list(cfg.get("presets", []))):
        if isinstance(preset, dict) and preset.get("id") in factories and preset.get("name_key"):
            cfg["presets"][i] = dict(factories[preset["id"]])

    # Limity wysokości dotyczą wyłącznie trzech dolnych segmentów.
    # Fabryczny układ interfejsu 0.4.41: kolejka 4–5, historia 3–4, log 3–4.
    row_defaults = {"queue": (4, 5), "history": (3, 4), "log": (3, 4)}
    for prefix, (default_min, default_max) in row_defaults.items():
        min_key = f"{prefix}_rows_min"
        max_key = f"{prefix}_rows_max"
        try:
            min_rows = int(cfg.get(min_key, default_min))
        except Exception:
            min_rows = default_min
        try:
            max_rows = int(cfg.get(max_key, default_max))
        except Exception:
            max_rows = default_max
        min_rows = max(1, min(50, min_rows))
        max_rows = max(1, min(50, max_rows))
        if min_rows > max_rows:
            max_rows = min_rows
        cfg[min_key] = min_rows
        cfg[max_key] = max_rows

    cfg.pop("ui_spacing", None)
    cfg.pop("window_geometry", None)
    cfg.pop("window_x", None)
    cfg.pop("window_y", None)
    return cfg

def save_cfg(cfg):
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    CFG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def _auto_linux_keyring():
    """Return the keyring that yt-dlp is likely to auto-detect, for diagnostics only.

    Do NOT use this value to build --cookies-from-browser in automatic mode.
    Upstream yt-dlp already detects KDE/GNOME and Chromium's keyring rules, and
    keeping the browser spec bare matches normal system yt-dlp behavior.
    """
    if os.name == "nt":
        return ""

    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    session = (os.environ.get("DESKTOP_SESSION") or "").lower()
    kde_version = (os.environ.get("KDE_SESSION_VERSION") or "").strip()
    is_kde = (
        "kde" in desktop or "plasma" in desktop or
        "kde" in session or "plasma" in session or
        bool(os.environ.get("KDE_FULL_SESSION"))
    )
    if is_kde:
        if kde_version == "6":
            return "kwallet6"
        if kde_version == "5":
            return "kwallet5"
        return "kwallet"
    if any(x in desktop for x in ("gnome", "cinnamon", "unity", "pantheon")) or any(
        x in session for x in ("gnome", "cinnamon", "unity")
    ):
        return "gnomekeyring"
    return ""


def _resolved_cookie_keyring(cfg):
    """Return only a keyring explicitly selected by the user.

    An empty value means true yt-dlp automatic detection.  This is intentional:
    forcing kwallet6 from the GUI changed behavior compared with system yt-dlp
    and can be wrong when Chromium/Brave was started with another backend.
    """
    if os.name == "nt":
        return ""
    return str(cfg.get("cookies_keyring", "") or "").strip().lower()

def _keyring_display_name(keyring, lang="pl"):
    names = {
        "kwallet6": "KWallet 6",
        "kwallet5": "KWallet 5",
        "kwallet": tr(lang, "kwallet_legacy"),
        "gnomekeyring": "GNOME Keyring",
        "basictext": "Basic text",
        "": tr(lang, "auto"),
    }
    return names.get(str(keyring or "").lower(), str(keyring or tr(lang, "auto")))


def is_cookie_read_error(text):
    value = (text or "").lower()
    needles = (
        "failed to decrypt", "could not decrypt", "cookie database",
        "kwallet-query", "safe storage", "keyring", "cookies from browser",
        "failed to read from keyring", "failed to read networkwallet",
    )
    return any(n in value for n in needles)


def cookie_error_description(cfg, lang):
    browser = browser_display_name(cfg)
    keyring = _resolved_cookie_keyring(cfg)
    if os.name != "nt":
        if keyring:
            return tr(lang, "cookie_linux_read_failed", browser=browser, keyring=_keyring_display_name(keyring, lang))
        auto_hint = _auto_linux_keyring()
        detected = _keyring_display_name(auto_hint, lang) if auto_hint else tr(lang, "auto")
        return tr(lang, "cookie_linux_auto_failed", browser=browser, detected=detected)
    return tr(lang, "e06")


def _cookie_browser_spec(cfg):
    lang = cfg.get("language", "en")
    b = cfg.get("cookies_browser", "none")
    if b in {"none", "chromium-extension"}:
        return ""

    profile = cfg.get("cookies_profile", "").strip()
    kr = "" if os.name == "nt" else _resolved_cookie_keyring(cfg)

    if b == "opera-gx":
        if not profile:
            raise AppError("E06", tr(lang, "opera_profile_required"))
        b = "opera"

    spec = b
    if kr and b in {"brave", "chrome", "chromium", "edge", "opera", "vivaldi"}:
        spec += "+" + kr
    if profile:
        spec += ":" + profile
    return spec


def _filter_youtube_cookie_lines(lines):
    out = ["# Netscape HTTP Cookie File", "# YT-Downloader: YouTube-only cookie import"]
    kept = 0
    for raw in lines:
        line = raw.rstrip("\\r\\n")
        if not line:
            continue
        parsed_line = line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line
        if parsed_line.startswith("#"):
            continue
        parts = parsed_line.split("\\t")
        if len(parts) < 7:
            continue
        domain = parts[0].lstrip(".").lower()
        if domain == "youtube.com" or domain.endswith(".youtube.com"):
            out.append(line)
            kept += 1
    return out, kept


def import_manual_cookie_file(source):
    """Import a Netscape cookie file and retain only YouTube domains."""
    src = Path(source)
    if not src.is_file():
        return False
    try:
        raw = src.read_text(encoding="utf-8", errors="ignore")
        first = next((x.strip() for x in raw.splitlines() if x.strip()), "")
        if first not in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}:
            return False
        out, kept = _filter_youtube_cookie_lines(raw.splitlines())
        if not kept:
            return False
        MANUAL_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MANUAL_COOKIE_FILE.write_bytes(("\\r\\n".join(out) + "\\r\\n").encode("utf-8"))
        return True
    except Exception:
        return False


def _manual_cookie_enabled(cfg):
    return bool(cfg.get("cookies_file_enabled", False) and _valid_cookie_cache(MANUAL_COOKIE_FILE))


def _extension_cookie_enabled(cfg):
    # Preferred explicit mode is "chromium-extension". Keep legacy "brave"
    # compatibility for users upgrading from older Windows portable builds.
    return bool(
        os.name == "nt"
        and cfg.get("cookies_browser", "none") in {"chromium-extension", "brave"}
        and _valid_cookie_cache(EXTENSION_COOKIE_FILE)
    )


class _BraveCookieBridgeHandler(BaseHTTPRequestHandler):
    server_version = f"YTDownloaderBridge/{VERSION}"

    def log_message(self, format, *args):
        # Never print cookie-bearing HTTP requests to stdout/stderr.
        return

    def _origin_ok(self):
        return self.headers.get("Origin", "") == BRAVE_EXTENSION_ORIGIN

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", BRAVE_EXTENSION_ORIGIN)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if self.path != "/sync" or not self._origin_ok():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", BRAVE_EXTENSION_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_POST(self):
        if self.path != "/sync":
            self._send_json(404, {"ok": False, "message": "Nieznana ścieżka."})
            return
        if not self._origin_ok():
            self._send_json(403, {"ok": False, "message": "Niedozwolone źródło rozszerzenia."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 2 * 1024 * 1024:
            self._send_json(413, {"ok": False, "message": "Nieprawidłowy rozmiar danych."})
            return

        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("action") != "sync":
                raise ValueError("Nieobsługiwana operacja.")
            text = str(payload.get("text") or "")
            out, kept = _filter_youtube_cookie_lines(text.splitlines())
            if kept <= 0:
                self._send_json(400, {"ok": False, "count": 0, "message": "Brak cookies youtube.com do zapisania."})
                return

            COOKIE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = EXTENSION_COOKIE_FILE.with_suffix(EXTENSION_COOKIE_FILE.suffix + ".tmp")
            tmp.write_bytes(("\r\n".join(out) + "\r\n").encode("utf-8"))
            os.replace(tmp, EXTENSION_COOKIE_FILE)
            self._send_json(200, {
                "ok": True,
                "count": kept,
                "message": "Cookies YouTube zapisane lokalnie w YT-Downloaderze."
            })
        except Exception as exc:
            self._send_json(400, {"ok": False, "count": 0, "message": str(exc)})


def start_brave_cookie_bridge():
    """Start a loopback-only cookie bridge for the bundled Brave extension."""
    if os.name != "nt":
        return None
    try:
        server = ThreadingHTTPServer((BRAVE_BRIDGE_HOST, BRAVE_BRIDGE_PORT), _BraveCookieBridgeHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="BraveCookieBridge", daemon=True)
        thread.start()
        return server
    except OSError:
        # Usually means another YT-Downloader instance already owns the port.
        return None


def extension_cookie_info():
    """Return (valid, count, human timestamp) without exposing cookie values."""
    if not _valid_cookie_cache(EXTENSION_COOKIE_FILE):
        return False, 0, ""
    try:
        lines = EXTENSION_COOKIE_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        count = 0
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            parsed = line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line
            if parsed.startswith("#"):
                continue
            parts = parsed.split("\t")
            if len(parts) >= 7:
                domain = parts[0].lstrip(".").lower()
                if domain == "youtube.com" or domain.endswith(".youtube.com"):
                    count += 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(EXTENSION_COOKIE_FILE.stat().st_mtime))
        return True, count, stamp
    except Exception:
        return False, 0, ""


def _cookie_cache_paths(cfg):
    spec = _cookie_browser_spec(cfg)
    if not spec:
        return None, None
    key = hashlib.sha256(spec.encode("utf-8", errors="ignore")).hexdigest()[:16]
    cache = COOKIE_CACHE_DIR / f"youtube-{key}.txt"
    temp = COOKIE_CACHE_DIR / f".export-{key}-{threading.get_ident()}.txt"
    return cache, temp


def _valid_cookie_cache(path):
    try:
        if not path or not Path(path).is_file() or Path(path).stat().st_size < 64:
            return False
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        return "youtube.com" in text and ("# Netscape HTTP Cookie File" in text or "# HTTP Cookie File" in text)
    except Exception:
        return False


def cookies_args(cfg):
    # Explicit manual import remains the highest-priority override.
    if _manual_cookie_enabled(cfg):
        return ["--cookies", str(MANUAL_COOKIE_FILE)]

    # On Windows, Chromium App-Bound/DPAPI encryption can block yt-dlp. The
    # bundled extension reads only youtube.com cookies through the browser API
    # and the local bridge stores a Netscape file for yt-dlp.
    if _extension_cookie_enabled(cfg):
        return ["--cookies", str(EXTENSION_COOKIE_FILE)]

    spec = _cookie_browser_spec(cfg)
    if not spec:
        return []

    # On Windows Chromium-based browsers lock their live SQLite cookie DB while
    # running. After one successful browser read we keep ONLY youtube.com
    # entries in a small Netscape cookie file inside the portable state folder.
    # This avoids the lock on subsequent checks/downloads while not dumping the
    # user's cookies for unrelated websites.
    if os.name == "nt":
        cache, temp = _cookie_cache_paths(cfg)
        if _valid_cookie_cache(cache):
            return ["--cookies", str(cache)]
        COOKIE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if temp.exists():
                temp.unlink()
        except Exception:
            pass
        return ["--cookies-from-browser", spec, "--cookies", str(temp)]

    return ["--cookies-from-browser", spec]


def finalize_cookie_export(cfg):
    """Convert yt-dlp's temporary browser export into YouTube-only cookies."""
    if os.name != "nt":
        return
    try:
        cache, temp = _cookie_cache_paths(cfg)
    except Exception:
        return
    if not temp or not temp.exists():
        return

    try:
        raw = temp.read_text(encoding="utf-8", errors="ignore").splitlines()
        out, kept = _filter_youtube_cookie_lines(raw)
        if out:
            out[1] = "# YT-Downloader: YouTube-only cookie cache"
        if kept:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(("\r\n".join(out) + "\r\n").encode("utf-8"))
    except Exception:
        pass
    finally:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass


def is_cookie_lock_error(text):
    low = (text or "").lower()
    return (
        "could not copy chrome cookie database" in low
        or ("permission denied" in low and "cookies" in low)
    )


def is_cookie_dpapi_error(text):
    low = (text or "").lower()
    return (
        "failed to decrypt with dpapi" in low
        or "app-bound" in low
        or "app bound" in low
    )


def browser_display_name(cfg):
    value = cfg.get("cookies_browser", "none")
    labels = {
        "brave": "Brave", "firefox": "Firefox", "chrome": "Google Chrome",
        "chromium": "Chromium", "edge": "Microsoft Edge", "opera": "Opera",
        "opera-gx": "Opera GX", "vivaldi": "Vivaldi",
    }
    return labels.get(value, value or "browser")

def valid_url(s):
    try:
        p = urlparse(s)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

def youtube_music_variant(url):
    """Return a music.youtube.com equivalent when the URL is a YouTube URL."""
    raw = (url or "").strip()
    try:
        p = urlparse(raw)
    except Exception:
        return raw

    host = (p.hostname or "").lower()
    if host == "music.youtube.com":
        return raw

    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        path = p.path or "/"
        if path.startswith("/shorts/") or path.startswith("/live/"):
            parts = [x for x in path.split("/") if x]
            if len(parts) >= 2:
                query = parse_qs(p.query)
                pairs = [("v", parts[1])]
                if query.get("list"):
                    pairs.append(("list", query["list"][0]))
                return urlunparse(("https", "music.youtube.com", "/watch", "", urlencode(pairs), ""))
        return urlunparse((p.scheme or "https", "music.youtube.com", path, p.params, p.query, p.fragment))

    if host in ("youtu.be", "www.youtu.be"):
        video_id = (p.path or "").strip("/").split("/")[0]
        if video_id:
            query = parse_qs(p.query)
            pairs = [("v", video_id)]
            if query.get("list"):
                pairs.append(("list", query["list"][0]))
            return urlunparse(("https", "music.youtube.com", "/watch", "", urlencode(pairs), ""))

    return raw


def download_source_url(it):
    # Premium AAC/Opus is exposed by the YouTube Music web client.  Audio-only
    # M4A Premium, exact variants detected there, and Video+audio downloads use
    # the equivalent music URL so ID 141/774 can actually be selected.
    custom_selector = str(getattr(it, "advanced_selector", "") or "")
    if re.search(r"(?<!\d)(141|774)(?!\d)", custom_selector):
        return youtube_music_variant(it.url)
    if getattr(it, "exact_use_music", False):
        return youtube_music_variant(it.url)
    if it.media == "audio" and it.fmt in ("m4a", "opus") and it.quality in ("best", "256"):
        return youtube_music_variant(it.url)
    if it.media == "av":
        return youtube_music_variant(it.url)
    return it.url


def validate_numbers(s, lang):
    if not ITEM_RE.fullmatch(s):
        raise ValueError(tr(lang, "numbers_bad"))
    for x in s.split(","):
        if "-" in x:
            a, b = map(int, x.split("-", 1))
            if a < 1 or b < a:
                raise ValueError(tr(lang, "range_bad", x=x))
        elif int(x) < 1:
            raise ValueError(tr(lang, "numbers_start"))

def expand_numbers(s):
    out = set()
    for x in s.split(","):
        if "-" in x:
            a, b = map(int, x.split("-", 1))
            out.update(range(a, b + 1))
        else:
            out.add(int(x))
    return out

def dest_path(text, cfg):
    root = Path(cfg["default_download_dir"]).expanduser()
    text = text.strip()
    p = Path(text).expanduser() if text else root
    return p if p.is_absolute() else root / p

def audio_stream_selector(fmt, quality="best", variant="standard", playlist=False):
    """Return source-audio format preferences for M4A/Opus.

    Quality has priority.  DRC is preferred only within the requested quality;
    if that DRC variant does not exist, the matching standard stream is used.
    This makes the fallback explicit and detectable from yt-dlp's selected ID.
    """
    fmt = str(fmt or "").lower()
    quality = str(quality or "best").lower()
    variant = str(variant or "standard").lower()
    drc = variant == "drc"

    if fmt == "m4a":
        if quality == "256":
            return "141-drc/141" if drc else "141"
        if quality == "128":
            return "140-drc/140" if drc else "140"
        return "141-drc/141/140-drc/140" if drc else "141/140"

    if fmt == "opus":
        if quality == "256":
            return "774-drc/774" if drc else "774"
        if quality == "128":
            return "251-drc/251" if drc else "251"
        return (
            "774-drc/774/251-drc/251/250-drc/250/249-drc/249"
            if drc else
            "774/251/250/249"
        )

    return "bestaudio"


def item_requests_drc(it):
    if str(getattr(it, "advanced_selector", "") or "").strip():
        return False
    if str(getattr(it, "exact_selector", "") or "").strip():
        return False
    if getattr(it, "media", "audio") == "audio":
        return (
            getattr(it, "fmt", "") in ("m4a", "opus")
            and getattr(it, "audio_variant", "standard") == "drc"
        )
    if getattr(it, "media", "") == "av":
        return (
            getattr(it, "smart_audio_format", "") in ("m4a", "opus")
            and getattr(it, "smart_audio_variant", "standard") == "drc"
        )
    return False


def drc_fallback_used(it, log):
    """True when DRC was requested but yt-dlp selected a non-DRC audio ID."""
    if not item_requests_drc(it):
        return False
    selected = re.findall(r"Downloading \d+ format\(s\):\s*([^\n\r]+)", log or "")
    if not selected:
        return False
    # For playlists there can be one selection line per item.  If any selected
    # item lacks a -drc audio format, report that a standard fallback occurred.
    for spec in selected:
        parts = re.split(r"[+,/]", spec.strip())
        audioish = [p.strip() for p in parts if p.strip()]
        if audioish and not any("-drc" in p.lower() for p in audioish):
            return True
    return False


def audio_quality_arg(q):
    return "0" if q in ("best", "v0") else f"{q}K"


def mp3_quality_arg(mode, q):
    mode = (mode or "vbr").lower()
    q = (q or "v0").lower()
    if mode == "vbr":
        return {"v0": "0", "v2": "2", "v4": "4"}.get(q, "0")
    return f"{q if q in MP3_BITRATE_IDS else '320'}K"


def extra_yt_dlp_args(text):
    text = (text or "").strip()
    if not text:
        return []
    return shlex.split(text, posix=(os.name != "nt"))

def height(q):
    if q == "best":
        return None
    m = re.match(r"(\d+)p", q)
    return int(m.group(1)) if m else None

def partial_playlist_result(log, lang):
    """
    yt-dlp can finish a playlist and still return a non-zero exit status when
    one or more individual entries are unavailable. In that case the GUI
    should report a warning, not mark the entire playlist as failed.
    """
    if "[download] Finished downloading playlist:" not in log:
        return None

    # Require evidence that at least one entry was actually handled
    # successfully (downloaded or already present).
    success_markers = (
        "[download] 100% of",
        "has already been downloaded",
        "[Metadata] Adding metadata",
        "[EmbedThumbnail]",
        "[FixupM4a]",
    )
    if not any(marker in log for marker in success_markers):
        return None

    error_lines = [
        line for line in log.splitlines()
        if line.lstrip().startswith("ERROR:")
    ]
    if not error_lines:
        return None

    # Usually one ERROR line corresponds to one unavailable playlist entry.
    # Deduplicate identical lines so retries do not inflate the count.
    unique_errors = list(dict.fromkeys(error_lines))
    count = max(1, len(unique_errors))
    return "W01", tr(lang, "partial_playlist", n=count)



RETRYABLE_ERROR_NEEDLES = (
    "requested format is not available",
    "no video formats found",
    "timed out",
    "timeout",
    "network is unreachable",
    "connection refused",
    "connection reset",
    "remote end closed connection",
    "temporary failure",
    "temporarily unavailable",
    "http error 429",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "server error",
)


def is_retryable_error_text(text):
    low = (text or "").lower()
    return any(x in low for x in RETRYABLE_ERROR_NEEDLES)


def playlist_errors(log):
    """
    Return errors from one yt-dlp playlist attempt as:
        [{"index": original_playlist_index_or_None,
          "line": full_error_line,
          "retryable": bool}, ...]
    MetadataParser's track_number is the original playlist index, so retries
    can target only failed entries instead of scanning the whole playlist.
    """
    errors = []
    current_track = None

    for raw in (log or "").splitlines():
        line = raw.strip()

        if line.startswith("[download] Downloading item "):
            current_track = None

        m = re.search(
            r"\[MetadataParser\]\s+Parsed track_number .*?:\s*'(\d+)'",
            line
        )
        if m:
            current_track = int(m.group(1))

        if line.startswith("ERROR:"):
            errors.append({
                "index": current_track,
                "line": line,
                "retryable": is_retryable_error_text(line),
            })

    # Deduplicate repeated reports of the same failure.
    seen = set()
    out = []
    for err in errors:
        key = (err["index"], err["line"])
        if key in seen:
            continue
        seen.add(key)
        out.append(err)
    return out


def has_download_success(log):
    markers = (
        "[download] 100% of",
        "has already been downloaded",
        "[Metadata] Adding metadata",
        "[EmbedThumbnail]",
        "[FixupM4a]",
    )
    return any(x in (log or "") for x in markers)


def classify(log, lang, cfg=None):
    l = log.lower()
    rules = [
        ("E01", ("not a valid url", "unsupported url", "invalid url"), "e01"),
        ("E02", ("private video", "private playlist", "sign in", "login required",
                 "authentication required", "unable to recognize playlist"), "e02"),
        ("E03", ("video unavailable", "this video is unavailable",
                 "playlist does not exist", "removed by the uploader"), "e03"),
        ("E04", ("requested format is not available", "no video formats found"), "e04"),
        ("E05", ("timed out", "network is unreachable", "connection refused",
                 "connection reset"), "e05"),
        ("E06", ("failed to decrypt", "could not decrypt", "cookie database", "no such table: meta",
                 "keyring", "kwallet-query", "safe storage", "cookies from browser"), "e06"),
        ("E07", ("ffmpeg not found", "ffprobe not found", "ffmpeg exited"), "e07"),
        ("E08", ("permission denied", "read-only file system", "no space left on device"), "e08"),
    ]
    for code, needles, key in rules:
        if any(n in l for n in needles):
            if code == "E06" and cfg is not None:
                return code, cookie_error_description(cfg, lang)
            return code, tr(lang, key)
    return "E99", tr(lang, "e99")

def media_args(it, cfg):
    custom = str(getattr(it, "advanced_selector", "") or "").strip()
    if custom:
        # Zaawansowany selektor -f przejmuje dobór formatu. Pozostałe
        # zachowanie użytkownik może doprecyzować w dodatkowych argumentach.
        args = ["-f", custom]
        if it.media == "audio" and cfg.get("embed_thumbnail", True):
            args += ["--embed-thumbnail"]
        return args

    exact = str(getattr(it, "exact_selector", "") or "").strip()
    if exact:
        args = ["-f", exact]
        if it.media == "audio":
            if cfg.get("embed_thumbnail", True):
                args += ["--embed-thumbnail"]
            return args
        if it.media == "av":
            merge = str(getattr(it, "exact_merge", "") or "").strip().lower()
            if merge in ("mp4", "webm", "mkv") and "+" in exact:
                args += ["--merge-output-format", merge]
            return args
        if it.media == "video":
            remux = str(getattr(it, "exact_merge", "") or "").strip().lower()
            if remux in ("mp4", "webm", "mkv"):
                args += ["--remux-video", remux]
        return args

    if it.media == "audio":
        if it.fmt == "original":
            args = ["-f", "bestaudio"]
        elif it.fmt == "m4a":
            selector = audio_stream_selector(
                "m4a", it.quality, getattr(it, "audio_variant", "standard"), it.playlist
            )
            args = ["-f", selector, "-x", "--audio-format", "m4a"]
        elif it.fmt == "opus":
            selector = audio_stream_selector(
                "opus", it.quality, getattr(it, "audio_variant", "standard"), it.playlist
            )
            args = ["-f", selector, "-x", "--audio-format", "opus"]
        elif it.fmt == "mp3":
            args = ["-f", "bestaudio", "-x", "--audio-format", "mp3",
                    "--audio-quality", mp3_quality_arg(getattr(it, "audio_quality_mode", "vbr"), it.quality)]
        else:
            args = ["-f", "bestaudio", "-x", "--audio-format", "flac"]

        if cfg.get("embed_thumbnail", True):
            args += ["--embed-thumbnail"]
        return args

    # Wideo korzysta z inteligentnych reguł. Dla starszych wpisów kolejki
    # pola smart_* mają wartości domyślne i zachowanie pozostaje automatyczne.
    vf = _smart_video_filter_expr(it)
    tier = str(getattr(it, "smart_video_tier", "auto") or "auto")
    if tier == "premium":
        video_selector = f"bestvideo{vf}[format_note*=Premium]/bestvideo{vf}"
    else:
        video_selector = f"bestvideo{vf}"

    container = str(getattr(it, "smart_video_container", "auto") or "auto")
    if it.media == "video":
        args = ["-f", video_selector]
        if container in ("mp4", "webm", "mkv"):
            args += ["--remux-video", container]
        return args

    audio_selector = _smart_audio_selector(it)
    merge = _smart_merge_container(it)
    combined = _combine_format_alternatives(video_selector, audio_selector)
    args = ["-f", combined]
    if merge:
        args += ["--merge-output-format", merge]
    return args

def subtitle_args(it):
    if it.media == "audio" or it.subtitle_mode == "none":
        return []

    args = []
    if it.subtitle_mode == "manual":
        args += ["--write-subs"]
    elif it.subtitle_mode == "auto":
        args += ["--write-auto-subs"]
    elif it.subtitle_mode == "fallback":
        # For the same language yt-dlp keeps the normal subtitle when one is
        # available; automatic captions act as the fallback.
        args += ["--write-subs", "--write-auto-subs"]

    args += ["--sub-langs", it.subtitle_lang or "pl"]

    if it.subtitle_output in ("embed", "both"):
        args += ["--embed-subs"]
    if it.subtitle_output == "embed":
        # Explicitly remove the separate subtitle after successful embedding.
        args += ["--compat-options", "no-keep-subs"]

    return args


def build_cmd(it, cfg, override=None, source_url=None):
    # Command construction must be side-effect free. The GUI calls build_cmd()
    # while the user types in order to refresh the advanced command preview.
    # Creating the directory here used to produce E / Ec / Ech / Echo.
    d = dest_path(it.destination, cfg)

    cmd = [
        tool_executable("yt-dlp"), "--ignore-config", "--newline", "--windows-filenames",
        "--retries", "3", "--fragment-retries", "3",
        "--retry-sleep", "http:2", "--retry-sleep", "fragment:2",
    ]
    if cfg.get("no_overwrites", True):
        cmd += ["--no-overwrites"]
    if cfg.get("embed_metadata", True):
        cmd += ["--embed-metadata"]

    cmd += cookies_args(cfg) + media_args(it, cfg) + subtitle_args(it)
    cmd += extra_yt_dlp_args(getattr(it, "advanced_args", ""))

    if it.playlist and it.media == "audio":
        cmd += [
            "--parse-metadata", "playlist_index:%(track_number)s",
            "--parse-metadata", "%(album,playlist_title|)s:%(meta_album)s",
            "--parse-metadata", "%(album_artist,artist,uploader|)s:%(meta_album_artist)s",
        ]

    if it.playlist:
        cmd += ["--yes-playlist"]
        sel = override or (it.numbers if it.selection == "select" else "")
        if sel:
            cmd += ["--playlist-items", sel]
    else:
        cmd += ["--no-playlist"]

    template = "%(track,title)s.%(ext)s" if it.media == "audio" else "%(title)s.%(ext)s"
    source = source_url or download_source_url(it)
    return cmd + ["-P", str(d), "-o", template, source]

def _parse_duration(value):
    try:
        x = float(value)
        return x if x > 0 else 0.0
    except Exception:
        return 0.0


def probe_playlist(url, cfg):
    lang = cfg.get("language", "en")
    cmd = [
        tool_executable("yt-dlp"), "--ignore-config", "--flat-playlist", "--yes-playlist",
        "--print", "%(playlist_index)s\t%(duration)s\t%(title)s"
    ] + cookies_args(cfg) + [url]

    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW, env=external_subprocess_env())
    finalize_cookie_export(cfg)
    full = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()

    if p.returncode:
        code, desc = classify(full, lang, cfg)
        raise AppError(code, desc, full)

    entries = []
    for line in (p.stdout or "").splitlines():
        parts = line.split("\t", 2)
        if not parts:
            continue
        idx = parts[0].strip()
        if not idx.isdigit():
            continue
        dur = _parse_duration(parts[1].strip()) if len(parts) > 1 else 0.0
        entries.append((int(idx), dur))

    if not entries:
        if "unable to recognize playlist" in full.lower():
            raise AppError("E02", tr(lang, "playlist_no_access"), full)
        raise AppError("E03", tr(lang, "not_playlist"), full)

    return entries, full

def selected_playlist_entries(it, entries):
    if it.selection == "select":
        wanted = expand_numbers(it.numbers)
        return [(idx, dur) for idx, dur in entries if idx in wanted]
    if it.selection == "omit":
        omitted = expand_numbers(it.numbers)
        return [(idx, dur) for idx, dur in entries if idx not in omitted]
    return list(entries)

def probe_item_duration(it, cfg):
    if it.playlist:
        entries, full = probe_playlist(it.url, cfg)
        selected = selected_playlist_entries(it, entries)
        return sum(d for _, d in selected if d > 0), len(selected), full

    cmd = [
        tool_executable("yt-dlp"), "--ignore-config", "--skip-download", "--no-playlist",
        "--print", "%(duration)s"
    ] + cookies_args(cfg) + [it.url]
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW, env=external_subprocess_env())
    finalize_cookie_export(cfg)
    full = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
    duration = 0.0
    for line in (p.stdout or "").splitlines():
        duration = _parse_duration(line.strip())
        if duration > 0:
            break
    return duration, 1, full


def _extract_format_info(data):
    if not isinstance(data, dict):
        return None
    if data.get("formats"):
        return data
    for entry in data.get("entries") or []:
        found = _extract_format_info(entry)
        if found:
            return found
    return data


def _format_bitrate(fmt):
    try:
        abr = float(fmt.get("abr") or 0)
    except Exception:
        abr = 0.0
    if abr > 0:
        return abr
    try:
        return float(fmt.get("tbr") or 0)
    except Exception:
        return 0.0


def _audio_only(fmt):
    return (fmt.get("vcodec") in (None, "none")) and (fmt.get("acodec") not in (None, "none"))


def _video_only(fmt):
    return (fmt.get("vcodec") not in (None, "none")) and (fmt.get("acodec") in (None, "none"))


def _human_kbps(value):
    try:
        x = float(value or 0)
    except Exception:
        x = 0.0
    if x <= 0:
        return "—"
    return f"{int(round(x))} kb/s"


def _video_codec_family(fmt):
    codec = str(fmt.get("vcodec") or "").lower()
    if codec.startswith("avc1") or "h264" in codec or "h.264" in codec:
        return "h264"
    if "vp9" in codec or codec.startswith("vp09"):
        return "vp9"
    if codec.startswith("av01") or codec == "av1" or " av1" in codec:
        return "av1"
    if codec.startswith("hev1") or codec.startswith("hvc1") or "hevc" in codec or "h265" in codec:
        return "hevc"
    return codec or "other"


def _video_is_premium(fmt):
    text = " ".join(str(fmt.get(k) or "") for k in ("format_note", "format", "format_id")).lower()
    if "premium" in text or "enhanced" in text:
        return True
    # IDs observed by yt-dlp for YouTube's 1080p Premium variants.
    return str(fmt.get("format_id") or "") in {"356", "721"}


def _video_source_ext(fmt):
    return str(fmt.get("ext") or "").lower()


def _container_accepts_source(container, fmt):
    container = (container or "auto").lower()
    if container in ("", "auto", "mkv"):
        return True
    ext = _video_source_ext(fmt)
    if container == "mp4":
        return ext in ("mp4", "m4v")
    if container == "webm":
        return ext == "webm"
    return True


def _video_matches_rule(fmt, field, value):
    value = str(value or "auto")
    if value in ("", "auto", "best"):
        return True
    if field == "container":
        return _container_accepts_source(value, fmt)
    if field == "resolution":
        try:
            return int(round(float(fmt.get("height") or 0))) == int(value)
        except Exception:
            return False
    if field == "fps":
        try:
            return int(round(float(fmt.get("fps") or 0))) == int(round(float(value)))
        except Exception:
            return False
    if field == "codec":
        return _video_codec_family(fmt) == value
    if field == "tier":
        return _video_is_premium(fmt) if value == "premium" else (not _video_is_premium(fmt))
    if field == "bitrate":
        try:
            got = int(round(float(fmt.get("vbr") or fmt.get("tbr") or 0)))
            return got == int(round(float(value)))
        except Exception:
            return False
    return True


def _audio_family(fmt):
    ext = str(fmt.get("ext") or "").lower()
    codec = str(fmt.get("acodec") or "").lower()
    if ext in ("m4a", "mp4") or codec.startswith("mp4a") or "aac" in codec:
        return "m4a"
    if "opus" in codec:
        return "opus"
    return ext or codec or "other"


def _audio_quality_class(fmt):
    br = _format_bitrate(fmt)
    if br >= 220:
        return "256"
    if 100 <= br <= 190:
        return "128"
    return str(int(round(br))) if br > 0 else "auto"


def _smart_video_filter_expr(it):
    parts = []
    container = getattr(it, "smart_video_container", "auto") or "auto"
    if container == "mp4":
        parts.append("[ext=mp4]")
    elif container == "webm":
        parts.append("[ext=webm]")

    res = str(getattr(it, "smart_video_resolution", "auto") or "auto")
    if res not in ("auto", "best", ""):
        # For playlists resolution is a maximum so one odd entry does not kill the whole batch.
        op = "<=" if getattr(it, "playlist", False) else "="
        parts.append(f"[height{op}{int(float(res))}]")

    fps = str(getattr(it, "smart_video_fps", "auto") or "auto")
    if fps not in ("auto", "best", ""):
        parts.append(f"[fps={int(float(fps))}]")

    codec = str(getattr(it, "smart_video_codec", "auto") or "auto")
    if codec == "h264":
        parts.append("[vcodec^=avc1]")
    elif codec == "vp9":
        parts.append("[vcodec*=vp9]")
    elif codec == "av1":
        parts.append("[vcodec^=av01]")
    elif codec == "hevc":
        parts.append("[vcodec~='^(hev1|hvc1)']")

    # Exact bitrate only makes sense for a single analysed material.
    br = str(getattr(it, "smart_video_bitrate", "auto") or "auto")
    if not getattr(it, "playlist", False) and br not in ("auto", ""):
        try:
            b = int(float(br))
            parts.append(f"[tbr>={max(1,b-2)}][tbr<={b+2}]")
        except Exception:
            pass
    return "".join(parts)


def _smart_audio_selector(it):
    fmt = str(getattr(it, "smart_audio_format", "auto") or "auto")
    q = str(getattr(it, "smart_audio_quality", "best") or "best")
    variant = str(getattr(it, "smart_audio_variant", "standard") or "standard")
    if fmt in ("m4a", "opus"):
        return audio_stream_selector(fmt, q, variant, getattr(it, "playlist", False))
    return "bestaudio"


def _smart_merge_container(it):
    value = str(getattr(it, "smart_video_container", "auto") or "auto")
    if value in ("mp4", "mkv", "webm"):
        return value
    # In Automatic mode let yt-dlp/FFmpeg select a container compatible with
    # the actual video+audio pair. This is safer for heterogeneous playlists.
    return ""


def _combine_format_alternatives(video_selector, audio_selector):
    """Build fallback pairs without relying on yt-dlp grouping precedence."""
    videos = [x for x in str(video_selector).split("/") if x]
    audios = [x for x in str(audio_selector).split("/") if x]
    if not videos:
        videos = ["bestvideo"]
    if not audios:
        audios = ["bestaudio"]
    return "/".join(f"{v}+{a}" for v in videos for a in audios)


class FormatProbeWorker(QObject):
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, url, cfg):
        super().__init__()
        self.url = url
        self.cfg = cfg
        self.proc = None
        self.stop_req = False

    def stop(self):
        self.stop_req = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def _run_one(self, url):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        pure_playlist = parsed.path.rstrip("/").endswith("/playlist") and bool(qs.get("list"))
        cmd = [tool_executable("yt-dlp"), "--ignore-config", "--dump-single-json"]
        if pure_playlist:
            cmd += ["--playlist-items", "1"]
        else:
            cmd += ["--no-playlist"]
        cmd += cookies_args(self.cfg) + [url]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW, env=external_subprocess_env())
        out, err = self.proc.communicate()
        rc = self.proc.returncode
        self.proc = None
        finalize_cookie_export(self.cfg)
        if rc != 0:
            # --dump-single-json may already have written a huge valid JSON
            # document to stdout before a later cookie/keyring failure makes
            # yt-dlp exit non-zero.  Never dump that JSON into an error dialog;
            # stderr contains the actionable diagnostics.
            details = (err or "").strip()
            if not details:
                details = "yt-dlp exited with a non-zero status"
            return None, details
        try:
            return _extract_format_info(json.loads(out)), ""
        except Exception as exc:
            return None, f"{exc}\n{err or ''}".strip()

    def run(self):
        try:
            original = self.url.strip()
            music = youtube_music_variant(original)
            urls = [original]
            if music and music != original:
                urls.append(music)

            infos = []
            errors = []
            for u in urls:
                if self.stop_req:
                    return
                info, err = self._run_one(u)
                if info:
                    infos.append((u, info))
                elif err:
                    if err not in errors:
                        errors.append(err)
                    # The same locked browser DB would fail for both the normal
                    # YouTube URL and the music.youtube.com variant. Stop here
                    # instead of showing the same error multiple times.
                    if is_cookie_lock_error(err) or is_cookie_dpapi_error(err):
                        break

            if not infos:
                self.failed.emit("\n\n".join(errors) or "yt-dlp returned no format information")
                return

            primary = infos[0][1]
            merged = {}
            for source_url, info in infos:
                for f in info.get("formats") or []:
                    key = (
                        str(f.get("format_id") or ""), str(f.get("ext") or ""),
                        str(f.get("vcodec") or ""), str(f.get("acodec") or ""),
                    )
                    score = (_format_bitrate(f), float(f.get("height") or 0), float(f.get("fps") or 0))
                    prev = merged.get(key)
                    if prev is None or score > prev[0]:
                        g = dict(f)
                        g["_probe_source"] = source_url
                        merged[key] = (score, g)

            formats = [v[1] for v in merged.values()]
            payload = {
                "title": primary.get("title") or primary.get("fulltitle") or original,
                "webpage_url": primary.get("webpage_url") or original,
                "formats": formats,
                "checked_music": any(urlparse(u).hostname == "music.youtube.com" for u, _ in infos),
            }
            self.result.emit(payload)
        except Exception as exc:
            self.failed.emit(repr(exc))
        finally:
            self.proc = None
            self.finished.emit()



def _ready_av(fmt):
    return (
        fmt.get("vcodec") not in (None, "none")
        and fmt.get("acodec") not in (None, "none")
    )


def _format_is_music(fmt):
    try:
        return urlparse(str(fmt.get("_probe_source") or "")).hostname == "music.youtube.com"
    except Exception:
        return False


def _recommended_merge(vf, af):
    vext = str(vf.get("ext") or "").lower()
    aext = str(af.get("ext") or "").lower()
    if vext == "mp4" and aext in ("m4a", "mp4"):
        return "mp4"
    if vext == "webm" and aext == "webm":
        return "webm"
    return "mkv"


def _fmt_id_sort_value(value):
    text = str(value or "")
    try:
        return (0, int(text))
    except Exception:
        return (1, text.casefold())


def _variant_audio_label(f):
    return (
        f"ID {f.get('format_id') or '—'} • {(f.get('ext') or '—').upper()} • "
        f"{f.get('acodec') or '—'} • {_human_kbps(_format_bitrate(f))}"
    )


def _variant_video_label(f):
    h = int(f.get("height") or 0)
    fps = int(round(float(f.get("fps") or 0))) if f.get("fps") else 0
    res = f"{h}p" if h else (f.get("resolution") or "—")
    if fps:
        res += f"/{fps}"
    return (
        f"ID {f.get('format_id') or '—'} • {(f.get('ext') or '—').upper()} • "
        f"{res} • {f.get('vcodec') or '—'} • {_human_kbps(f.get('vbr') or f.get('tbr'))}"
    )


def build_exact_variants(formats, media, lang="pl"):
    audio = [f for f in formats if _audio_only(f)]
    video = [f for f in formats if _video_only(f)]
    ready = [f for f in formats if _ready_av(f)]

    if media == "audio":
        out = []
        rows = sorted(
            audio,
            key=lambda f: (_format_bitrate(f), str(f.get("ext") or ""), _fmt_id_sort_value(f.get("format_id"))),
            reverse=True,
        )
        for f in rows:
            out.append({
                "selector": str(f.get("format_id") or ""),
                "label": _variant_audio_label(f),
                "merge": "",
                "use_music": _format_is_music(f),
            })
        return out

    if media == "video":
        out = []
        rows = sorted(
            video,
            key=lambda f: (
                float(f.get("height") or 0), float(f.get("fps") or 0),
                float(f.get("vbr") or f.get("tbr") or 0),
                _fmt_id_sort_value(f.get("format_id")),
            ),
            reverse=True,
        )
        for f in rows:
            out.append({
                "selector": str(f.get("format_id") or ""),
                "label": _variant_video_label(f),
                "merge": "",
                "use_music": _format_is_music(f),
            })
        return out

    out = []
    # Ready-made combined streams.
    for f in sorted(
        ready,
        key=lambda x: (
            float(x.get("height") or 0), float(x.get("fps") or 0),
            _format_bitrate(x), float(x.get("tbr") or 0),
            _fmt_id_sort_value(x.get("format_id")),
        ),
        reverse=True,
    ):
        h = int(f.get("height") or 0)
        fps = int(round(float(f.get("fps") or 0))) if f.get("fps") else 0
        res = f"{h}p" if h else (f.get("resolution") or "—")
        if fps:
            res += f"/{fps}"
        label = (
            f"ID {f.get('format_id') or '—'} • {'gotowy' if lang == 'pl' else 'ready'} • {(f.get('ext') or '—').upper()} • "
            f"{res} • {f.get('vcodec') or '—'} + {f.get('acodec') or '—'} • "
            f"{_human_kbps(_format_bitrate(f))}"
        )
        out.append({
            "selector": str(f.get("format_id") or ""),
            "label": label,
            "merge": "",
            "use_music": _format_is_music(f),
        })

    # Every possible separate video + separate audio pairing.
    vrows = sorted(
        video,
        key=lambda x: (
            float(x.get("height") or 0), float(x.get("fps") or 0),
            float(x.get("vbr") or x.get("tbr") or 0),
            _fmt_id_sort_value(x.get("format_id")),
        ),
        reverse=True,
    )
    arows = sorted(
        audio,
        key=lambda x: (_format_bitrate(x), str(x.get("ext") or ""), _fmt_id_sort_value(x.get("format_id"))),
        reverse=True,
    )
    for vf in vrows:
        for af in arows:
            selector = f"{vf.get('format_id')}+{af.get('format_id')}"
            merge = _recommended_merge(vf, af)
            h = int(vf.get("height") or 0)
            fps = int(round(float(vf.get("fps") or 0))) if vf.get("fps") else 0
            res = f"{h}p" if h else (vf.get("resolution") or "—")
            if fps:
                res += f"/{fps}"
            label = (
                f"{selector} • {merge.upper()} • {res} • {vf.get('vcodec') or '—'} + "
                f"{af.get('acodec') or '—'} {_human_kbps(_format_bitrate(af))}"
            )
            out.append({
                "selector": selector,
                "label": label,
                "merge": merge,
                "use_music": _format_is_music(vf) or _format_is_music(af),
            })
    return out


class SortTableItem(QTableWidgetItem):
    def __init__(self, text, sort_value=None):
        super().__init__(str(text))
        self._sort_value = sort_value if sort_value is not None else str(text).casefold()

    def __lt__(self, other):
        if isinstance(other, SortTableItem):
            try:
                return self._sort_value < other._sort_value
            except TypeError:
                return str(self._sort_value) < str(other._sort_value)
        return super().__lt__(other)


class FormatInspectorDialog(QDialog):
    def __init__(self, parent, payload, lang):
        super().__init__(parent)
        self.lang = lang
        self.payload = payload
        self.main_window = parent
        self.setWindowTitle(tr(lang, "format_check_title"))
        self.resize(1080, 680)
        self.setMinimumSize(680, 400)

        layout = QVBoxLayout(self)
        title = QLabel(tr(lang, "format_check_source", title=payload.get("title") or "—"))
        title.setWordWrap(True)
        layout.addWidget(title)

        formats = payload.get("formats") or []
        audio = [f for f in formats if _audio_only(f)]
        video = [f for f in formats if _video_only(f)]

        best_m4a = max((f for f in audio if f.get("ext") == "m4a" and "-drc" not in str(f.get("format_id") or "").lower()), key=_format_bitrate, default=None)
        best_opus = max(
            (f for f in audio if f.get("acodec") and "opus" in str(f.get("acodec")).lower() and "-drc" not in str(f.get("format_id") or "").lower()),
            key=_format_bitrate, default=None
        )
        best_video = max(
            video,
            key=lambda f: (float(f.get("height") or 0), float(f.get("fps") or 0), _format_bitrate(f)),
            default=None
        )

        def audio_summary(f):
            if not f:
                return tr(lang, "format_check_none")
            extra = ""
            if str(f.get("format_id")) in ("141", "774") or _format_bitrate(f) >= 220:
                extra = " (Premium/high)"
            return f"{_human_kbps(_format_bitrate(f))} • ID {f.get('format_id', '—')}{extra}"

        if best_video:
            video_quality = f"{int(best_video.get('height') or 0)}p"
            if best_video.get("fps"):
                video_quality += f" / {int(round(float(best_video.get('fps'))))} FPS"
        else:
            video_quality = tr(lang, "format_check_none")

        summary = QLabel(
            tr(lang, "format_check_m4a", quality=audio_summary(best_m4a)) + "\n" +
            tr(lang, "format_check_opus", quality=audio_summary(best_opus)) + "\n" +
            tr(lang, "format_check_video_max", quality=video_quality)
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        if payload.get("checked_music"):
            note = QLabel(tr(lang, "format_check_used_music"))
            note.setWordWrap(True)
            layout.addWidget(note)

        tabs = QTabWidget()
        tabs.addTab(self._audio_table(audio), tr(lang, "format_check_audio"))
        tabs.addTab(self._video_table(video), tr(lang, "format_check_video"))
        layout.addWidget(tabs, 1)

        merge_note = QLabel(tr(lang, "format_check_merge_note"))
        merge_note.setWordWrap(True)
        layout.addWidget(merge_note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _prepare_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        h = table.horizontalHeader()
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(False)
        for i in range(len(headers)):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        if headers:
            h.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)

        # First click on a column = descending (highest -> lowest), second = ascending.
        state = {"column": None, "descending": True}
        def sort_column(column):
            if state["column"] != column:
                state["column"] = column
                state["descending"] = True
            order = (
                Qt.SortOrder.DescendingOrder
                if state["descending"]
                else Qt.SortOrder.AscendingOrder
            )
            table.sortItems(column, order)
            h.setSortIndicator(column, order)
            h.setSortIndicatorShown(True)
            state["descending"] = not state["descending"]
        h.sectionClicked.connect(sort_column)
        return table

    @staticmethod
    def _append_row(table, values):
        r = table.rowCount()
        table.insertRow(r)
        for c, value in enumerate(values):
            if isinstance(value, tuple):
                text, sort_value = value
            else:
                text, sort_value = value, str(value).casefold()
            table.setItem(r, c, SortTableItem(text, sort_value))

    def _enable_selector_pick(self, table):
        # Te akcje służą wyłącznie do wypełniania sekcji „Zaawansowane yt-dlp”.
        # Gdy użytkownik ukryje tę funkcję, nie pokazujemy również tych opcji
        # pod PPM ani nie przechwytujemy dwukliku do selektora.
        if not bool(getattr(self.main_window, "cfg", {}).get("show_advanced", True)):
            table.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
            return
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def selected_id():
            row = table.currentRow()
            item = table.item(row, 0) if row >= 0 else None
            return item.text().strip() if item else ""

        def set_selector(append=False):
            fmt_id = selected_id()
            target = getattr(self.main_window, "advanced_selector", None)
            if not fmt_id or target is None:
                return
            current = target.text().strip()
            value = f"{current}+{fmt_id}" if append and current else fmt_id
            target.setText(value)
            if hasattr(self.main_window, "advanced_toggle"):
                self.main_window.advanced_toggle.setChecked(True)

        def context_menu(pos):
            hit = table.itemAt(pos)
            if hit is None:
                return
            table.setCurrentCell(hit.row(), hit.column())
            menu = QMenu(table)
            use_action = menu.addAction(tr(self.lang, "format_pick_id"))
            add_action = menu.addAction(tr(self.lang, "format_add_id"))
            action = menu.exec(table.viewport().mapToGlobal(pos))
            if action == use_action:
                set_selector(False)
            elif action == add_action:
                set_selector(True)

        table.customContextMenuRequested.connect(context_menu)
        table.cellDoubleClicked.connect(lambda _row, _col: set_selector(False))

    def _audio_table(self, formats):
        headers = [
            "ID", "Format", "Kodek" if self.lang == "pl" else "Codec",
            "Bitrate", "Hz", "Kanały" if self.lang == "pl" else "Channels",
            tr(self.lang, "format_variant"),
            "Jakość" if self.lang == "pl" else "Quality",
        ]
        table = self._prepare_table(headers)
        rows = sorted(
            formats,
            key=lambda f: (_format_bitrate(f), str(f.get("ext") or ""), _fmt_id_sort_value(f.get("format_id"))),
            reverse=True
        )
        for f in rows:
            abr = _format_bitrate(f)
            note = ""
            if str(f.get("format_id")) in ("141", "774") or abr >= 220:
                note = "Premium / high"
            elif 105 <= abr <= 180:
                note = "~128 kb/s"
            self._append_row(table, [
                (f.get("format_id") or "—", _fmt_id_sort_value(f.get("format_id"))),
                ((f.get("ext") or "—").upper(), str(f.get("ext") or "").casefold()),
                (f.get("acodec") or "—", str(f.get("acodec") or "").casefold()),
                (_human_kbps(abr), abr),
                (str(int(f.get("asr") or 0)) if f.get("asr") else "—", float(f.get("asr") or 0)),
                (str(f.get("audio_channels") or "—"), float(f.get("audio_channels") or 0)),
                (
                    tr(self.lang, "format_variant_drc")
                    if "-drc" in str(f.get("format_id") or "").lower()
                    else tr(self.lang, "format_variant_standard"),
                    1 if "-drc" in str(f.get("format_id") or "").lower() else 0,
                ),
                (note, note.casefold()),
            ])
        self._enable_selector_pick(table)
        return table

    def _video_table(self, formats):
        headers = [
            "ID", "Format", "Rozdzielczość" if self.lang == "pl" else "Resolution",
            "FPS", "Kodek" if self.lang == "pl" else "Codec", "Bitrate", "HDR"
        ]
        table = self._prepare_table(headers)
        rows = sorted(
            formats,
            key=lambda f: (
                float(f.get("height") or 0), float(f.get("fps") or 0),
                float(f.get("vbr") or f.get("tbr") or 0),
                _fmt_id_sort_value(f.get("format_id")),
            ),
            reverse=True
        )
        for f in rows:
            h = float(f.get("height") or 0)
            w = float(f.get("width") or 0)
            res = f.get("resolution") or (f"{int(w)}x{int(h)}" if h else "—")
            dynamic = f.get("dynamic_range") or ""
            bitrate = float(f.get("vbr") or f.get("tbr") or 0)
            fps = float(f.get("fps") or 0)
            self._append_row(table, [
                (f.get("format_id") or "—", _fmt_id_sort_value(f.get("format_id"))),
                ((f.get("ext") or "—").upper(), str(f.get("ext") or "").casefold()),
                (res, h),
                (str(int(round(fps))) if fps else "—", fps),
                (f.get("vcodec") or "—", str(f.get("vcodec") or "").casefold()),
                (_human_kbps(bitrate), bitrate),
                ("" if dynamic in ("", "SDR") else dynamic, str(dynamic).casefold()),
            ])
        self._enable_selector_pick(table)
        return table

    def _av_table(self, formats):
        headers = [
            "Selektor", tr(self.lang, "format_check_container"),
            "Rozdzielczość" if self.lang == "pl" else "Resolution",
            "FPS", "ID wideo" if self.lang == "pl" else "Video ID",
            "Kodek wideo" if self.lang == "pl" else "Video codec",
            "ID audio" if self.lang == "pl" else "Audio ID",
            "Kodek audio" if self.lang == "pl" else "Audio codec",
            "Bitrate audio" if self.lang == "pl" else "Audio bitrate"
        ]
        table = self._prepare_table(headers)

        audio = [f for f in formats if _audio_only(f)]
        video = [f for f in formats if _video_only(f)]
        ready = [f for f in formats if _ready_av(f)]

        # Ready-made combined formats from YouTube.
        ready_rows = sorted(
            ready,
            key=lambda x: (
                float(x.get("height") or 0), float(x.get("fps") or 0),
                _format_bitrate(x), float(x.get("tbr") or 0),
                _fmt_id_sort_value(x.get("format_id")),
            ),
            reverse=True
        )
        for f in ready_rows:
            h = float(f.get("height") or 0)
            fps = float(f.get("fps") or 0)
            selector = str(f.get("format_id") or "")
            self._append_row(table, [
                (selector, _fmt_id_sort_value(selector)),
                ((f.get("ext") or "—").upper(), str(f.get("ext") or "").casefold()),
                (f"{int(h)}p" if h else (f.get("resolution") or "—"), h),
                (str(int(round(fps))) if fps else "—", fps),
                (selector, _fmt_id_sort_value(selector)),
                (f.get("vcodec") or "—", str(f.get("vcodec") or "").casefold()),
                (selector, _fmt_id_sort_value(selector)),
                (f.get("acodec") or "—", str(f.get("acodec") or "").casefold()),
                (_human_kbps(_format_bitrate(f)), _format_bitrate(f)),
            ])

        # Every separate video + audio combination, with no resolution/codec deduplication.
        vrows = sorted(
            video,
            key=lambda x: (
                float(x.get("height") or 0), float(x.get("fps") or 0),
                float(x.get("vbr") or x.get("tbr") or 0),
                _fmt_id_sort_value(x.get("format_id")),
            ),
            reverse=True
        )
        arows = sorted(
            audio,
            key=lambda x: (_format_bitrate(x), str(x.get("ext") or ""), _fmt_id_sort_value(x.get("format_id"))),
            reverse=True
        )
        for vf in vrows:
            for af in arows:
                selector = f"{vf.get('format_id')}+{af.get('format_id')}"
                merge = _recommended_merge(vf, af)
                h = float(vf.get("height") or 0)
                fps = float(vf.get("fps") or 0)
                abr = _format_bitrate(af)
                self._append_row(table, [
                    (selector, selector.casefold()),
                    (merge.upper(), merge),
                    (f"{int(h)}p" if h else (vf.get("resolution") or "—"), h),
                    (str(int(round(fps))) if fps else "—", fps),
                    (vf.get("format_id") or "—", _fmt_id_sort_value(vf.get("format_id"))),
                    (vf.get("vcodec") or "—", str(vf.get("vcodec") or "").casefold()),
                    (af.get("format_id") or "—", _fmt_id_sort_value(af.get("format_id"))),
                    (af.get("acodec") or "—", str(af.get("acodec") or "").casefold()),
                    (_human_kbps(abr), abr),
                ])
        return table


class ProbeWorker(QObject):
    done = Signal(str, float, int)
    finished = Signal()

    def __init__(self, item, cfg):
        super().__init__()
        self.item = item
        self.cfg = cfg
        self.proc = None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def run(self):
        duration = 0.0
        count = 0
        try:
            if self.item.playlist:
                cmd = [
                    tool_executable("yt-dlp"), "--ignore-config", "--flat-playlist", "--yes-playlist",
                    "--print", "%(playlist_index)s\t%(duration)s\t%(title)s"
                ] + cookies_args(self.cfg) + [self.item.url]
            else:
                cmd = [
                    tool_executable("yt-dlp"), "--ignore-config", "--skip-download", "--no-playlist",
                    "--print", "%(duration)s"
                ] + cookies_args(self.cfg) + [self.item.url]

            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW,
                env=external_subprocess_env()
            )
            stdout, _stderr = self.proc.communicate()
            rc = self.proc.returncode
            self.proc = None
            finalize_cookie_export(self.cfg)
            if rc != 0:
                self.done.emit(self.item.uid, 0.0, 0)
                return

            if self.item.playlist:
                entries = []
                for line in (stdout or "").splitlines():
                    parts = line.split("\t", 2)
                    if not parts or not parts[0].strip().isdigit():
                        continue
                    dur = _parse_duration(parts[1].strip()) if len(parts) > 1 else 0.0
                    entries.append((int(parts[0].strip()), dur))
                selected = selected_playlist_entries(self.item, entries)
                duration = sum(d for _, d in selected if d > 0)
                count = len(selected)
            else:
                for line in (stdout or "").splitlines():
                    duration = _parse_duration(line.strip())
                    if duration > 0:
                        break
                count = 1 if duration > 0 else 0

            self.done.emit(self.item.uid, float(duration), int(count))
        except Exception:
            self.done.emit(self.item.uid, 0.0, 0)
        finally:
            self.proc = None
            self.finished.emit()

class Worker(QObject):
    MAX_ATTEMPTS = 3
    RETRY_DELAYS = (3, 5)

    log = Signal(str)
    started = Signal(str)
    metadata = Signal(str, float, int)
    done = Signal(str, bool, str, str, str, float, float, bool)
    all_done = Signal(bool)

    def __init__(self, items, cfg):
        super().__init__()
        self.items = items
        self.cfg = cfg
        self.stop_req = False
        self.proc = None
        self.skipped = set()

    def stop(self):
        self.stop_req = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def skip(self, uid):
        self.skipped.add(uid)

    def is_skipped(self, uid):
        return uid in self.skipped

    def wait_retry(self, seconds):
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            if self.stop_req:
                return False
            time.sleep(min(0.15, max(0.0, deadline - time.monotonic())))
        return not self.stop_req

    def retry_log(self, key, **kwargs):
        text = tr(self.cfg.get("language", "en"), key, **kwargs)
        self.log.emit(text)
        return text

    def run_proc(self, cmd):
        shown = "$ " + " ".join(repr(x) if " " in x else x for x in cmd)
        lines = [shown]
        self.log.emit(shown)

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=NO_WINDOW,
            env=external_subprocess_env(),
        )

        for line in self.proc.stdout:
            line = line.rstrip()
            lines.append(line)
            self.log.emit(line)
            if self.stop_req:
                break

        if self.stop_req and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

        rc = self.proc.wait()
        self.proc = None
        finalize_cookie_export(self.cfg)
        return rc, "\n".join(lines)

    def run(self):
        lang = self.cfg.get("language", "en")
        stopped = False
        try:
            for it in self.items:
                if self.stop_req:
                    stopped = True
                    break
                if self.is_skipped(it.uid):
                    continue

                self.started.emit(it.uid)
                item_started = time.monotonic()
                logs = []
                duration = float(it.duration_s or 0)
                entries_count = int(it.entries_count or 1)

                try:
                    override = None

                    if it.playlist:
                        self.log.emit(tr(lang, "checking_playlist"))
                        entries, plog = probe_playlist(it.url, self.cfg)
                        logs.append("=== PLAYLIST ===\n" + plog)
                        selected = selected_playlist_entries(it, entries)
                        if not selected:
                            raise AppError("E09", tr(lang, "no_items_after_omit"))

                        duration = sum(d for _, d in selected if d > 0)
                        entries_count = len(selected)
                        self.metadata.emit(it.uid, duration, entries_count)

                        if it.selection == "omit":
                            override = ",".join(str(idx) for idx, _ in selected)

                    elif duration <= 0:
                        try:
                            duration, entries_count, mlog = probe_item_duration(it, self.cfg)
                            if mlog:
                                logs.append("=== METADATA ===\n" + mlog)
                            self.metadata.emit(it.uid, duration, entries_count)
                        except Exception:
                            pass

                    # It can be removed while playlist metadata is being checked.
                    if self.is_skipped(it.uid):
                        continue

                    # Create the final destination only when a real download
                    # is about to start. Previewing/typing a path must not touch
                    # the filesystem.
                    try:
                        dest_path(it.destination, self.cfg).mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        raise AppError("E08", tr(lang, "e08")) from exc

                    source_override = None
                    rc, dlog = self.run_proc(build_cmd(it, self.cfg, override, source_url=source_override))
                    logs.append("=== DOWNLOAD — ATTEMPT 1 ===\n" + dlog)

                    # Premium audio (including Video+audio) can require the
                    # music.youtube.com client. If that endpoint rejects a
                    # non-music item, retry the original YouTube URL instead.
                    music_fallback_error = any(
                        needle in (dlog or "").lower() for needle in (
                            "unsupported url", "video unavailable", "this video is unavailable",
                            "requested format is not available", "no video formats found",
                            "not available on this app", "sign in", "login required",
                        )
                    )
                    if (
                        rc != 0
                        and download_source_url(it) != it.url
                        and music_fallback_error
                        and not self.stop_req
                    ):
                        self.log.emit(tr(lang, "m4a_best_fallback"))
                        source_override = it.url
                        rc, dlog = self.run_proc(build_cmd(it, self.cfg, override, source_url=source_override))
                        logs.append("=== MUSIC CLIENT — ORIGINAL YOUTUBE FALLBACK ===\n" + dlog)

                    # Track unresolved playlist errors by original playlist
                    # index. Retry only transient failures when we know the
                    # exact index. Already downloaded entries are not touched.
                    unresolved = {}
                    unindexed_retryable = []

                    if it.playlist and rc != 0:
                        for err in playlist_errors(dlog):
                            if err["index"] is not None:
                                unresolved[err["index"]] = err
                            elif err["retryable"]:
                                unindexed_retryable.append(err)

                        pending = sorted(
                            idx for idx, err in unresolved.items()
                            if err["retryable"]
                        )

                        # If the exact failed playlist entry is known, retry
                        # only that/those entries. This preserves playlist
                        # metadata such as track number and album tags.
                        attempt = 2
                        while pending and attempt <= self.MAX_ATTEMPTS and not self.stop_req:
                            delay = self.RETRY_DELAYS[min(attempt - 2, len(self.RETRY_DELAYS) - 1)]
                            self.retry_log("retry_wait", seconds=delay)
                            if not self.wait_retry(delay):
                                break

                            spec = ",".join(str(x) for x in pending)
                            what = tr(lang, "retry_playlist_items", items=spec)
                            self.retry_log(
                                "retry_attempt",
                                attempt=attempt,
                                max_attempts=self.MAX_ATTEMPTS,
                                what=what,
                            )

                            rrc, rlog = self.run_proc(build_cmd(it, self.cfg, spec, source_url=source_override))
                            logs.append(f"=== RETRY ATTEMPT {attempt} — PLAYLIST ITEMS {spec} ===\n" + rlog)

                            retry_errors = playlist_errors(rlog)
                            by_index = {
                                err["index"]: err
                                for err in retry_errors
                                if err["index"] is not None
                            }

                            # A requested index with no ERROR in the retry log
                            # recovered successfully.
                            for idx in list(pending):
                                if idx not in by_index:
                                    unresolved.pop(idx, None)

                            # Keep the latest failure for entries still failing.
                            for idx, err in by_index.items():
                                unresolved[idx] = err

                            pending = sorted(
                                idx for idx in pending
                                if idx in unresolved and unresolved[idx]["retryable"]
                            )

                            if not pending:
                                self.retry_log("retry_success")
                                break
                            attempt += 1

                        # Rare case: retryable playlist-level error occurred
                        # before yt-dlp exposed a track_number. Retry the whole
                        # command; --no-overwrites prevents replacing files
                        # already completed by an earlier attempt.
                        if (
                            unindexed_retryable
                            and not self.stop_req
                            and not unresolved
                        ):
                            whole_rc = rc
                            attempt = 2
                            while whole_rc != 0 and attempt <= self.MAX_ATTEMPTS and not self.stop_req:
                                delay = self.RETRY_DELAYS[min(attempt - 2, len(self.RETRY_DELAYS) - 1)]
                                self.retry_log("retry_wait", seconds=delay)
                                if not self.wait_retry(delay):
                                    break
                                self.retry_log(
                                    "retry_attempt",
                                    attempt=attempt,
                                    max_attempts=self.MAX_ATTEMPTS,
                                    what=tr(lang, "retry_item"),
                                )
                                whole_rc, rlog = self.run_proc(build_cmd(it, self.cfg, override, source_url=source_override))
                                logs.append(f"=== RETRY ATTEMPT {attempt} — FULL PLAYLIST ===\n" + rlog)
                                if whole_rc == 0:
                                    unindexed_retryable.clear()
                                    self.retry_log("retry_success")
                                    break
                                attempt += 1

                        # rc reflects the final logical result, not merely the
                        # first yt-dlp process exit code.
                        if not unresolved and not unindexed_retryable:
                            rc = 0

                    elif not it.playlist and rc != 0 and is_retryable_error_text(dlog):
                        # Single video/audio: retry the same command up to
                        # three total attempts.
                        attempt = 2
                        while rc != 0 and attempt <= self.MAX_ATTEMPTS and not self.stop_req:
                            delay = self.RETRY_DELAYS[min(attempt - 2, len(self.RETRY_DELAYS) - 1)]
                            self.retry_log("retry_wait", seconds=delay)
                            if not self.wait_retry(delay):
                                break

                            self.retry_log(
                                "retry_attempt",
                                attempt=attempt,
                                max_attempts=self.MAX_ATTEMPTS,
                                what=tr(lang, "retry_item"),
                            )
                            rc, rlog = self.run_proc(build_cmd(it, self.cfg, override, source_url=source_override))
                            logs.append(f"=== RETRY ATTEMPT {attempt} ===\n" + rlog)

                            if rc == 0:
                                self.retry_log("retry_success")
                                break

                            # Do not keep retrying if the new failure turned
                            # into a permanent error such as private/unavailable.
                            if not is_retryable_error_text(rlog):
                                break
                            attempt += 1

                    full = "\n\n".join(logs)
                    elapsed = max(0.0, time.monotonic() - item_started)
                    sample_valid = (
                        "[download] Destination:" in full or
                        "[download] 100% of" in full
                    )

                    if self.stop_req:
                        stopped = True
                        self.done.emit(it.uid, False, "STOP", tr(lang, "download_stopped"), full, elapsed, duration, False)
                        break

                    if rc == 0:
                        if drc_fallback_used(it, full):
                            note = tr(lang, "drc_fallback_desc")
                            self.log.emit(note)
                            self.done.emit(it.uid, True, "OK_DRC_FALLBACK", note, full, elapsed, duration, sample_valid)
                        else:
                            self.done.emit(it.uid, True, "OK", tr(lang, "download_ok"), full, elapsed, duration, sample_valid)
                    elif it.playlist and unresolved and has_download_success(full):
                        # Only failures that remain after all retries count.
                        desc = tr(lang, "partial_playlist", n=len(unresolved))
                        self.done.emit(it.uid, False, "W01", desc, full, elapsed, duration, sample_valid)
                    else:
                        partial = partial_playlist_result(full, lang) if it.playlist else None
                        if partial is not None:
                            code, desc = partial
                            self.done.emit(it.uid, False, code, desc, full, elapsed, duration, sample_valid)
                        else:
                            code, desc = classify(full, lang, self.cfg)
                            self.done.emit(it.uid, False, code, desc, full, elapsed, duration, False)

                except AppError as e:
                    elapsed = max(0.0, time.monotonic() - item_started)
                    full = "\n\n".join(logs + [e.details])
                    self.done.emit(it.uid, False, e.code, e.desc, full, elapsed, duration, False)
                    self.log.emit(f"{e.code}: {e.desc}")

                except Exception as e:
                    elapsed = max(0.0, time.monotonic() - item_started)
                    full = "\n\n".join(logs + [repr(e)])
                    self.done.emit(it.uid, False, "E99", tr(lang, "app_unknown"), full, elapsed, duration, False)
                    self.log.emit(f"E99: {e}")

        finally:
            self.all_done.emit(stopped)

class TextDialog(QDialog):
    def __init__(self, parent, title, text, lang):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 520)
        self.setMinimumSize(360, 240)

        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(edit)
        layout.addWidget(buttons)


def apply_app_theme(app, mode):
    """Apply an app-only theme without changing the desktop theme."""
    if app is None:
        return
    mode = str(mode or "system").lower()
    if not hasattr(app, "_yt_system_palette"):
        app._yt_system_palette = QPalette(app.palette())

    if mode == "system":
        app.setPalette(QPalette(app._yt_system_palette))
        return

    pal = QPalette()
    if mode == "dark":
        colors = {
            QPalette.ColorRole.Window: QColor(32, 32, 32),
            QPalette.ColorRole.WindowText: QColor(238, 238, 238),
            QPalette.ColorRole.Base: QColor(24, 24, 24),
            QPalette.ColorRole.AlternateBase: QColor(38, 38, 38),
            QPalette.ColorRole.ToolTipBase: QColor(45, 45, 45),
            QPalette.ColorRole.ToolTipText: QColor(245, 245, 245),
            QPalette.ColorRole.Text: QColor(238, 238, 238),
            QPalette.ColorRole.Button: QColor(45, 45, 45),
            QPalette.ColorRole.ButtonText: QColor(238, 238, 238),
            QPalette.ColorRole.BrightText: QColor(255, 90, 90),
            QPalette.ColorRole.Highlight: QColor(55, 125, 210),
            QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
            QPalette.ColorRole.Link: QColor(100, 170, 255),
            QPalette.ColorRole.PlaceholderText: QColor(150, 150, 150),
        }
    else:
        colors = {
            QPalette.ColorRole.Window: QColor(246, 246, 246),
            QPalette.ColorRole.WindowText: QColor(24, 24, 24),
            QPalette.ColorRole.Base: QColor(255, 255, 255),
            QPalette.ColorRole.AlternateBase: QColor(240, 240, 240),
            QPalette.ColorRole.ToolTipBase: QColor(255, 255, 255),
            QPalette.ColorRole.ToolTipText: QColor(20, 20, 20),
            QPalette.ColorRole.Text: QColor(24, 24, 24),
            QPalette.ColorRole.Button: QColor(244, 244, 244),
            QPalette.ColorRole.ButtonText: QColor(24, 24, 24),
            QPalette.ColorRole.BrightText: QColor(190, 0, 0),
            QPalette.ColorRole.Highlight: QColor(48, 125, 200),
            QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
            QPalette.ColorRole.Link: QColor(0, 95, 190),
            QPalette.ColorRole.PlaceholderText: QColor(115, 115, 115),
        }
    for role, color in colors.items():
        pal.setColor(role, color)
    # Keep disabled controls readable in both forced themes.
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(125, 125, 125))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(125, 125, 125))
    app.setPalette(pal)


def _version_tuple(value):
    nums = re.findall(r"\d+", str(value or ""))
    return tuple(int(x) for x in nums[:4]) or (0,)


def github_ssl_context():
    """Return a TLS context that works reliably inside the frozen AppImage.

    A PyInstaller-frozen Python may not know the host distribution's CA bundle
    location.  AppImage builds therefore bundle certifi and use its Mozilla CA
    store for GitHub HTTPS.  Non-frozen/source builds can still fall back to
    the system trust store if certifi is not installed.
    """
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def github_urlopen(request, timeout):
    return urlopen(request, timeout=timeout, context=github_ssl_context())


def github_latest_release(timeout=10):
    req = Request(
        GITHUB_RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{APP_NAME}/{VERSION}",
        },
    )
    try:
        with github_urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    tag = str(payload.get("tag_name") or "").lstrip("vV")
    assets = payload.get("assets") or []
    appimage = next(
        (
            a for a in assets
            if str(a.get("name") or "").lower().endswith(".appimage")
            and ("x86_64" in str(a.get("name") or "").lower() or "x64" in str(a.get("name") or "").lower())
        ),
        None,
    )
    return {
        "version": tag,
        "page": payload.get("html_url") or GITHUB_RELEASES_URL,
        "asset_name": (appimage or {}).get("name"),
        "asset_url": (appimage or {}).get("browser_download_url"),
    }


def install_appimage_release(release):
    if not _APPIMAGE_PATH:
        raise RuntimeError("not running from AppImage")
    source = Path(_APPIMAGE_PATH).expanduser().resolve()
    url = str((release or {}).get("asset_url") or "")
    if not url:
        raise RuntimeError("release does not contain an x86_64 AppImage asset")
    if not source.exists():
        raise RuntimeError(f"current AppImage not found: {source}")

    tmp = source.with_name(source.name + ".download")
    backup = source.with_name(source.name + ".old")
    req = Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
    try:
        with github_urlopen(req, timeout=60) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out)
        if tmp.stat().st_size < 1024 * 1024:
            raise RuntimeError("downloaded AppImage is unexpectedly small")
        with tmp.open("rb") as f:
            if f.read(4) != b"\x7fELF":
                raise RuntimeError("downloaded file is not an ELF/AppImage")
        tmp.chmod(source.stat().st_mode | 0o111)

        try:
            backup.unlink(missing_ok=True)
        except Exception:
            pass
        os.replace(source, backup)
        try:
            os.replace(tmp, source)
        except Exception:
            os.replace(backup, source)
            raise
        try:
            backup.unlink(missing_ok=True)
        except Exception:
            pass
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


class Settings(QDialog):
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.base_cfg = dict(cfg)
        self._shared_cfg = cfg
        self.lang = cfg.get("language", "en")
        self.setWindowTitle(tr(self.lang, "settings_title"))
        self.setMinimumSize(420, 300)
        try:
            settings_w = max(420, int(cfg.get("settings_width", 638)))
            settings_h = max(300, int(cfg.get("settings_height", 752)))
        except Exception:
            settings_w, settings_h = 760, 520
        self.resize(settings_w, settings_h)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # W ustawieniach nie pokazuj niebieskiej ramki fokusu wokół całego
        # przewijanego obszaru. Fokus powinien być widoczny na kontrolce,
        # z którą użytkownik aktualnie pracuje, nie na całym panelu.
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        content = QWidget()
        content.setMinimumWidth(560)
        scroll.setWidget(content)

        main = QVBoxLayout(content)
        main.setSpacing(8)

        self.language = NoWheelComboBox()
        self.language.addItem(tr(self.lang, "polish"), "pl")
        self.language.addItem(tr(self.lang, "english"), "en")
        idx = self.language.findData(cfg.get("language", "en"))
        self.language.setCurrentIndex(max(0, idx))

        self.dir = QLineEdit(cfg["default_download_dir"])
        dir_btn = QPushButton(tr(self.lang, "choose"))
        dir_btn.clicked.connect(self.choose_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.dir, 1)
        dir_row.addWidget(dir_btn)

        self.sub = QLineEdit(cfg.get("default_subfolder", ""))
        self.sub.setPlaceholderText(tr(self.lang, "optional"))

        self.defpl = QCheckBox(tr(self.lang, "default_playlist"))
        self.defpl.setChecked(cfg.get("default_playlist", False))

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.addRow(tr(self.lang, "language"), self.language)
        form.addRow(tr(self.lang, "default_folder"), dir_row)
        form.addRow(tr(self.lang, "default_subfolder"), self.sub)
        form.addRow("", self.defpl)
        main.addLayout(form)

        self.browser = NoWheelComboBox()
        for value, label_key in BROWSERS:
            label = tr(self.lang, label_key) if label_key in TR[self.lang] else label_key
            self.browser.addItem(label, value)
        self.browser.setCurrentIndex(
            max(0, self.browser.findData(cfg.get("cookies_browser", "none")))
        )
        self.browser.setMaximumWidth(320)

        self.profile = QLineEdit(cfg.get("cookies_profile", ""))
        self.profile.setPlaceholderText(tr(self.lang, "profile_hint"))
        self.profile_btn = QPushButton(tr(self.lang, "choose_profile"))
        self.profile_btn.clicked.connect(self.choose_profile)
        profile_row = QHBoxLayout()
        profile_row.addWidget(self.profile, 1)
        profile_row.addWidget(self.profile_btn)

        self.keyring = NoWheelComboBox()
        for value, label_key in KEYRINGS:
            label = tr(self.lang, label_key) if label_key in TR[self.lang] else label_key
            self.keyring.addItem(label, value)
        self.keyring.setCurrentIndex(
            max(0, self.keyring.findData(cfg.get("cookies_keyring", "")))
        )
        self.keyring.setMaximumWidth(210)

        self.browser.currentIndexChanged.connect(self.cookie_state)

        cookie_box = QGroupBox(tr(self.lang, "cookies_group"))
        cookie_form = QFormLayout(cookie_box)
        cookie_form.setHorizontalSpacing(10)
        cookie_form.setVerticalSpacing(8)
        cookie_form.addRow(tr(self.lang, "browser"), self.browser)
        cookie_form.addRow(tr(self.lang, "profile"), profile_row)
        cookie_form.addRow(tr(self.lang, "keyring"), self.keyring)

        self.cookie_file_enabled = bool(cfg.get("cookies_file_enabled", False) and _valid_cookie_cache(MANUAL_COOKIE_FILE))
        if os.name == "nt":
            self.cookie_file_status = QLabel(
                tr(self.lang, "cookie_file_ready") if self.cookie_file_enabled else tr(self.lang, "cookie_file_none")
            )
            self.cookie_file_status.setWordWrap(True)
            self.cookie_file_import_btn = QPushButton(tr(self.lang, "cookie_file_import"))
            self.cookie_file_remove_btn = QPushButton(tr(self.lang, "cookie_file_remove"))
            self.cookie_file_import_btn.clicked.connect(self.choose_cookie_file)
            self.cookie_file_remove_btn.clicked.connect(self.remove_cookie_file)
            cookie_file_widget = QWidget()
            cookie_file_row = QHBoxLayout(cookie_file_widget)
            cookie_file_row.setContentsMargins(0, 0, 0, 0)
            cookie_file_row.addWidget(self.cookie_file_status, 1)
            cookie_file_row.addWidget(self.cookie_file_import_btn)
            cookie_file_row.addWidget(self.cookie_file_remove_btn)
            cookie_form.addRow(tr(self.lang, "cookie_file"), cookie_file_widget)

            self.brave_ext_status = QLabel()
            self.brave_ext_status.setWordWrap(True)
            self.brave_ext_install_btn = QPushButton(tr(self.lang, "brave_ext_install"))
            self.brave_ext_refresh_btn = QPushButton(tr(self.lang, "brave_ext_refresh"))
            self.brave_ext_install_btn.clicked.connect(self.open_brave_extension_setup)
            self.brave_ext_refresh_btn.clicked.connect(self.refresh_brave_extension_status)
            brave_ext_widget = QWidget()
            brave_ext_row = QHBoxLayout(brave_ext_widget)
            brave_ext_row.setContentsMargins(0, 0, 0, 0)
            brave_ext_row.addWidget(self.brave_ext_status, 1)
            brave_ext_row.addWidget(self.brave_ext_install_btn)
            brave_ext_row.addWidget(self.brave_ext_refresh_btn)
            cookie_form.addRow(tr(self.lang, "brave_ext"), brave_ext_widget)
            self.refresh_brave_extension_status()

        note = QLabel(tr(self.lang, "cookies_note"))
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        cookie_form.addRow(note)
        main.addWidget(cookie_box)

        self.meta = QCheckBox(tr(self.lang, "embed_meta"))
        self.meta.setChecked(cfg.get("embed_metadata", True))
        self.thumb = QCheckBox(tr(self.lang, "embed_thumb"))
        self.thumb.setChecked(cfg.get("embed_thumbnail", True))
        self.noow = QCheckBox(tr(self.lang, "no_overwrite"))
        self.noow.setChecked(cfg.get("no_overwrites", True))

        files_box = QGroupBox(tr(self.lang, "files"))
        files_layout = QVBoxLayout(files_box)
        files_layout.addWidget(self.meta)
        files_layout.addWidget(self.thumb)
        files_layout.addWidget(self.noow)
        main.addWidget(files_box)

        self.theme = NoWheelComboBox()
        self.theme.addItem(tr(self.lang, "theme_system"), "system")
        self.theme.addItem(tr(self.lang, "theme_light"), "light")
        self.theme.addItem(tr(self.lang, "theme_dark"), "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(cfg.get("theme", "system"))))

        self.show_history = QCheckBox(tr(self.lang, "show_history"))
        self.show_history.setChecked(cfg.get("show_history", True))
        self.confirm_clear_history = QCheckBox(tr(self.lang, "confirm_clear_history"))
        self.confirm_clear_history.setChecked(cfg.get("confirm_clear_history", False))
        self.show_log = QCheckBox(tr(self.lang, "show_current_log"))
        self.show_log.setChecked(cfg.get("show_log", True))
        self.show_presets = QCheckBox(tr(self.lang, "show_presets"))
        self.show_presets.setChecked(cfg.get("show_presets", True))
        self.show_advanced = QCheckBox(tr(self.lang, "show_advanced"))
        self.show_advanced.setChecked(cfg.get("show_advanced", True))

        interface_box = QGroupBox(tr(self.lang, "interface_group"))
        interface_form = QFormLayout(interface_box)
        interface_form.setHorizontalSpacing(10)
        interface_form.setVerticalSpacing(8)
        interface_form.addRow(tr(self.lang, "theme"), self.theme)
        # Najczęściej przełączane elementy głównego formularza są na górze.
        interface_form.addRow("", self.show_presets)
        interface_form.addRow("", self.show_advanced)
        interface_form.addRow("", self.show_history)
        interface_form.addRow("", self.confirm_clear_history)
        interface_form.addRow("", self.show_log)

        # Użytkownik steruje tylko wysokością trzech dolnych segmentów.
        # Ta sekcja jest osobnym, spójnym kafelkiem zamiast surowej tabelki
        # wciśniętej w prawą kolumnę formularza.
        rows_box = QGroupBox(tr(self.lang, "panel_rows_title"))
        rows_grid = QGridLayout(rows_box)
        rows_grid.setContentsMargins(12, 10, 12, 10)
        rows_grid.setHorizontalSpacing(10)
        rows_grid.setVerticalSpacing(7)

        min_header = QLabel(tr(self.lang, "panel_rows_min"))
        max_header = QLabel(tr(self.lang, "panel_rows_max"))
        min_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        max_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rows_grid.addWidget(min_header, 0, 2)
        rows_grid.addWidget(max_header, 0, 3)

        def make_rows_spin(value):
            spin = CenteredDisplaySpinBox()
            spin.setRange(1, 50)
            spin.setValue(int(value))

            # To są małe pola na wartości 1..50, więc nie powinny wyglądać
            # jak pełnowymiarowe pola tekstowe. 48 px mieści dwie cyfry oraz
            # natywne przyciski góra/dół, ale nie zostawia pustej przestrzeni.
            spin.setFixedWidth(48)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # Nie przesuwamy geometrii QLineEdit ani nie zgadujemy marginesów.
            # Wbudowany edytor nadal przyjmuje wpisy z klawiatury, ale jego
            # własny tekst jest przezroczysty. CenteredDisplaySpinBox pokazuje
            # tę samą wartość na nakładce wycentrowanej względem całych 48 px,
            # niezależnie od szerokości natywnej kolumny strzałek KDE/Qt.
            spin.setStyleSheet("QSpinBox { padding-left: 0px; padding-right: 0px; }")
            editor = spin.lineEdit()
            if editor is not None:
                editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
                editor.setTextMargins(0, 0, 0, 0)
                editor.setStyleSheet(
                    "QLineEdit { border: none; border-radius: 0px; "
                    "background: transparent; padding: 0px; margin: 0px; "
                    "color: transparent; selection-color: transparent; }"
                )
            return spin

        self.queue_rows_min = make_rows_spin(cfg.get("queue_rows_min", 3))
        self.queue_rows_max = make_rows_spin(cfg.get("queue_rows_max", 5))
        self.history_rows_min = make_rows_spin(cfg.get("history_rows_min", 2))
        self.history_rows_max = make_rows_spin(cfg.get("history_rows_max", 3))
        self.log_rows_min = make_rows_spin(cfg.get("log_rows_min", 2))
        self.log_rows_max = make_rows_spin(cfg.get("log_rows_max", 3))

        for row, (label_key, min_spin, max_spin) in enumerate((
            ("panel_rows_queue", self.queue_rows_min, self.queue_rows_max),
            ("panel_rows_history", self.history_rows_min, self.history_rows_max),
            ("panel_rows_log", self.log_rows_min, self.log_rows_max),
        ), start=1):
            row_label = QLabel(tr(self.lang, label_key))
            row_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rows_grid.addWidget(row_label, row, 1)
            rows_grid.addWidget(min_spin, row, 2)
            rows_grid.addWidget(max_spin, row, 3)

        rows_hint = QLabel(tr(self.lang, "panel_rows_hint"))
        rows_hint.setWordWrap(True)
        rows_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rows_grid.addWidget(rows_hint, 4, 1, 1, 3)

        # Symetryczne elastyczne kolumny utrzymują cały blok Min./Maks.
        # pośrodku kafelka również przy szerokim oknie ustawień.
        rows_grid.setColumnStretch(0, 1)
        rows_grid.setColumnStretch(4, 1)
        rows_grid.setColumnMinimumWidth(1, 105)
        interface_form.addRow(rows_box)
        main.addWidget(interface_box)

        if os.name != "nt" and PORTABLE:
            shortcuts_box = QGroupBox(tr(self.lang, "shortcuts_group"))
            shortcuts_layout = QVBoxLayout(shortcuts_box)
            shortcuts_note = QLabel(tr(self.lang, "shortcuts_note"))
            shortcuts_note.setWordWrap(True)
            shortcuts_layout.addWidget(shortcuts_note)
            shortcuts_row = QHBoxLayout()
            self.shortcut_menu_btn = QPushButton(tr(self.lang, "shortcut_menu"))
            self.shortcut_desktop_btn = QPushButton(tr(self.lang, "shortcut_desktop"))
            self.shortcut_remove_btn = QPushButton(tr(self.lang, "shortcut_remove"))
            self.shortcut_menu_btn.clicked.connect(lambda: self.change_shortcut("menu"))
            self.shortcut_desktop_btn.clicked.connect(lambda: self.change_shortcut("desktop"))
            self.shortcut_remove_btn.clicked.connect(lambda: self.change_shortcut("remove"))
            shortcuts_row.addWidget(self.shortcut_menu_btn)
            shortcuts_row.addWidget(self.shortcut_desktop_btn)
            shortcuts_row.addWidget(self.shortcut_remove_btn)
            shortcuts_row.addStretch(1)
            shortcuts_layout.addLayout(shortcuts_row)
            main.addWidget(shortcuts_box)

        update_box = QGroupBox(tr(self.lang, "updates_group"))
        update_layout = QVBoxLayout(update_box)
        self.update_version_label = QLabel(tr(self.lang, "current_version", version=VERSION))
        update_layout.addWidget(self.update_version_label)
        update_row = QHBoxLayout()
        self.check_versions_btn = QPushButton(tr(self.lang, "check_components"))
        self.update_components_btn = QPushButton(tr(self.lang, "update_components"))
        self.check_versions_btn.clicked.connect(self.show_component_versions)
        self.update_components_btn.clicked.connect(self.update_components)
        update_row.addWidget(self.check_versions_btn)
        update_row.addWidget(self.update_components_btn)
        update_row.addStretch(1)
        update_layout.addLayout(update_row)
        main.addWidget(update_box)

        main.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_button is not None:
            save_button.setText(tr(self.lang, "save"))
        if cancel_button is not None:
            cancel_button.setText(tr(self.lang, "cancel"))

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)
        outer.addWidget(buttons)

        # Settings uses the same exact 28 px control height as the main
        # window: combo boxes, line edits, Choose/Save/Cancel buttons, etc.
        apply_uniform_control_metrics(self)
        self.cookie_state()
        # Rozmiar okna Ustawień jest preferencją interfejsu, więc zapisujemy
        # go także po Anuluj — bez zapisywania zmian formularza.
        self.finished.connect(self.remember_window_size)

    def change_shortcut(self, action):
        try:
            if action == "remove":
                remove_portable_shortcuts()
                message = tr(self.lang, "shortcut_removed")
            else:
                create_portable_shortcut(action)
                message = tr(
                    self.lang,
                    "shortcut_menu_created" if action == "menu" else "shortcut_desktop_created",
                )
            QMessageBox.information(self, APP_NAME, message)
        except Exception as exc:
            QMessageBox.warning(
                self, APP_NAME, tr(self.lang, "shortcut_failed", error=str(exc))
            )

    def remember_window_size(self, _result=None):
        try:
            width = max(self.minimumWidth(), int(self.width()))
            height = max(self.minimumHeight(), int(self.height()))
            self.base_cfg["settings_width"] = width
            self.base_cfg["settings_height"] = height
            if isinstance(self._shared_cfg, dict):
                self._shared_cfg["settings_width"] = width
                self._shared_cfg["settings_height"] = height
                save_cfg(self._shared_cfg)
        except Exception:
            pass

    def cookie_state(self):
        b = self.browser.currentData()
        on = b != "none"
        direct_browser = on and b != "chromium-extension"
        self.profile.setEnabled(direct_browser)
        self.profile_btn.setEnabled(direct_browser)
        self.keyring.setEnabled(
            os.name != "nt" and on and b in {"brave", "chrome", "chromium", "edge", "opera", "opera-gx", "vivaldi"}
        )
        if hasattr(self, "cookie_file_remove_btn"):
            self.cookie_file_remove_btn.setEnabled(bool(self.cookie_file_enabled))
        if hasattr(self, "brave_ext_install_btn"):
            self.brave_ext_install_btn.setEnabled(True)
            self.brave_ext_refresh_btn.setEnabled(True)

    def refresh_brave_extension_status(self):
        if not hasattr(self, "brave_ext_status"):
            return
        ok, count, stamp = extension_cookie_info()
        if ok:
            self.brave_ext_status.setText(tr(self.lang, "brave_ext_ready", count=count, time=stamp))
        else:
            self.brave_ext_status.setText(tr(self.lang, "brave_ext_waiting"))

    def open_brave_extension_setup(self):
        try:
            base = Path(__file__).resolve().parent
            target = base / "extension" / "YT-Downloader-Chromium"
            if not target.is_dir():
                # Backward-compatible fallback for packages made before 0.4.09.
                target = base / "extension" / "YT-Downloader-Brave"
            if not target.is_dir():
                raise FileNotFoundError(str(target))

            # Open ONE level above the folder that must be selected in
            # "Load unpacked". Do not attempt to open a browser URL: Chromium
            # browsers handle their internal extension pages differently.
            if os.name == "nt":
                os.startfile(str(target.parent))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

            QMessageBox.information(
                self,
                tr(self.lang, "chromium_ext_setup_title"),
                tr(self.lang, "chromium_ext_setup_text")
            )
        except Exception as exc:
            QMessageBox.warning(
                self, APP_NAME, tr(self.lang, "brave_ext_open_failed", error=str(exc))
            )

    def choose_cookie_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self,
            tr(self.lang, "cookie_file_dialog"),
            str(Path.home()),
            "cookies.txt (*.txt);;All files (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not p:
            return
        if not import_manual_cookie_file(p):
            QMessageBox.warning(self, APP_NAME, tr(self.lang, "cookie_file_bad"))
            return
        self.cookie_file_enabled = True
        self.cookie_file_status.setText(tr(self.lang, "cookie_file_ready"))
        self.cookie_file_remove_btn.setEnabled(True)
        QMessageBox.information(self, APP_NAME, tr(self.lang, "cookie_file_imported"))

    def remove_cookie_file(self):
        self.cookie_file_enabled = False
        try:
            MANUAL_COOKIE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        self.cookie_file_status.setText(tr(self.lang, "cookie_file_none"))
        self.cookie_file_remove_btn.setEnabled(False)

    def choose_dir(self):
        p = choose_directory_dialog(
            self,
            tr(self.lang, "choose_folder"),
            self.dir.text() or str(Path.home()),
            self.lang,
        )
        if p:
            self.dir.setText(p)

    def choose_profile(self):
        p = choose_directory_dialog(
            self,
            tr(self.lang, "choose_profile_dialog"),
            self.profile.text() or str(Path.home()),
            self.lang,
        )
        if p:
            self.profile.setText(p)

    def show_component_versions(self):
        try:
            release = github_latest_release()
            if not release or not release.get("version"):
                QMessageBox.information(self, APP_NAME, tr(self.lang, "update_no_release"))
                return
            latest = release["version"]
            if _version_tuple(latest) > _version_tuple(VERSION):
                message = (
                    tr(self.lang, "latest_version", version=latest) + "\n" +
                    tr(self.lang, "update_available", version=latest)
                )
            else:
                message = tr(self.lang, "update_none", version=VERSION)
            QMessageBox.information(self, APP_NAME, message)
        except Exception as exc:
            QMessageBox.warning(
                self, APP_NAME, tr(self.lang, "update_failed", error=str(exc))
            )

    def update_components(self):
        try:
            release = github_latest_release()
            if not release or not release.get("version"):
                QMessageBox.information(self, APP_NAME, tr(self.lang, "update_no_release"))
                return
            latest = release["version"]
            if _version_tuple(latest) <= _version_tuple(VERSION):
                QMessageBox.information(self, APP_NAME, tr(self.lang, "update_none", version=VERSION))
                return

            if not _APPIMAGE_PATH:
                QDesktopServices.openUrl(QUrl(release.get("page") or GITHUB_RELEASES_URL))
                QMessageBox.information(self, APP_NAME, tr(self.lang, "update_not_appimage"))
                return

            ans = QMessageBox.question(
                self,
                APP_NAME,
                tr(self.lang, "update_confirm", version=latest),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                install_appimage_release(release)
            finally:
                QApplication.restoreOverrideCursor()
            QMessageBox.information(
                self, APP_NAME, tr(self.lang, "update_downloaded", version=latest)
            )
        except Exception as exc:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            QMessageBox.warning(
                self, APP_NAME, tr(self.lang, "update_failed", error=str(exc))
            )

    def values(self):
        new_lang = self.language.currentData()
        if self.browser.currentData() == "opera-gx" and not self.profile.text().strip():
            raise ValueError(tr(new_lang, "opera_profile_required"))

        row_pairs = (
            ("panel_rows_queue", self.queue_rows_min.value(), self.queue_rows_max.value()),
            ("panel_rows_history", self.history_rows_min.value(), self.history_rows_max.value()),
            ("panel_rows_log", self.log_rows_min.value(), self.log_rows_max.value()),
        )
        for label_key, min_rows, max_rows in row_pairs:
            if min_rows > max_rows:
                raise ValueError(tr(
                    new_lang, "panel_rows_error",
                    panel=tr(new_lang, label_key)
                ))

        v = dict(self.base_cfg)
        v.update(
            language=new_lang,
            default_download_dir=self.dir.text().strip(),
            default_subfolder=self.sub.text().strip(),
            default_playlist=self.defpl.isChecked(),
            cookies_browser=self.browser.currentData(),
            cookies_profile=self.profile.text().strip(),
            cookies_keyring=self.keyring.currentData(),
            cookies_file_enabled=bool(self.cookie_file_enabled and _valid_cookie_cache(MANUAL_COOKIE_FILE)),
            embed_metadata=self.meta.isChecked(),
            embed_thumbnail=self.thumb.isChecked(),
            no_overwrites=self.noow.isChecked(),
            show_history=self.show_history.isChecked(),
            confirm_clear_history=self.confirm_clear_history.isChecked(),
            show_log=self.show_log.isChecked(),
            show_presets=self.show_presets.isChecked(),
            show_advanced=self.show_advanced.isChecked(),
            theme=self.theme.currentData() or "system",
            settings_width=max(self.minimumWidth(), int(self.width())),
            settings_height=max(self.minimumHeight(), int(self.height())),
            queue_rows_min=self.queue_rows_min.value(),
            queue_rows_max=self.queue_rows_max.value(),
            history_rows_min=self.history_rows_min.value(),
            history_rows_max=self.history_rows_max.value(),
            log_rows_min=self.log_rows_min.value(),
            log_rows_max=self.log_rows_max.value(),
        )
        # Stare wersje 0.3.5/0.3.6 mogły mieć zapisane ui_spacing.
        # Od 0.3.7 ta opcja nie istnieje i nie wpływa na interfejs.
        v.pop("ui_spacing", None)
        return v

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        apply_app_theme(QApplication.instance(), self.cfg.get("theme", "system"))
        self.queue = []
        self.errors = {}  # keyed by item uid
        self.item_state = {}
        self.thread = None
        self.worker = None
        self.busy = False
        self.was_stopped = False
        self.active_batch_uids = set()
        self.applying_preset = False

        self.stats_db = StatsDB()
        self.session_uids = set()
        self.session_started_at = None
        self.session_elapsed_final = 0.0
        self.session_downloaded_duration = 0.0
        self.current_uid = None
        self.current_started_at = None

        self.probe_queue = []
        self.probe_thread = None
        self.probe_worker = None
        self.format_probe_thread = None
        self.format_probe_worker = None
        self.last_format_payload = None
        self.last_format_url = ""

        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))

        # Startowy rozmiar zbliżony do 0.2.1, ale samo okno może zostać
        # zmniejszone dalej niż dawniej.
        self.resize(1220, 790)
        self.setMinimumSize(260, 180)

        # Główne okno nadal można mocno zmniejszać. To zawartość ustala
        # naturalne minimum widocznych paneli. Po jego osiągnięciu przewija się
        # środek aplikacji, zamiast ściskać kolejkę / historię / log do zera.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCentralWidget(self.scroll)

        self.content = QWidget()
        self.scroll.setWidget(self.content)

        root = QVBoxLayout(self.content)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        # Jak w 0.3.4: zawartość ma naturalne minimum.
        # Dopóki mieści się w oknie, panele rozciągają się proporcjonalnie.
        # Gdy wysokość okna spadnie poniżej sumy minimów widocznych paneli,
        # QScrollArea uruchamia pionowy pasek przewijania zamiast zgniatać panele.
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        # Pasek z logo/nazwą był redundantny z paskiem tytułu okna i zabierał
        # sporo wysokości. Nagłówek formularza jest teraz zwarty: tytuł sekcji
        # oraz Ustawienia obok siebie.
        self.add_box = QGroupBox()
        self.add_box.setTitle("")
        # Górny formularz ma zachowywać naturalną wysokość. Gdy dolne
        # segmenty dojdą do swoich limitów MAX albo zostaną ukryte, wolne
        # miejsce ma zostać na dole zamiast rozciągać „Dodaj do kolejki”.
        self.add_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        outer = QVBoxLayout(self.add_box)
        outer.setContentsMargins(10, 9, 10, 10)
        outer.setSpacing(7)

        add_header = QGridLayout()
        add_header.setContentsMargins(0, 1, 0, 2)
        add_header.setHorizontalSpacing(8)
        add_header.setVerticalSpacing(0)
        self.add_title_label = QLabel()
        add_title_font = self.add_title_label.font()
        if add_title_font.pointSizeF() > 0:
            add_title_font.setPointSizeF(add_title_font.pointSizeF() + 1.0)
        else:
            add_title_font.setPixelSize(max(13, add_title_font.pixelSize() + 1))
        self.add_title_label.setFont(add_title_font)
        self.add_title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        self.settings_btn = QPushButton()
        self.settings_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.settings_btn.clicked.connect(self.open_settings)
        add_header.addWidget(self.settings_btn, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        add_header.addWidget(self.add_title_label, 0, 1, Qt.AlignmentFlag.AlignCenter)
        # Three equal horizontal zones keep the title visually centered while
        # the Settings button sits above the left side of the form.
        add_header.setColumnStretch(0, 1)
        add_header.setColumnStretch(1, 1)
        add_header.setColumnStretch(2, 1)
        outer.addLayout(add_header)

        # Responsywny formularz:
        # - nie ma już pustych, rozciąganych kolumn między polami,
        # - pola Pobierz / Format / Jakość dostają po tyle samo miejsca,
        # - link i ścieżka rosną i maleją razem z szerokością okna,
        # - naturalne minima kontrolek wyznaczają moment pojawienia się scrolla.
        form_widget = QWidget()
        form_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred
        )
        form = QVBoxLayout(form_widget)
        form.setContentsMargins(4, 3, 4, 3)
        form.setSpacing(8)

        def make_label():
            x = QLabel()
            x.setAlignment(
                Qt.AlignmentFlag.AlignRight |
                Qt.AlignmentFlag.AlignVCenter
            )
            x.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed
            )
            return x

        # --- Link ---------------------------------------------------------
        link_row = QHBoxLayout()
        link_row.setContentsMargins(0, 0, 0, 0)
        link_row.setSpacing(8)

        self.link_label = make_label()
        self.url = QLineEdit()
        self.url.setMinimumWidth(90)
        self.url.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.url.setPlaceholderText("https://www.youtube.com/…")
        self.url.textChanged.connect(self.format_url_changed)

        self.paste_btn = QPushButton()
        self.paste_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.paste_btn.clicked.connect(self.paste_url)

        self.inspect_btn = QPushButton()
        self.inspect_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.inspect_btn.clicked.connect(self.check_formats)

        self.inspect_progress = QProgressBar()
        self.inspect_progress.setRange(0, 0)
        self.inspect_progress.setTextVisible(False)
        self.inspect_progress.setFixedWidth(72)
        self.inspect_progress.setFixedHeight(10)
        self.inspect_progress.setVisible(False)

        link_row.addWidget(self.link_label)
        link_row.addWidget(self.paste_btn)
        link_row.addWidget(self.url, 1)
        link_row.addWidget(self.inspect_progress)
        link_row.addWidget(self.inspect_btn)
        form.addLayout(link_row)

        # --- Tryb ---------------------------------------------------------
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        self.mode_label = make_label()
        self.pl = QCheckBox()
        self.pl.toggled.connect(self.update_pl)
        mode_row.addWidget(self.mode_label)
        mode_row.addWidget(self.pl)
        mode_row.addStretch(1)
        form.addLayout(mode_row)

        # --- Ścieżka ------------------------------------------------------
        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(8)
        self.path_label = make_label()
        self.dest = QLineEdit(self.cfg.get("default_subfolder", ""))
        self.dest.setMinimumWidth(90)
        self.dest.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dest.textChanged.connect(lambda _text: self.update_advanced_preview())
        self.dest_paste_btn = QPushButton()
        self.dest_paste_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.dest_paste_btn.clicked.connect(self.paste_destination)
        self.dest_btn = QPushButton()
        self.dest_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.dest_btn.clicked.connect(self.choose_dest)
        path_row.addWidget(self.path_label)
        path_row.addWidget(self.dest_paste_btn)
        path_row.addWidget(self.dest, 1)
        path_row.addWidget(self.dest_btn)
        form.addLayout(path_row)

        # --- Playlista ----------------------------------------------------
        playlist_row = QHBoxLayout()
        playlist_row.setContentsMargins(0, 0, 0, 0)
        playlist_row.setSpacing(8)
        self.playlist_label_widget = make_label()
        self.select = QCheckBox()
        self.omit = QCheckBox()
        self.select.toggled.connect(self.select_changed)
        self.omit.toggled.connect(self.omit_changed)
        self.numbers_label = QLabel()
        self.nums = QLineEdit()
        self.nums.setMinimumWidth(80)
        self.nums.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        playlist_row.addWidget(self.playlist_label_widget)
        playlist_row.addWidget(self.select)
        playlist_row.addWidget(self.omit)
        playlist_row.addSpacing(16)
        playlist_row.addWidget(self.numbers_label)
        playlist_row.addWidget(self.nums, 1)
        form.addLayout(playlist_row)

        # --- Presety ------------------------------------------------------
        self.preset_widget = QWidget()
        preset_row = QHBoxLayout(self.preset_widget)
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(8)
        self.preset_label = make_label()
        self.preset_combo = NoWheelComboBox()
        self.preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preset_save_btn = QPushButton()
        self.preset_delete_btn = QPushButton()
        self.preset_combo.currentIndexChanged.connect(self.preset_selected)
        self.preset_save_btn.clicked.connect(self.save_current_preset)
        self.preset_delete_btn.clicked.connect(self.delete_current_preset)
        preset_row.addWidget(self.preset_label)
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.preset_save_btn)
        preset_row.addWidget(self.preset_delete_btn)
        form.addWidget(self.preset_widget)

        # --- Wybór trybu pobierania --------------------------------------
        choice_row = QGridLayout()
        choice_row.setContentsMargins(0, 0, 0, 0)
        choice_row.setHorizontalSpacing(8)
        choice_row.setVerticalSpacing(0)
        self.download_label = make_label()
        self.media = NoWheelComboBox()
        self.media.setMinimumWidth(150)
        self.media.setMaximumWidth(220)
        self.media.setMinimumContentsLength(12)
        self.media.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.media.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        for media_id in ("audio", "video", "av"):
            self.media.addItem("", media_id)
        mi = self.media.findData(self.cfg.get("last_media", "audio"))
        if mi >= 0:
            self.media.setCurrentIndex(mi)
        self.media.currentIndexChanged.connect(self.media_changed)
        choice_row.addWidget(self.download_label, 0, 0)
        choice_row.addWidget(self.media, 0, 1)
        choice_row.setColumnStretch(2, 1)
        form.addLayout(choice_row)

        # --- Audio: osobny kafelek, 3 równe miejsca ----------------------
        self.audio_widget = QGroupBox()
        self.audio_layout = QGridLayout(self.audio_widget)
        self.audio_layout.setContentsMargins(9, 7, 9, 7)
        self.audio_layout.setHorizontalSpacing(9)
        self.audio_layout.setVerticalSpacing(7)
        self.format_label_widget = QLabel()
        self.audio_mode_label = QLabel()
        self.quality_label_widget = QLabel()
        self.audio_variant_label = QLabel()
        self.fmt = NoWheelComboBox()
        self.audio_mode = NoWheelComboBox()
        self.quality = NoWheelComboBox()
        self.audio_variant = NoWheelComboBox()
        for combo in (self.fmt, self.audio_mode, self.quality, self.audio_variant):
            combo.setMinimumWidth(90)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.audio_mode.addItem("VBR", "vbr")
        self.audio_mode.addItem("Bitrate", "bitrate")
        self.audio_variant.addItem("", "standard")
        self.audio_variant.addItem("", "drc")
        avi = self.audio_variant.findData(self.cfg.get("last_audio_variant", "standard"))
        self.audio_variant.setCurrentIndex(avi if avi >= 0 else 0)
        self.fmt.currentIndexChanged.connect(self.format_changed)
        self.audio_mode.currentIndexChanged.connect(self.audio_mode_changed)
        self.quality.currentIndexChanged.connect(self.quality_changed)
        self.audio_variant.currentIndexChanged.connect(self.audio_variant_changed)

        self.audio_format_slot = QWidget()
        afs = QHBoxLayout(self.audio_format_slot)
        afs.setContentsMargins(0, 0, 0, 0)
        afs.setSpacing(6)
        afs.addWidget(self.format_label_widget)
        afs.addWidget(self.fmt, 1)
        self.audio_mode_slot = QWidget()
        ams = QHBoxLayout(self.audio_mode_slot)
        ams.setContentsMargins(0, 0, 0, 0)
        ams.setSpacing(6)
        ams.addWidget(self.audio_mode_label)
        ams.addWidget(self.audio_mode, 1)
        self.audio_quality_slot = QWidget()
        aqs = QHBoxLayout(self.audio_quality_slot)
        aqs.setContentsMargins(0, 0, 0, 0)
        aqs.setSpacing(6)
        aqs.addWidget(self.quality_label_widget)
        aqs.addWidget(self.quality, 1)
        self.audio_variant_slot = QWidget()
        avs = QHBoxLayout(self.audio_variant_slot)
        avs.setContentsMargins(0, 0, 0, 0)
        avs.setSpacing(6)
        avs.addWidget(self.audio_variant_label)
        avs.addWidget(self.audio_variant, 1)
        self.audio_layout.addWidget(self.audio_format_slot, 0, 0)
        self.audio_layout.addWidget(self.audio_mode_slot, 0, 1)
        self.audio_layout.addWidget(self.audio_quality_slot, 0, 2)
        for col in range(3):
            self.audio_layout.setColumnStretch(col, 1)
        form.addWidget(self.audio_widget)

        # --- Obraz: trzy zwarte rzędy po trzy pary -----------------------
        self.smart_video_widget = QGroupBox()
        vr = QGridLayout(self.smart_video_widget)
        vr.setContentsMargins(9, 7, 9, 7)
        vr.setHorizontalSpacing(9)
        vr.setVerticalSpacing(7)
        self.smart_container_label = QLabel()
        self.smart_resolution_label = QLabel()
        self.smart_fps_label = QLabel()
        self.smart_codec_label = QLabel()
        self.smart_tier_label = QLabel()
        self.smart_bitrate_label = QLabel()
        self.smart_container = NoWheelComboBox()
        self.smart_resolution = NoWheelComboBox()
        self.smart_fps = NoWheelComboBox()
        self.smart_codec = NoWheelComboBox()
        self.smart_tier = NoWheelComboBox()
        self.smart_bitrate = NoWheelComboBox()
        for combo in (self.smart_container, self.smart_resolution, self.smart_fps, self.smart_codec, self.smart_tier, self.smart_bitrate):
            combo.setMinimumWidth(80)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.currentIndexChanged.connect(self.smart_rule_changed)
        vr.addWidget(self.smart_container_label, 0, 0)
        vr.addWidget(self.smart_container, 0, 1)
        vr.addWidget(self.smart_resolution_label, 0, 2)
        vr.addWidget(self.smart_resolution, 0, 3)
        vr.addWidget(self.smart_fps_label, 0, 4)
        vr.addWidget(self.smart_fps, 0, 5)
        vr.addWidget(self.smart_codec_label, 1, 0)
        vr.addWidget(self.smart_codec, 1, 1)
        vr.addWidget(self.smart_tier_label, 1, 2)
        vr.addWidget(self.smart_tier, 1, 3)
        vr.addWidget(self.smart_bitrate_label, 1, 4)
        vr.addWidget(self.smart_bitrate, 1, 5)

        # Napisy są częścią kafelka Obraz i zajmują trzeci rząd.
        self.subtitle_label = QLabel()
        self.subtitle_mode = NoWheelComboBox()
        for sid in ("none", "manual", "auto", "fallback"):
            self.subtitle_mode.addItem("", sid)
        self.subtitle_lang_label = QLabel()
        self.subtitle_lang = NoWheelComboBox()
        self.subtitle_lang.addItem("", "pl")
        self.subtitle_lang.addItem("", "en")
        self.subtitle_output_label = QLabel()
        self.subtitle_output = NoWheelComboBox()
        for oid in ("separate", "embed", "both"):
            self.subtitle_output.addItem("", oid)
        for combo in (self.subtitle_mode, self.subtitle_lang, self.subtitle_output):
            combo.setMinimumWidth(80)
            combo.setMinimumContentsLength(3)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.subtitle_mode.currentIndexChanged.connect(self.subtitle_changed)
        self.subtitle_lang.currentIndexChanged.connect(self.subtitle_changed)
        self.subtitle_output.currentIndexChanged.connect(self.subtitle_changed)
        vr.addWidget(self.subtitle_label, 2, 0)
        vr.addWidget(self.subtitle_mode, 2, 1)
        vr.addWidget(self.subtitle_lang_label, 2, 2)
        vr.addWidget(self.subtitle_lang, 2, 3)
        vr.addWidget(self.subtitle_output_label, 2, 4)
        vr.addWidget(self.subtitle_output, 2, 5)
        vr.setColumnStretch(1, 1)
        vr.setColumnStretch(3, 1)
        vr.setColumnStretch(5, 1)
        form.addWidget(self.smart_video_widget)

        # --- Audio w trybie Obraz + audio: dwa z trzech miejsc -----------
        self.smart_audio_widget = QGroupBox()
        ar = QGridLayout(self.smart_audio_widget)
        ar.setContentsMargins(9, 7, 9, 7)
        ar.setHorizontalSpacing(9)
        ar.setVerticalSpacing(7)
        self.smart_audio_format_label = QLabel()
        self.smart_audio_quality_label = QLabel()
        self.smart_audio_variant_label = QLabel()
        self.smart_audio_format = NoWheelComboBox()
        self.smart_audio_quality = NoWheelComboBox()
        self.smart_audio_variant = NoWheelComboBox()
        for combo in (self.smart_audio_format, self.smart_audio_quality, self.smart_audio_variant):
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.smart_audio_variant.addItem("", "standard")
        self.smart_audio_variant.addItem("", "drc")
        svi = self.smart_audio_variant.findData(self.cfg.get("smart_av_audio_variant", "standard"))
        self.smart_audio_variant.setCurrentIndex(svi if svi >= 0 else 0)
        self.smart_audio_format.currentIndexChanged.connect(self.smart_audio_changed)
        self.smart_audio_quality.currentIndexChanged.connect(self.smart_audio_changed)
        self.smart_audio_variant.currentIndexChanged.connect(self.smart_audio_changed)
        self.smart_audio_format_slot = QWidget()
        safs = QHBoxLayout(self.smart_audio_format_slot)
        safs.setContentsMargins(0, 0, 0, 0)
        safs.setSpacing(6)
        safs.addWidget(self.smart_audio_format_label)
        safs.addWidget(self.smart_audio_format, 1)
        self.smart_audio_quality_slot = QWidget()
        saqs = QHBoxLayout(self.smart_audio_quality_slot)
        saqs.setContentsMargins(0, 0, 0, 0)
        saqs.setSpacing(6)
        saqs.addWidget(self.smart_audio_quality_label)
        saqs.addWidget(self.smart_audio_quality, 1)
        self.smart_audio_variant_slot = QWidget()
        savs = QHBoxLayout(self.smart_audio_variant_slot)
        savs.setContentsMargins(0, 0, 0, 0)
        savs.setSpacing(6)
        savs.addWidget(self.smart_audio_variant_label)
        savs.addWidget(self.smart_audio_variant, 1)
        ar.addWidget(self.smart_audio_format_slot, 0, 0)
        ar.addWidget(self.smart_audio_quality_slot, 0, 1)
        ar.addWidget(self.smart_audio_variant_slot, 0, 2)
        ar.setColumnStretch(0, 1)
        ar.setColumnStretch(1, 1)
        ar.setColumnStretch(2, 1)
        form.addWidget(self.smart_audio_widget)

        # --- Zaawansowane yt-dlp -----------------------------------------
        self.advanced_section = QWidget()
        advanced_section_layout = QVBoxLayout(self.advanced_section)
        advanced_section_layout.setContentsMargins(0, 0, 0, 0)
        # Tylko ta część formularza jest ciaśniejsza: przycisk Dodaj ma być
        # bliżej „Zaawansowane yt-dlp”, bez zmieniania odstępów reszty UI.
        advanced_section_layout.setSpacing(3)

        self.advanced_toggle = QPushButton()
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setFlat(True)
        self.advanced_toggle.setChecked(bool(self.cfg.get("advanced_expanded", False)))
        self.advanced_toggle.toggled.connect(self.advanced_toggled)
        advanced_section_layout.addWidget(self.advanced_toggle)

        self.advanced_body = QWidget()
        adv = QGridLayout(self.advanced_body)
        adv.setContentsMargins(8, 4, 8, 4)
        adv.setHorizontalSpacing(6)
        adv.setVerticalSpacing(7)
        self.advanced_selector_label = QLabel()
        self.advanced_args_label = QLabel()
        self.advanced_preview_label = QLabel()
        self.advanced_selector = QLineEdit(str(self.cfg.get("advanced_selector", "")))
        self.advanced_args = QLineEdit(str(self.cfg.get("advanced_args", "")))
        self.advanced_preview = QLineEdit()
        self.advanced_preview.setReadOnly(True)
        self.advanced_preview_left = QPushButton("◀")
        self.advanced_preview_right = QPushButton("▶")
        self.advanced_preview_left.setFixedWidth(30)
        self.advanced_preview_right.setFixedWidth(30)
        self.advanced_copy_btn = QPushButton()
        self.advanced_selector.textChanged.connect(self.advanced_changed)
        self.advanced_args.textChanged.connect(self.advanced_changed)
        self.advanced_preview_left.clicked.connect(lambda: self.scroll_advanced_preview(-24))
        self.advanced_preview_right.clicked.connect(lambda: self.scroll_advanced_preview(24))
        self.advanced_copy_btn.clicked.connect(self.copy_advanced_preview)
        adv.addWidget(self.advanced_selector_label, 0, 0)
        adv.addWidget(self.advanced_selector, 0, 1, 1, 4)
        adv.addWidget(self.advanced_args_label, 1, 0)
        adv.addWidget(self.advanced_args, 1, 1, 1, 4)
        adv.addWidget(self.advanced_preview_label, 2, 0)
        adv.addWidget(self.advanced_preview_left, 2, 1)
        adv.addWidget(self.advanced_preview, 2, 2)
        adv.addWidget(self.advanced_preview_right, 2, 3)
        adv.addWidget(self.advanced_copy_btn, 2, 4)
        adv.setColumnStretch(2, 1)
        self.advanced_body.setVisible(self.advanced_toggle.isChecked())
        advanced_section_layout.addWidget(self.advanced_body)

        # --- Zaawansowane / Dodaj ------------------------------------------
        # Przycisk Dodaj NIE należy do sekcji zaawansowanej. Dzięki temu
        # wyłączenie „Wyświetlaj zaawansowane yt-dlp” chowa wyłącznie tę
        # sekcję, a podstawowa akcja dodawania zawsze pozostaje dostępna.
        form.addWidget(self.advanced_section)
        outer.addWidget(form_widget)

        add_row = QHBoxLayout()
        # Większy oddech od prawej krawędzi niż dla zwykłych kontrolek.
        add_row.setContentsMargins(0, 2, 14, 0)
        add_row.addStretch(1)

        self.add_btn = QPushButton()
        self.add_btn.clicked.connect(self.add_item)
        self.add_btn.setMinimumWidth(78)
        add_font = self.add_btn.font()
        if add_font.pointSizeF() > 0:
            add_font.setPointSizeF(add_font.pointSizeF() + 1.0)
        else:
            add_font.setPixelSize(max(13, add_font.pixelSize() + 1))
        self.add_btn.setFont(add_font)
        add_row.addWidget(self.add_btn)
        outer.addLayout(add_row)

        root.addWidget(self.add_box)

        self.queue_pane = QWidget()
        self.queue_pane.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        queue_pane_layout = QVBoxLayout(self.queue_pane)
        queue_pane_layout.setContentsMargins(0, 0, 0, 0)

        self.queue_box = QGroupBox()
        self.queue_box.setObjectName("primarySectionTitle")
        self.queue_box.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        qv = QVBoxLayout(self.queue_box)

        self.table = QTableWidget(0, 7)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context)
        # Zachowanie tabeli z 0.2.1: kolumny Ścieżka i Link kurczą się wraz
        # z całym oknem. Scroll całego interfejsu pojawia się dopiero po
        # osiągnięciu naturalnego minimum layoutu.
        h = self.table.horizontalHeader()
        for i in (0, 1, 2, 3, 6):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        # Dokładny zakres wysokości (min/max liczby rzędów) jest nakładany
        # przez apply_panel_row_limits() po zbudowaniu wszystkich trzech paneli.
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        qv.addWidget(self.table)

        br = QHBoxLayout()
        self.remove = QPushButton()
        self.clear = QPushButton()
        self.open = QPushButton()
        self.remove.clicked.connect(self.remove_rows)
        self.clear.clicked.connect(self.clear_rows)
        self.open.clicked.connect(self.open_dir)
        br.addWidget(self.remove)
        br.addWidget(self.clear)
        br.addWidget(self.open)
        br.addStretch()
        qv.addLayout(br)

        queue_pane_layout.addWidget(self.queue_box, 1)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        self.stats_label = QLabel()
        self.stats_label.setWordWrap(False)
        self.queue_progress = QProgressBar()
        self.queue_progress.setRange(0, 1000)
        self.queue_progress.setValue(0)
        self.queue_progress.setMinimumWidth(180)
        self.queue_progress.setMaximumWidth(360)
        stats_row.addWidget(self.stats_label, 1)
        stats_row.addWidget(self.queue_progress)
        queue_pane_layout.addLayout(stats_row)

        ar = QHBoxLayout()
        ar.setContentsMargins(0, 2, 14, 0)
        self.stop = QPushButton()
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_dl)
        self.download_all = QPushButton()
        self.download_all.clicked.connect(self.start_dl)
        for action_button in (self.stop, self.download_all):
            action_button.setMinimumWidth(78)
            action_font = action_button.font()
            if action_font.pointSizeF() > 0:
                action_font.setPointSizeF(action_font.pointSizeF() + 1.0)
            else:
                action_font.setPixelSize(max(13, action_font.pixelSize() + 1))
            action_button.setFont(action_font)
        ar.addStretch()
        ar.addWidget(self.stop)
        ar.addWidget(self.download_all)
        queue_pane_layout.addLayout(ar)
        root.addWidget(self.queue_pane, 4)

        # Historia jest celowo NAD logiem.
        # Kolejka / historia / log są zwykłymi elastycznymi panelami layoutu.
        # Ukryty panel nie zabiera miejsca, więc kolejka automatycznie dostaje
        # większą część wysokości.
        self.history_box = QGroupBox()
        self.history_box.setObjectName("primarySectionTitle")
        self.history_box.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        hv = QVBoxLayout(self.history_box)
        self.history_table = QTableWidget(0, 8)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self.history_context)
        hh = self.history_table.horizontalHeader()
        for i in (0, 1, 2, 3, 6, 7):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.history_table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        hv.addWidget(self.history_table)
        hist_buttons = QHBoxLayout()
        self.refresh_history_btn = QPushButton()
        self.clear_history_btn = QPushButton()
        self.refresh_history_btn.clicked.connect(self.refresh_history)
        self.clear_history_btn.clicked.connect(self.clear_download_history)
        hist_buttons.addWidget(self.refresh_history_btn)
        hist_buttons.addWidget(self.clear_history_btn)
        hist_buttons.addStretch(1)
        hv.addLayout(hist_buttons)
        root.addWidget(self.history_box, 2)

        self.log_box = QGroupBox()
        self.log_box.setObjectName("primarySectionTitle")
        self.log_box.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        lv = QVBoxLayout(self.log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(6000)
        self.log.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        lv.addWidget(self.log)
        root.addWidget(self.log_box, 2)

        # Pochłania całą wysokość pozostałą po osiągnięciu MAX widocznych
        # segmentów. Dzięki temu żaden wcześniejszy panel nie jest sztucznie
        # rozciągany.
        root.addStretch(1)

        self.refresh_preset_combo()
        self.restore_last_subtitle_choices()
        self.apply_language()
        self.refresh_formats(remember=False)
        self.update_pl()
        self.apply_ui_preferences()
        self.refresh_history()
        restored = self.restore_queue_state()
        if restored:
            self.append_log(tr(self.lang, "queue_restored", n=restored))

        self.eta_timer = QTimer(self)
        self.eta_timer.setInterval(1000)
        self.eta_timer.timeout.connect(self.update_stats_ui)
        self.eta_timer.start()
        self.update_stats_ui()
        self.apply_compact_control_metrics()

        # Przywróć ostatni rozmiar, stan i (tam, gdzie menedżer okien na to
        # pozwala) pozycję głównego okna dopiero po zbudowaniu całego UI.
        self.restore_window_geometry()

    def apply_compact_control_metrics(self):
        """Use compact metrics, with a slightly stronger primary Add button."""
        apply_uniform_control_metrics(self)
        # Dodaj jest główną akcją formularza, więc może być odrobinę większy
        # niż pomocnicze przyciski 28 px. Robimy to po uniformizacji, aby
        # apply_uniform_control_metrics() nie nadpisało tej wysokości.
        for button in (self.add_btn, self.stop, self.download_all):
            button.setFixedHeight(32)
            button.setMinimumWidth(78)

    @property
    def lang(self):
        return self.cfg.get("language", "en")

    def apply_language(self):
        lang = self.lang
        self.settings_btn.setText(tr(lang, "settings"))
        self.add_title_label.setText(tr(lang, "add_queue"))
        self.link_label.setText(tr(lang, "link"))
        self.paste_btn.setText(tr(lang, "paste"))
        self.inspect_btn.setText(tr(lang, "check_formats"))
        self.mode_label.setText(tr(lang, "mode"))
        self.download_label.setText(tr(lang, "download"))
        self.audio_widget.setTitle(tr(lang, "audio_rules"))
        self.format_label_widget.setText(tr(lang, "format"))
        self.audio_mode_label.setText(tr(lang, "audio_mode"))
        self.quality_label_widget.setText(tr(lang, "quality"))
        self.audio_variant_label.setText(tr(lang, "audio_variant"))
        for i in range(self.audio_variant.count()):
            self.audio_variant.setItemText(i, tr(lang, "audio_variant_drc" if self.audio_variant.itemData(i) == "drc" else "audio_variant_standard"))
        for i in range(self.audio_mode.count()):
            self.audio_mode.setItemText(i, tr(lang, "audio_mode_vbr" if self.audio_mode.itemData(i) == "vbr" else "audio_mode_bitrate"))
        self.smart_video_widget.setTitle(tr(lang, "video_rules"))
        self.smart_audio_widget.setTitle(tr(lang, "audio_rules"))
        self.smart_container_label.setText(tr(lang, "container"))
        self.smart_resolution_label.setText(tr(lang, "resolution"))
        self.smart_fps_label.setText(tr(lang, "fps"))
        self.smart_codec_label.setText(tr(lang, "codec"))
        self.smart_tier_label.setText(tr(lang, "video_tier"))
        self.smart_bitrate_label.setText(tr(lang, "video_bitrate"))
        self.smart_audio_format_label.setText(tr(lang, "audio_format"))
        self.smart_audio_quality_label.setText(tr(lang, "quality"))
        self.smart_audio_variant_label.setText(tr(lang, "audio_variant"))
        for i in range(self.smart_audio_variant.count()):
            self.smart_audio_variant.setItemText(i, tr(lang, "audio_variant_drc" if self.smart_audio_variant.itemData(i) == "drc" else "audio_variant_standard"))
        arrow = "▼" if self.advanced_toggle.isChecked() else "▶"
        self.advanced_toggle.setText(f"{arrow} {tr(lang, 'advanced_title')}")
        self.advanced_selector_label.setText(tr(lang, "advanced_selector"))
        self.advanced_args_label.setText(tr(lang, "advanced_args"))
        self.advanced_preview_label.setText(tr(lang, "advanced_preview"))
        self.advanced_copy_btn.setText(tr(lang, "advanced_copy"))
        self.advanced_selector.setToolTip(tr(lang, "advanced_selector_hint"))
        self.advanced_args.setToolTip(tr(lang, "advanced_args_hint"))
        self.path_label.setText(tr(lang, "path"))
        self.dest_paste_btn.setText(tr(lang, "paste"))
        self.dest_btn.setText(tr(lang, "show_path"))
        self.dest.setPlaceholderText(tr(lang, "path_hint"))
        self.playlist_label_widget.setText(tr(lang, "playlist"))
        self.select.setText(tr(lang, "select"))
        self.omit.setText(tr(lang, "omit"))
        self.numbers_label.setText(tr(lang, "numbers"))
        self.nums.setPlaceholderText(tr(lang, "numbers_hint"))
        self.add_btn.setText(tr(lang, "add"))
        self.preset_label.setText(tr(lang, "preset"))
        self.preset_save_btn.setText(tr(lang, "preset_save_as"))
        self.preset_delete_btn.setText(tr(lang, "preset_delete"))
        self.retranslate_presets()
        self.subtitle_label.setText(tr(lang, "subtitles"))
        self.subtitle_lang_label.setText(tr(lang, "sub_language"))
        self.subtitle_output_label.setText(tr(lang, "sub_output"))
        sub_mode_keys = {"none": "sub_none", "manual": "sub_manual", "auto": "sub_auto", "fallback": "sub_fallback"}
        for i in range(self.subtitle_mode.count()):
            self.subtitle_mode.setItemText(i, tr(lang, sub_mode_keys[self.subtitle_mode.itemData(i)]))
        for i in range(self.subtitle_lang.count()):
            self.subtitle_lang.setItemText(i, tr(lang, "sub_polish" if self.subtitle_lang.itemData(i) == "pl" else "sub_english"))
        sub_out_keys = {"separate": "sub_separate", "embed": "sub_embed", "both": "sub_both"}
        for i in range(self.subtitle_output.count()):
            self.subtitle_output.setItemText(i, tr(lang, sub_out_keys[self.subtitle_output.itemData(i)]))

        self.queue_box.setTitle(tr(lang, "queue"))
        self.table.setHorizontalHeaderLabels([
            tr(lang, "col_mode"),
            tr(lang, "col_media"),
            tr(lang, "col_format"),
            tr(lang, "col_quality"),
            tr(lang, "col_path"),
            tr(lang, "col_link"),
            tr(lang, "col_status"),
        ])
        self.remove.setText(tr(lang, "remove_selected"))
        self.clear.setText(tr(lang, "clear_queue"))
        self.open.setText(tr(lang, "open_default"))
        self.stop.setText(tr(lang, "stop"))
        self.download_all.setText(tr(lang, "download_all"))
        self.log_box.setTitle(tr(lang, "current_log"))
        self.history_box.setTitle(tr(lang, "history"))
        self.history_table.setHorizontalHeaderLabels([
            tr(lang, "history_date"), tr(lang, "history_mode"),
            tr(lang, "col_media"), tr(lang, "col_format"),
            tr(lang, "col_path"), tr(lang, "col_link"),
            tr(lang, "history_result"), tr(lang, "history_time"),
        ])
        self.refresh_history_btn.setText(tr(lang, "refresh"))
        self.clear_history_btn.setText(tr(lang, "clear_history"))
        self.retranslate_queue()
        self.refresh_history()

        # Translate media combo while preserving internal IDs.
        for i in range(self.media.count()):
            mid = self.media.itemData(i)
            self.media.setItemText(i, media_label(lang, mid))

        self.refresh_label()
        self.retranslate_queue()
        self.refresh_formats(remember=False)
        if self.last_format_payload:
            self.populate_exact_variants()
        else:
            self.reset_exact_variants()
        self.refresh_smart_controls()
        self.update_audio_tile_layout()
        self.update_advanced_preview()
        if hasattr(self, "stats_label"):
            self.update_stats_ui()

    def effective_pl(self):
        default = self.cfg.get("default_playlist", False)
        return (not default) if self.pl.isChecked() else default

    def refresh_label(self):
        key = "track_checked" if self.cfg.get("default_playlist", False) else "playlist_checked"
        self.pl.setText(tr(self.lang, key))

    def paste_url(self):
        text = QApplication.clipboard().text().strip()
        if text:
            self.url.setText(text)
        self.url.setFocus()

    def advanced_toggled(self, checked):
        if hasattr(self, "advanced_body"):
            self.advanced_body.setVisible(bool(checked))
        self.cfg["advanced_expanded"] = bool(checked)
        save_cfg(self.cfg)
        if hasattr(self, "advanced_toggle"):
            arrow = "▼" if checked else "▶"
            self.advanced_toggle.setText(f"{arrow} {tr(self.lang, 'advanced_title')}")
        self.update_advanced_preview()

    def advanced_changed(self):
        if not hasattr(self, "advanced_selector"):
            return
        self.cfg["advanced_selector"] = self.advanced_selector.text().strip()
        self.cfg["advanced_args"] = self.advanced_args.text().strip()
        save_cfg(self.cfg)
        self.update_smart_visibility()
        self.mark_preset_custom()

    def set_advanced_selector(self, selector):
        selector = str(selector or "").strip()
        if not selector:
            return
        self.advanced_toggle.setChecked(True)
        self.advanced_selector.setText(selector)
        self.advanced_selector.setFocus()

    def copy_advanced_preview(self):
        text = self.advanced_preview.text().strip()
        if text:
            QApplication.clipboard().setText(text)

    def scroll_advanced_preview(self, delta):
        if not hasattr(self, "advanced_preview"):
            return
        text = self.advanced_preview.text()
        pos = self.advanced_preview.cursorPosition()
        pos = max(0, min(len(text), pos + int(delta)))
        self.advanced_preview.setCursorPosition(pos)
        self.advanced_preview.deselect()
        self.advanced_preview.setFocus(Qt.FocusReason.OtherFocusReason)

    def _preview_item_from_ui(self):
        media = self.media.currentData() or "audio"
        fmt = self.fmt.currentData() or "original"
        quality = self.quality.currentData() or "best"
        url = self.url.text().strip() if hasattr(self, "url") else ""
        if not valid_url(url):
            url = "https://www.youtube.com/watch?v=VIDEO_ID"
        return Item(
            playlist=self.effective_pl() if hasattr(self, "pl") else False,
            destination=self.dest.text().strip() if hasattr(self, "dest") else "",
            url=url,
            media=media,
            fmt=fmt,
            quality=quality,
            subtitle_mode=self.subtitle_mode.currentData() or "none" if hasattr(self, "subtitle_mode") else "none",
            subtitle_lang=self.subtitle_lang.currentData() or "pl" if hasattr(self, "subtitle_lang") else "pl",
            subtitle_output=self.subtitle_output.currentData() or "separate" if hasattr(self, "subtitle_output") else "separate",
            smart_video_container=self.smart_container.currentData() or "auto" if hasattr(self, "smart_container") else "auto",
            smart_video_resolution=self.smart_resolution.currentData() or "auto" if hasattr(self, "smart_resolution") else "auto",
            smart_video_fps=self.smart_fps.currentData() or "auto" if hasattr(self, "smart_fps") else "auto",
            smart_video_codec=self.smart_codec.currentData() or "auto" if hasattr(self, "smart_codec") else "auto",
            smart_video_tier=self.smart_tier.currentData() or "auto" if hasattr(self, "smart_tier") else "auto",
            smart_video_bitrate=self.smart_bitrate.currentData() or "auto" if hasattr(self, "smart_bitrate") else "auto",
            smart_audio_format=self.smart_audio_format.currentData() or "auto" if hasattr(self, "smart_audio_format") else "auto",
            smart_audio_quality=self.smart_audio_quality.currentData() or "best" if hasattr(self, "smart_audio_quality") else "best",
            smart_audio_variant=self.smart_audio_variant.currentData() or "standard" if hasattr(self, "smart_audio_variant") else "standard",
            audio_quality_mode=self.audio_mode.currentData() or "vbr" if hasattr(self, "audio_mode") else "vbr",
            audio_variant=self.audio_variant.currentData() or "standard" if hasattr(self, "audio_variant") else "standard",
            advanced_selector=self.advanced_selector.text().strip() if hasattr(self, "advanced_selector") else "",
            advanced_args=self.advanced_args.text().strip() if hasattr(self, "advanced_args") else "",
        )

    def update_advanced_preview(self):
        if not hasattr(self, "advanced_preview"):
            return
        try:
            item = self._preview_item_from_ui()
            cmd = build_cmd(item, dict(self.cfg), source_url=item.url)
            text = subprocess.list2cmdline(cmd) if os.name == "nt" else shlex.join(cmd)
        except ValueError:
            text = tr(self.lang, "advanced_bad_args")
        except Exception:
            return
        self.advanced_preview.setText(text)

    def reset_exact_variants(self):
        if not hasattr(self, "variant_combo"):
            return
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        self.variant_combo.addItem(tr(self.lang, "variant_auto"), None)
        self.variant_combo.setToolTip(tr(self.lang, "variant_hint"))
        self.variant_combo.blockSignals(False)
        self.variant_changed()

    def format_url_changed(self, text):
        current = (text or "").strip()
        if current != (self.last_format_url or ""):
            self.last_format_payload = None
            self.last_format_url = ""
            if hasattr(self, "smart_container"):
                self.refresh_smart_controls()
        self.update_advanced_preview()

    def populate_exact_variants(self):
        if not hasattr(self, "variant_combo"):
            return
        payload = self.last_format_payload or {}
        formats = payload.get("formats") or []
        media = self.media.currentData() or "audio"
        current_data = self.variant_combo.currentData()
        current_selector = current_data.get("selector") if isinstance(current_data, dict) else ""

        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        self.variant_combo.addItem(tr(self.lang, "variant_auto"), None)
        selected_index = 0
        for variant in build_exact_variants(formats, media, self.lang):
            if not variant.get("selector"):
                continue
            self.variant_combo.addItem(variant.get("label") or variant["selector"], variant)
            if current_selector and variant.get("selector") == current_selector:
                selected_index = self.variant_combo.count() - 1
        self.variant_combo.setCurrentIndex(selected_index)
        if self.variant_combo.count() > 1:
            self.variant_combo.setToolTip(tr(self.lang, "variant_exact_tip"))
        else:
            self.variant_combo.setToolTip(tr(self.lang, "variant_hint"))
        self.variant_combo.blockSignals(False)
        self.variant_changed()

    def variant_changed(self):
        exact = self.variant_combo.currentData() if hasattr(self, "variant_combo") else None
        enabled = bool(getattr(self, "exact_toggle", None) and self.exact_toggle.isChecked())
        exact_on = enabled and isinstance(exact, dict) and bool(exact.get("selector"))
        if hasattr(self, "variant_combo"):
            self.variant_combo.setToolTip(
                tr(self.lang, "variant_exact_tip") if exact_on
                else (tr(self.lang, "variant_exact_tip") if self.variant_combo.count() > 1 else tr(self.lang, "variant_hint"))
            )
        self.update_smart_visibility()
        self.mark_preset_custom()

    def check_formats(self):
        url = self.url.text().strip()
        if not valid_url(url):
            QMessageBox.warning(self, tr(self.lang, "format_check_title"), tr(self.lang, "invalid_url"))
            return
        if self.format_probe_thread is not None:
            return

        self.inspect_btn.setEnabled(False)
        self.inspect_btn.setText(tr(self.lang, "checking_formats"))
        self.inspect_progress.setVisible(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        thread = QThread(self)
        worker = FormatProbeWorker(url, dict(self.cfg))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result.connect(self.format_probe_done)
        worker.failed.connect(self.format_probe_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.format_probe_finished)
        self.format_probe_thread = thread
        self.format_probe_worker = worker
        thread.start()

    def format_probe_done(self, payload):
        try:
            self.last_format_payload = payload
            self.last_format_url = self.url.text().strip()
            self.populate_exact_variants()
            self.refresh_smart_controls()
            dlg = FormatInspectorDialog(self, payload, self.lang)
            dlg.exec()
        except Exception as exc:
            QMessageBox.warning(
                self, tr(self.lang, "format_check_title"),
                tr(self.lang, "format_check_failed") + "\n\n" + repr(exc)
            )

    def format_probe_failed(self, details):
        if is_cookie_dpapi_error(details):
            msg = tr(
                self.lang, "cookie_dpapi_windows",
                browser=browser_display_name(self.cfg)
            )
        elif is_cookie_lock_error(details):
            msg = tr(
                self.lang, "cookie_locked_windows",
                browser=browser_display_name(self.cfg)
            )
        elif is_cookie_read_error(details):
            msg = "E06: " + cookie_error_description(self.cfg, self.lang)
        else:
            # Keep error dialogs readable. Full technical output belongs in
            # the history/error log, not in a QMessageBox.
            concise = (details or "").strip()
            if len(concise) > 1800:
                concise = concise[-1800:]
            msg = tr(self.lang, "format_check_failed") + ("\n\n" + concise if concise else "")
        QMessageBox.warning(self, tr(self.lang, "format_check_title"), msg)

    def format_probe_finished(self):
        self.format_probe_thread = None
        self.format_probe_worker = None
        self.inspect_btn.setEnabled(True)
        self.inspect_btn.setText(tr(self.lang, "check_formats"))
        self.inspect_progress.setVisible(False)
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def _set_combo_options(self, combo, options, wanted="auto"):
        """Replace combo items while preserving a meaningful current value."""
        combo.blockSignals(True)
        combo.clear()
        for label, data in options:
            combo.addItem(label, data)
        idx = combo.findData(wanted)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _smart_payload_formats(self):
        if not self.last_format_payload or self.url.text().strip() != self.last_format_url:
            return []
        return self.last_format_payload.get("formats") or []

    def _smart_video_candidates(self, selections=None, exclude=None):
        formats = [f for f in self._smart_payload_formats() if _video_only(f)]
        if not formats:
            return []
        selections = selections or {
            "container": self.smart_container.currentData() or "auto",
            "resolution": self.smart_resolution.currentData() or "auto",
            "fps": self.smart_fps.currentData() or "auto",
            "codec": self.smart_codec.currentData() or "auto",
            "tier": self.smart_tier.currentData() or "auto",
            "bitrate": self.smart_bitrate.currentData() or "auto",
        }
        out = []
        for f in formats:
            ok = True
            for field, value in selections.items():
                if field == exclude:
                    continue
                if not _video_matches_rule(f, field, value):
                    ok = False
                    break
            if ok:
                out.append(f)
        return out

    def _video_option_values(self, field, candidates):
        vals = set()
        if field == "container":
            for f in candidates:
                ext = _video_source_ext(f)
                vals.add("mkv")
                if ext in ("mp4", "m4v"):
                    vals.add("mp4")
                if ext == "webm":
                    vals.add("webm")
            order = ["mp4", "mkv", "webm"]
            return [x for x in order if x in vals]
        if field == "resolution":
            vals = {str(int(round(float(f.get("height") or 0)))) for f in candidates if f.get("height")}
            return sorted(vals, key=lambda x: int(x), reverse=True)
        if field == "fps":
            vals = {str(int(round(float(f.get("fps") or 0)))) for f in candidates if f.get("fps")}
            return sorted(vals, key=lambda x: int(x), reverse=True)
        if field == "codec":
            vals = {_video_codec_family(f) for f in candidates}
            order = ["h264", "vp9", "av1", "hevc", "other"]
            return [x for x in order if x in vals]
        if field == "tier":
            vals = {"premium" if _video_is_premium(f) else "standard" for f in candidates}
            return [x for x in ("premium", "standard") if x in vals]
        if field == "bitrate":
            vals = {
                str(int(round(float(f.get("vbr") or f.get("tbr") or 0))))
                for f in candidates if (f.get("vbr") or f.get("tbr"))
            }
            return sorted(vals, key=lambda x: int(x), reverse=True)
        return []

    def _smart_label(self, field, value):
        if value == "auto":
            return tr(self.lang, "auto_choice")
        if field == "container":
            return value.upper()
        if field == "resolution":
            return f"{value}p"
        if field == "fps":
            return f"{value} FPS"
        if field == "codec":
            return tr(self.lang, value if value in ("h264", "vp9", "av1", "hevc") else "codec") if value != "other" else "Inny"
        if field == "tier":
            return tr(self.lang, "premium_video" if value == "premium" else "standard_video")
        if field == "bitrate":
            return f"~{value} kb/s"
        return str(value)

    def refresh_smart_video_options(self, protected=None):
        if not hasattr(self, "smart_container"):
            return
        self._smart_refreshing = True
        try:
            combos = {
                "container": self.smart_container,
                "resolution": self.smart_resolution,
                "fps": self.smart_fps,
                "codec": self.smart_codec,
                "tier": self.smart_tier,
                "bitrate": self.smart_bitrate,
            }
            cfg_keys = {
                "container": "smart_video_container",
                "resolution": "smart_video_resolution",
                "fps": "smart_video_fps",
                "codec": "smart_video_codec",
                "tier": "smart_video_tier",
                "bitrate": "smart_video_bitrate",
            }
            current = {
                field: (combo.currentData() if combo.count() else self.cfg.get(cfg_keys[field], "auto")) or "auto"
                for field, combo in combos.items()
            }

            formats = [f for f in self._smart_payload_formats() if _video_only(f)]
            use_dynamic = bool(formats) and not self.effective_pl()
            if use_dynamic:
                # Remove incompatible selections. Two passes are enough because
                # every field is checked against all the remaining selections.
                for _ in range(3):
                    changed = False
                    for field in combos:
                        cands = self._smart_video_candidates(current, exclude=field)
                        values = self._video_option_values(field, cands)
                        if field == protected:
                            continue
                        if current[field] != "auto" and current[field] not in values:
                            current[field] = "auto"
                            changed = True
                    if not changed:
                        break

                for field, combo in combos.items():
                    cands = self._smart_video_candidates(current, exclude=field)
                    values = self._video_option_values(field, cands)
                    opts = [(tr(self.lang, "auto_choice"), "auto")]
                    opts += [(self._smart_label(field, v), v) for v in values]
                    self._set_combo_options(combo, opts, current[field])
            else:
                generic = {
                    "container": ["mp4", "mkv", "webm"],
                    "resolution": ["2160", "1440", "1080", "720", "480", "360"],
                    "fps": ["60", "50", "30", "25", "24"],
                    "codec": ["h264", "vp9", "av1", "hevc"],
                    "tier": ["premium", "standard"],
                    "bitrate": [],
                }
                for field, combo in combos.items():
                    wanted = current[field] or self.cfg.get(cfg_keys[field], "auto")
                    opts = [(tr(self.lang, "auto_choice"), "auto")]
                    opts += [(self._smart_label(field, v), v) for v in generic[field]]
                    self._set_combo_options(combo, opts, wanted)

            is_playlist = self.effective_pl()
            self.smart_bitrate.setEnabled(not is_playlist and bool(formats))
            self.smart_bitrate.setToolTip(tr(self.lang, "bitrate_single_only") if is_playlist else tr(self.lang, "smart_rules_hint"))
        finally:
            self._smart_refreshing = False

    def refresh_smart_audio_options(self):
        if not hasattr(self, "smart_audio_format"):
            return
        self._smart_refreshing = True
        try:
            wanted_fmt = self.smart_audio_format.currentData() if self.smart_audio_format.count() else self.cfg.get("smart_av_audio_format", "m4a")
            wanted_q = self.smart_audio_quality.currentData() if self.smart_audio_quality.count() else self.cfg.get("smart_av_audio_quality", "best")
            wanted_variant = self.smart_audio_variant.currentData() if self.smart_audio_variant.count() else self.cfg.get("smart_av_audio_variant", "standard")
            audio = [f for f in self._smart_payload_formats() if _audio_only(f)]
            dynamic = bool(audio) and not self.effective_pl()

            if dynamic:
                fams = {_audio_family(f) for f in audio}
                fmt_values = [x for x in ("m4a", "opus") if x in fams]
            else:
                fmt_values = ["m4a", "opus"]
            fmt_opts = [(tr(self.lang, "auto_choice"), "auto")]
            fmt_opts += [("M4A / AAC" if x == "m4a" else "Opus", x) for x in fmt_values]
            self._set_combo_options(self.smart_audio_format, fmt_opts, wanted_fmt or "m4a")

            selected_fmt = self.smart_audio_format.currentData() or "auto"
            qualities = []
            if dynamic:
                rows = [f for f in audio if selected_fmt == "auto" or _audio_family(f) == selected_fmt]
                classes = {_audio_quality_class(f) for f in rows}
                qualities = [x for x in ("256", "128") if x in classes]
            else:
                qualities = ["256", "128"]
            q_opts = [(tr(self.lang, "best_available"), "best")]
            for q in qualities:
                label = tr(self.lang, "premium_256") if q == "256" else tr(self.lang, "standard_128")
                q_opts.append((label, q))
            self._set_combo_options(self.smart_audio_quality, q_opts, wanted_q or "best")
            vi = self.smart_audio_variant.findData(wanted_variant or "standard")
            self.smart_audio_variant.setCurrentIndex(vi if vi >= 0 else 0)
            explicit_family = (self.smart_audio_format.currentData() or "auto") in ("m4a", "opus")
            self.smart_audio_variant_slot.setVisible(explicit_family)
            self.smart_audio_variant.setEnabled(explicit_family)
        finally:
            self._smart_refreshing = False

    def refresh_smart_controls(self):
        self.refresh_smart_video_options()
        self.refresh_smart_audio_options()
        self.update_smart_visibility()

    def update_smart_visibility(self):
        if not hasattr(self, "smart_video_widget"):
            return
        media = self.media.currentData() or "audio"
        audio_only = media == "audio"
        custom_selector = bool(getattr(self, "advanced_selector", None) and self.advanced_selector.text().strip())

        self.audio_widget.setVisible(audio_only)
        self.smart_video_widget.setVisible(media in ("video", "av"))
        self.smart_audio_widget.setVisible(media == "av")

        self.audio_widget.setEnabled(not custom_selector)
        self.smart_video_widget.setEnabled(not custom_selector)
        self.smart_audio_widget.setEnabled(not custom_selector)
        if audio_only and not custom_selector:
            self.refresh_quality(remember=False)
        self.update_audio_tile_layout()
        self.update_advanced_preview()

    def smart_rule_changed(self):
        if getattr(self, "_smart_refreshing", False):
            return
        sender = self.sender()
        protected = {
            self.smart_container: "container",
            self.smart_resolution: "resolution",
            self.smart_fps: "fps",
            self.smart_codec: "codec",
            self.smart_tier: "tier",
            self.smart_bitrate: "bitrate",
        }.get(sender)
        self.cfg["smart_video_container"] = self.smart_container.currentData() or "auto"
        self.cfg["smart_video_resolution"] = self.smart_resolution.currentData() or "auto"
        self.cfg["smart_video_fps"] = self.smart_fps.currentData() or "auto"
        self.cfg["smart_video_codec"] = self.smart_codec.currentData() or "auto"
        self.cfg["smart_video_tier"] = self.smart_tier.currentData() or "auto"
        self.cfg["smart_video_bitrate"] = self.smart_bitrate.currentData() or "auto"
        save_cfg(self.cfg)
        self.refresh_smart_video_options(protected=protected)
        self.update_advanced_preview()
        self.mark_preset_custom()

    def smart_audio_changed(self):
        if getattr(self, "_smart_refreshing", False):
            return
        self.cfg["smart_av_audio_format"] = self.smart_audio_format.currentData() or "auto"
        self.cfg["smart_av_audio_quality"] = self.smart_audio_quality.currentData() or "best"
        self.cfg["smart_av_audio_variant"] = self.smart_audio_variant.currentData() or "standard"
        save_cfg(self.cfg)
        self.refresh_smart_audio_options()
        self.update_advanced_preview()
        self.mark_preset_custom()

    def exact_toggle_changed(self, checked):
        self.variant_combo.setVisible(bool(checked))
        self.variant_changed()

    def _resolve_smart_exact_variant(self):
        """For one checked URL resolve smart fields to exact yt-dlp IDs."""
        if self.effective_pl() or not self._smart_payload_formats():
            return {}
        media = self.media.currentData() or "audio"
        if media not in ("video", "av"):
            return {}
        sels = {
            "container": self.smart_container.currentData() or "auto",
            "resolution": self.smart_resolution.currentData() or "auto",
            "fps": self.smart_fps.currentData() or "auto",
            "codec": self.smart_codec.currentData() or "auto",
            "tier": self.smart_tier.currentData() or "auto",
            "bitrate": self.smart_bitrate.currentData() or "auto",
        }
        videos = self._smart_video_candidates(sels)
        if not videos:
            return {}
        vf = max(videos, key=lambda f: (float(f.get("height") or 0), float(f.get("fps") or 0), float(f.get("vbr") or f.get("tbr") or 0)))
        if media == "video":
            return {
                "selector": str(vf.get("format_id") or ""),
                "label": _variant_video_label(vf),
                "merge": self.smart_container.currentData() if self.smart_container.currentData() in ("mp4", "mkv", "webm") else "",
                "use_music": _format_is_music(vf),
            }

        audio = [f for f in self._smart_payload_formats() if _audio_only(f)]
        afmt = self.smart_audio_format.currentData() or "auto"
        aq = self.smart_audio_quality.currentData() or "best"
        rows = [f for f in audio if afmt == "auto" or _audio_family(f) == afmt]
        if aq not in ("best", "auto", ""):
            strict = [f for f in rows if _audio_quality_class(f) == aq]
            if strict:
                rows = strict
        if not rows:
            return {}
        wanted_variant = self.smart_audio_variant.currentData() or "standard"
        variant_rows = [
            f for f in rows
            if ("-drc" in str(f.get("format_id") or "").lower()) == (wanted_variant == "drc")
        ]
        if variant_rows:
            rows = variant_rows
        af = max(rows, key=_format_bitrate)
        merge = self.smart_container.currentData() or "auto"
        if merge not in ("mp4", "mkv", "webm"):
            merge = _recommended_merge(vf, af)
        selector = f"{vf.get('format_id')}+{af.get('format_id')}"
        return {
            "selector": selector,
            "label": f"{selector} • {merge.upper()} • {_variant_video_label(vf)} + {_variant_audio_label(af)}",
            "merge": merge,
            "use_music": _format_is_music(vf) or _format_is_music(af),
        }

    def paste_destination(self):
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        if text.startswith("file://"):
            local = QUrl(text).toLocalFile()
            if local:
                text = local

        root = Path(self.cfg["default_download_dir"]).expanduser().resolve()
        p = Path(text).expanduser()
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(root)
                text = "" if str(rel) == "." else str(rel)
            except Exception:
                text = str(p)

        self.dest.setText(text)
        self.dest.setFocus()

    def media_changed(self):
        self.cfg["last_media"] = self.media.currentData()
        save_cfg(self.cfg)
        self.refresh_formats(remember=False)
        self.refresh_smart_controls()
        self.update_audio_tile_layout()
        self.update_subtitle_state()
        self.update_advanced_preview()
        self.mark_preset_custom()

    def format_key(self, media=None):
        return "last_format_" + (media or self.media.currentData())

    def format_changed(self):
        if self.fmt.currentData():
            self.cfg[self.format_key()] = self.fmt.currentData()
            save_cfg(self.cfg)
        self.refresh_quality(remember=False)
        self.update_audio_tile_layout()
        self.update_advanced_preview()
        self.mark_preset_custom()

    def audio_mode_changed(self):
        if not hasattr(self, "audio_mode"):
            return
        mode = self.audio_mode.currentData() or "vbr"
        self.cfg["last_mp3_quality_mode"] = mode
        save_cfg(self.cfg)
        if self.fmt.currentData() == "mp3":
            self.refresh_quality(remember=False)
        self.update_audio_tile_layout()
        self.update_advanced_preview()
        self.mark_preset_custom()

    def quality_changed(self):
        q = self.quality.currentData()
        if not q:
            return
        if self.media.currentData() == "audio":
            if self.fmt.currentData() == "mp3":
                mode = self.audio_mode.currentData() or "vbr"
                self.cfg["last_mp3_quality_mode"] = mode
                if mode == "vbr":
                    self.cfg["last_mp3_vbr"] = q
                else:
                    self.cfg["last_mp3_bitrate"] = q
            else:
                self.cfg["last_audio_quality"] = q
        else:
            self.cfg["last_video_quality"] = q
        save_cfg(self.cfg)
        self.update_advanced_preview()
        self.mark_preset_custom()

    def audio_variant_changed(self):
        if not hasattr(self, "audio_variant"):
            return
        self.cfg["last_audio_variant"] = self.audio_variant.currentData() or "standard"
        save_cfg(self.cfg)
        self.update_advanced_preview()
        self.mark_preset_custom()

    def update_audio_tile_layout(self):
        if not hasattr(self, "audio_layout"):
            return
        fmt = self.fmt.currentData() or "original"
        is_mp3 = fmt == "mp3"
        has_variant = fmt in ("m4a", "opus")
        layout = self.audio_layout

        for widget in (self.audio_mode_slot, self.audio_quality_slot, self.audio_variant_slot):
            layout.removeWidget(widget)

        self.audio_mode_slot.setVisible(is_mp3)
        self.audio_variant_slot.setVisible(has_variant)

        if is_mp3:
            layout.addWidget(self.audio_mode_slot, 0, 1)
            layout.addWidget(self.audio_quality_slot, 0, 2)
        elif has_variant:
            layout.addWidget(self.audio_quality_slot, 0, 1)
            layout.addWidget(self.audio_variant_slot, 0, 2)
        else:
            layout.addWidget(self.audio_quality_slot, 0, 1)

        self.audio_quality_slot.setVisible(True)

    def refresh_formats(self, remember=True):
        """Odśwież listę formatów dla wybranego trybu pobierania.

        Kafelek formatu jest widoczny w trybie audio, ale jego stan jest
        nadal używany przez presety i podgląd polecenia także po zmianie
        trybu. Ta metoda musi istnieć już podczas budowania głównego okna.
        """
        media = self.media.currentData() or "audio"
        values = (
            AUDIO_FORMAT_IDS if media == "audio"
            else VIDEO_FORMAT_IDS if media == "video"
            else AV_FORMAT_IDS
        )
        wanted = self.cfg.get(self.format_key(media), values[0])

        self.fmt.blockSignals(True)
        self.fmt.clear()
        for fid in values:
            self.fmt.addItem(format_label(self.lang, media, fid), fid)
        idx = self.fmt.findData(wanted)
        self.fmt.setCurrentIndex(idx if idx >= 0 else 0)
        self.fmt.blockSignals(False)

        if remember:
            self.cfg[self.format_key(media)] = self.fmt.currentData()
            save_cfg(self.cfg)

        self.refresh_quality(remember=False)

    def refresh_quality(self, remember=True):
        media = self.media.currentData() or "audio"
        fmt = self.fmt.currentData()

        self.quality.blockSignals(True)
        self.audio_mode.blockSignals(True)
        self.audio_variant.blockSignals(True)
        self.quality.clear()
        avi = self.audio_variant.findData(self.cfg.get("last_audio_variant", "standard"))
        self.audio_variant.setCurrentIndex(avi if avi >= 0 else 0)

        if media == "audio":
            if fmt == "m4a":
                self.quality.addItem(tr(self.lang, "best"), "best")
                self.quality.addItem(tr(self.lang, "premium_256"), "256")
                self.quality.addItem(tr(self.lang, "standard_128"), "128")
                wanted = self.cfg.get("last_audio_quality", "best")
                if wanted not in M4A_QUALITY_IDS:
                    wanted = "best"
                idx = self.quality.findData(wanted)
                self.quality.setCurrentIndex(idx if idx >= 0 else 0)
                self.quality.setEnabled(True)
                self.quality.setToolTip(tr(self.lang, "m4a_quality_tip"))
            elif fmt == "mp3":
                wanted_mode = self.cfg.get("last_mp3_quality_mode", "vbr")
                mi = self.audio_mode.findData(wanted_mode)
                self.audio_mode.setCurrentIndex(mi if mi >= 0 else 0)
                mode = self.audio_mode.currentData() or "vbr"
                if mode == "vbr":
                    self.quality.addItem(tr(self.lang, "mp3_v0"), "v0")
                    self.quality.addItem(tr(self.lang, "mp3_v2"), "v2")
                    self.quality.addItem(tr(self.lang, "mp3_v4"), "v4")
                    wanted = self.cfg.get("last_mp3_vbr", "v0")
                    if wanted not in MP3_VBR_IDS:
                        wanted = "v0"
                else:
                    for q in MP3_BITRATE_IDS:
                        self.quality.addItem(f"{q} kb/s", q)
                    wanted = self.cfg.get("last_mp3_bitrate", "320")
                    if wanted not in MP3_BITRATE_IDS:
                        wanted = "320"
                idx = self.quality.findData(wanted)
                self.quality.setCurrentIndex(idx if idx >= 0 else 0)
                self.quality.setEnabled(True)
                self.quality.setToolTip(tr(self.lang, "mp3_quality_tip"))
                self.audio_mode.setToolTip(tr(self.lang, "mp3_quality_tip"))
            elif fmt == "opus":
                self.quality.addItem(tr(self.lang, "best"), "best")
                self.quality.addItem(tr(self.lang, "premium_256"), "256")
                self.quality.addItem(tr(self.lang, "standard_128"), "128")
                wanted = self.cfg.get("last_audio_quality", "best")
                if wanted not in AUDIO_QUALITY_IDS:
                    wanted = "best"
                idx = self.quality.findData(wanted)
                self.quality.setCurrentIndex(idx if idx >= 0 else 0)
                self.quality.setEnabled(True)
                self.quality.setToolTip(tr(self.lang, "audio_quality_tip"))
            else:
                self.quality.addItem(tr(self.lang, "best"), "best")
                self.quality.setCurrentIndex(0)
                self.quality.setEnabled(False)
                self.quality.setToolTip("")
        else:
            for q in VIDEO_QUALITY_IDS:
                self.quality.addItem(quality_label(self.lang, media, q), q)
            wanted = self.cfg.get("last_video_quality", "best")
            idx = self.quality.findData(wanted)
            self.quality.setCurrentIndex(idx if idx >= 0 else 0)
            self.quality.setEnabled(True)
            self.quality.setToolTip(tr(self.lang, "video_quality_tip"))

        self.audio_mode.blockSignals(False)
        self.audio_variant.blockSignals(False)
        self.quality.blockSignals(False)
        self.update_audio_tile_layout()
        if remember:
            self.quality_changed()
    def restore_last_subtitle_choices(self):
        for combo, key, default in (
            (self.subtitle_mode, "last_subtitle_mode", "none"),
            (self.subtitle_lang, "last_subtitle_lang", "pl"),
            (self.subtitle_output, "last_subtitle_output", "separate"),
        ):
            idx = combo.findData(self.cfg.get(key, default))
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.update_subtitle_state()

    def subtitle_changed(self):
        if not hasattr(self, "subtitle_mode"):
            return
        self.cfg["last_subtitle_mode"] = self.subtitle_mode.currentData() or "none"
        self.cfg["last_subtitle_lang"] = self.subtitle_lang.currentData() or "pl"
        self.cfg["last_subtitle_output"] = self.subtitle_output.currentData() or "separate"
        save_cfg(self.cfg)
        self.update_subtitle_state()
        self.update_advanced_preview()
        self.mark_preset_custom()

    def update_subtitle_layout(self, force=False):
        # Od 0.4.15 napisy są trzecim rzędem kafelka Obraz, więc nie wymagają
        # osobnego responsywnego przełączania układu.
        return

    def restore_window_geometry(self):
        """Restore only main-window width/height, never global X/Y position."""
        try:
            w = int(self.cfg.get("window_width", 697))
            h = int(self.cfg.get("window_height", 932))
            w = max(self.minimumWidth(), w)
            h = max(self.minimumHeight(), h)
            self.resize(w, h)
        except Exception:
            pass

    def save_window_geometry(self):
        """Persist only width/height; placement remains the window manager's job."""
        try:
            size = self.normalGeometry().size() if (self.isMaximized() or self.isFullScreen()) else self.size()
            self.cfg["window_width"] = int(size.width())
            self.cfg["window_height"] = int(size.height())
            # Drop obsolete position/geometry keys if they came from older builds.
            self.cfg.pop("window_geometry", None)
            self.cfg.pop("window_x", None)
            self.cfg.pop("window_y", None)
            save_cfg(self.cfg)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def update_subtitle_state(self):
        if not hasattr(self, "subtitle_mode"):
            return
        video = (self.media.currentData() or "audio") != "audio"
        mode_on = video and (self.subtitle_mode.currentData() or "none") != "none"
        self.subtitle_mode.setEnabled(video)
        self.subtitle_label.setEnabled(video)
        self.subtitle_lang.setEnabled(mode_on)
        self.subtitle_lang_label.setEnabled(mode_on)
        self.subtitle_output.setEnabled(mode_on)
        self.subtitle_output_label.setEnabled(mode_on)

    def current_preset_data(self):
        media = self.media.currentData() or "audio"
        data = {
            "media": media,
            "fmt": self.fmt.currentData() or "original",
            "quality": self.quality.currentData() or "best",
            "audio_variant": self.audio_variant.currentData() or "standard",
            "subtitle_mode": self.subtitle_mode.currentData() or "none",
            "subtitle_lang": self.subtitle_lang.currentData() or "pl",
            "subtitle_output": self.subtitle_output.currentData() or "separate",
        }
        if media == "audio" and (self.fmt.currentData() or "") == "mp3":
            data["audio_quality_mode"] = self.audio_mode.currentData() or "vbr"
        if media in ("video", "av"):
            data.update({
                "smart_video_container": self.smart_container.currentData() or "auto",
                "smart_video_resolution": self.smart_resolution.currentData() or "auto",
                "smart_video_fps": self.smart_fps.currentData() or "auto",
                "smart_video_codec": self.smart_codec.currentData() or "auto",
                "smart_video_tier": self.smart_tier.currentData() or "auto",
                "smart_video_bitrate": self.smart_bitrate.currentData() or "auto",
                "smart_audio_format": self.smart_audio_format.currentData() or "auto",
                "smart_audio_quality": self.smart_audio_quality.currentData() or "best",
                "smart_audio_variant": self.smart_audio_variant.currentData() or "standard",
            })
        return data

    def refresh_preset_combo(self, select_id=None):
        if not hasattr(self, "preset_combo"):
            return
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem(tr(self.lang, "preset_custom"), None)
        for p in self.cfg.get("presets", []):
            self.preset_combo.addItem(preset_display_name(p, self.lang), p.get("id"))
        idx = self.preset_combo.findData(select_id) if select_id else 0
        self.preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.preset_combo.blockSignals(False)
        self.preset_delete_btn.setEnabled(self.preset_combo.currentData() is not None)

    def retranslate_presets(self):
        if not hasattr(self, "preset_combo"):
            return
        selected = self.preset_combo.currentData()
        self.refresh_preset_combo(selected)

    def mark_preset_custom(self):
        if self.applying_preset or not hasattr(self, "preset_combo"):
            return
        if self.preset_combo.currentData() is not None:
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(0)
            self.preset_combo.blockSignals(False)
            self.preset_delete_btn.setEnabled(False)

    def preset_selected(self):
        if self.applying_preset:
            return
        pid = self.preset_combo.currentData()
        self.preset_delete_btn.setEnabled(pid is not None)
        if pid is None:
            return
        preset = next((p for p in self.cfg.get("presets", []) if p.get("id") == pid), None)
        if not preset:
            return
        self.applying_preset = True
        try:
            # Preset korzysta z kafelków GUI, więc wyczyść ręczne nadpisania.
            if hasattr(self, "advanced_selector"):
                self.advanced_selector.blockSignals(True)
                self.advanced_args.blockSignals(True)
                self.advanced_selector.clear()
                self.advanced_args.clear()
                self.advanced_selector.blockSignals(False)
                self.advanced_args.blockSignals(False)
                self.cfg["advanced_selector"] = ""
                self.cfg["advanced_args"] = ""
            self.media.blockSignals(True)
            mi = self.media.findData(preset.get("media", "audio"))
            self.media.setCurrentIndex(mi if mi >= 0 else 0)
            self.media.blockSignals(False)
            self.refresh_formats(remember=False)
            self.refresh_smart_controls()

            media = self.media.currentData() or "audio"
            if media == "audio":
                fi = self.fmt.findData(preset.get("fmt"))
                if fi >= 0:
                    self.fmt.setCurrentIndex(fi)
                if (self.fmt.currentData() or "") == "mp3":
                    mode = preset.get("audio_quality_mode", "vbr")
                    mi2 = self.audio_mode.findData(mode)
                    if mi2 >= 0:
                        self.audio_mode.setCurrentIndex(mi2)
                    self.cfg["last_mp3_quality_mode"] = mode
                    qv = preset.get("quality", "v0")
                    if mode == "vbr":
                        self.cfg["last_mp3_vbr"] = qv if qv in MP3_VBR_IDS else "v0"
                    else:
                        self.cfg["last_mp3_bitrate"] = qv if qv in MP3_BITRATE_IDS else "320"
                self.refresh_quality(remember=False)
                qi = self.quality.findData(preset.get("quality", "v0" if (self.fmt.currentData() or "") == "mp3" else "best"))
                if qi >= 0:
                    self.quality.setCurrentIndex(qi)
                vi = self.audio_variant.findData(preset.get("audio_variant", "standard"))
                if vi >= 0:
                    self.audio_variant.setCurrentIndex(vi)
            else:
                smart_values = {
                    self.smart_container: preset.get("smart_video_container", preset.get("fmt", "auto")),
                    self.smart_resolution: str(preset.get("smart_video_resolution", str(preset.get("quality", "best")).replace("p", ""))) if preset.get("quality", "best") != "best" else preset.get("smart_video_resolution", "auto"),
                    self.smart_fps: preset.get("smart_video_fps", "auto"),
                    self.smart_codec: preset.get("smart_video_codec", "auto"),
                    self.smart_tier: preset.get("smart_video_tier", "auto"),
                    self.smart_bitrate: preset.get("smart_video_bitrate", "auto"),
                    self.smart_audio_format: preset.get("smart_audio_format", "m4a"),
                    self.smart_audio_quality: preset.get("smart_audio_quality", "best"),
                    self.smart_audio_variant: preset.get("smart_audio_variant", "standard"),
                }
                for combo, value in smart_values.items():
                    idx = combo.findData(value)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                self.refresh_smart_controls()

            for combo, key, default in (
                (self.subtitle_mode, "subtitle_mode", "none"),
                (self.subtitle_lang, "subtitle_lang", "pl"),
                (self.subtitle_output, "subtitle_output", "separate"),
            ):
                ci = combo.findData(preset.get(key, default))
                if ci >= 0:
                    combo.setCurrentIndex(ci)
            self.remember_current_choices()
            self.update_subtitle_state()
            self.update_smart_visibility()
        finally:
            self.applying_preset = False

    def save_current_preset(self):
        name, ok = QInputDialog.getText(
            self, tr(self.lang, "preset_name"), tr(self.lang, "preset_name_prompt")
        )
        name = name.strip()
        if not ok or not name:
            return
        presets = list(self.cfg.get("presets", []))
        existing = next((p for p in presets if preset_display_name(p, self.lang).casefold() == name.casefold()), None)
        data = self.current_preset_data()
        if existing is not None:
            ans = QMessageBox.question(
                self, APP_NAME, tr(self.lang, "preset_overwrite"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            existing.pop("name_key", None)
            existing["name"] = name
            existing.update(data)
            pid = existing.get("id")
        else:
            pid = "user-" + uuid.uuid4().hex
            presets.append({"id": pid, "name": name, **data})
        self.cfg["presets"] = presets
        self.cfg["presets_initialized"] = True
        save_cfg(self.cfg)
        self.refresh_preset_combo(pid)

    def delete_current_preset(self):
        pid = self.preset_combo.currentData()
        if pid is None:
            return
        preset = next((p for p in self.cfg.get("presets", []) if p.get("id") == pid), None)
        if not preset:
            return
        name = preset_display_name(preset, self.lang)
        ans = QMessageBox.question(
            self, APP_NAME, tr(self.lang, "preset_delete_confirm", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.cfg["presets"] = [p for p in self.cfg.get("presets", []) if p.get("id") != pid]
        self.cfg["presets_initialized"] = True
        save_cfg(self.cfg)
        self.refresh_preset_combo()

    def select_changed(self, on):
        if on:
            self.omit.blockSignals(True)
            self.omit.setChecked(False)
            self.omit.blockSignals(False)
        self.update_pl()

    def omit_changed(self, on):
        if on:
            self.select.blockSignals(True)
            self.select.setChecked(False)
            self.select.blockSignals(False)
        self.update_pl()

    def update_pl(self):
        on = self.effective_pl()
        self.select.setEnabled(on)
        self.omit.setEnabled(on)

        if not self.effective_pl():
            self.select.setChecked(False)
            self.omit.setChecked(False)

        nums = on and (self.select.isChecked() or self.omit.isChecked())
        self.nums.setEnabled(nums)
        self.numbers_label.setEnabled(nums)
        if not nums:
            self.nums.clear()
        if hasattr(self, "smart_container"):
            self.refresh_smart_controls()

    def choose_dest(self):
        root = Path(self.cfg["default_download_dir"]).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        cur = dest_path(self.dest.text(), self.cfg)
        start = cur if cur.exists() else root

        p = choose_directory_dialog(
            self,
            tr(self.lang, "choose_destination"),
            str(start),
            self.lang,
        )
        if p:
            pp = Path(p).resolve()
            try:
                rel = pp.relative_to(root.resolve())
                self.dest.setText("" if str(rel) == "." else str(rel))
            except ValueError:
                self.dest.setText(str(pp))

    def remember_current_choices(self):
        media = self.media.currentData()
        self.cfg["last_media"] = media
        if media == "audio":
            self.cfg[self.format_key()] = self.fmt.currentData()
            if self.fmt.currentData() == "mp3":
                mode = self.audio_mode.currentData() or "vbr"
                self.cfg["last_mp3_quality_mode"] = mode
                if mode == "vbr":
                    self.cfg["last_mp3_vbr"] = self.quality.currentData() or "v0"
                else:
                    self.cfg["last_mp3_bitrate"] = self.quality.currentData() or "320"
            elif self.fmt.currentData() in ("opus", "m4a"):
                self.cfg["last_audio_quality"] = self.quality.currentData()
                self.cfg["last_audio_variant"] = self.audio_variant.currentData() or "standard"
        else:
            self.cfg["smart_video_container"] = self.smart_container.currentData() or "auto"
            self.cfg["smart_video_resolution"] = self.smart_resolution.currentData() or "auto"
            self.cfg["smart_video_fps"] = self.smart_fps.currentData() or "auto"
            self.cfg["smart_video_codec"] = self.smart_codec.currentData() or "auto"
            self.cfg["smart_video_tier"] = self.smart_tier.currentData() or "auto"
            self.cfg["smart_video_bitrate"] = self.smart_bitrate.currentData() or "auto"
            if media == "av":
                self.cfg["smart_av_audio_format"] = self.smart_audio_format.currentData() or "auto"
                self.cfg["smart_av_audio_quality"] = self.smart_audio_quality.currentData() or "best"
                self.cfg["smart_av_audio_variant"] = self.smart_audio_variant.currentData() or "standard"

        self.cfg["advanced_selector"] = self.advanced_selector.text().strip()
        self.cfg["advanced_args"] = self.advanced_args.text().strip()
        self.cfg["last_subtitle_mode"] = self.subtitle_mode.currentData() or "none"
        self.cfg["last_subtitle_lang"] = self.subtitle_lang.currentData() or "pl"
        self.cfg["last_subtitle_output"] = self.subtitle_output.currentData() or "separate"
        save_cfg(self.cfg)

    def add_item(self):
        try:
            u = self.url.text().strip()
            if not valid_url(u):
                raise ValueError(tr(self.lang, "invalid_url"))

            pl = self.effective_pl()
            sel = ""
            nums = ""

            if pl:
                sel = "select" if self.select.isChecked() else "omit" if self.omit.isChecked() else ""
                if sel:
                    nums = self.nums.text().replace(" ", "")
                    validate_numbers(nums, self.lang)

            self.remember_current_choices()

            media = self.media.currentData() or "audio"
            custom_selector = self.advanced_selector.text().strip()
            custom_args = self.advanced_args.text().strip()
            try:
                extra_yt_dlp_args(custom_args)
            except ValueError as exc:
                raise ValueError(tr(self.lang, "advanced_bad_args")) from exc

            exact = {}
            if not custom_selector and media in ("video", "av"):
                # For a single URL that was already analysed, translate the
                # friendly tiles into exact IDs. Playlists intentionally keep
                # rule-based selectors so every entry can resolve separately.
                exact = self._resolve_smart_exact_variant()

            if media == "audio":
                item_fmt = self.fmt.currentData() or "original"
                item_quality = self.quality.currentData() or "best"
            else:
                item_fmt = self.smart_container.currentData() or "auto"
                res = self.smart_resolution.currentData() or "auto"
                item_quality = "best" if res == "auto" else f"{res}p"

            it = Item(
                pl,
                self.dest.text().strip(),
                u,
                media,
                item_fmt,
                item_quality,
                sel,
                nums,
                self.subtitle_mode.currentData() or "none",
                self.subtitle_lang.currentData() or "pl",
                self.subtitle_output.currentData() or "separate",
                exact_selector=str(exact.get("selector") or ""),
                exact_label=str(exact.get("label") or ""),
                exact_merge=str(exact.get("merge") or ""),
                exact_use_music=bool(exact.get("use_music", False)),
                smart_video_container=self.smart_container.currentData() or "auto" if media != "audio" else "auto",
                smart_video_resolution=self.smart_resolution.currentData() or "auto" if media != "audio" else "auto",
                smart_video_fps=self.smart_fps.currentData() or "auto" if media != "audio" else "auto",
                smart_video_codec=self.smart_codec.currentData() or "auto" if media != "audio" else "auto",
                smart_video_tier=self.smart_tier.currentData() or "auto" if media != "audio" else "auto",
                smart_video_bitrate=self.smart_bitrate.currentData() or "auto" if media != "audio" else "auto",
                smart_audio_format=self.smart_audio_format.currentData() or "auto" if media == "av" else "auto",
                smart_audio_quality=self.smart_audio_quality.currentData() or "best" if media == "av" else "best",
                smart_audio_variant=self.smart_audio_variant.currentData() or "standard" if media == "av" else "standard",
                audio_quality_mode=self.audio_mode.currentData() or "vbr" if media == "audio" else "vbr",
                audio_variant=self.audio_variant.currentData() or "standard" if media == "audio" else "standard",
                advanced_selector=custom_selector,
                advanced_args=custom_args,
            )
            self.queue.append(it)
            self.item_state[it.uid] = "waiting"
            self.append_row(it)
            if self.busy:
                self.session_uids.add(it.uid)
            self.enqueue_probe(it.uid)
            self.update_stats_ui()
            self.save_queue_state()
            self.reset_form()

        except ValueError as e:
            QMessageBox.warning(self, tr(self.lang, "cannot_add"), str(e))

    def row_values(self, it):
        mode = tr(self.lang, "playlist_word") if it.playlist else tr(self.lang, "track_movie")
        if it.selection:
            word = tr(self.lang, "choose_word") if it.selection == "select" else tr(self.lang, "omit_word")
            mode += f" • {word} {it.numbers}"

        full = str(dest_path(it.destination, self.cfg))

        status = tr(self.lang, "waiting")
        fmt_text = format_label(self.lang, it.media, it.fmt)
        quality_text = quality_label(self.lang, it.media, it.quality)
        if it.media == "audio" and it.fmt in ("m4a", "opus"):
            if it.quality == "256":
                quality_text = tr(self.lang, "premium_256")
            elif it.quality == "128":
                quality_text = tr(self.lang, "standard_128")
            if getattr(it, "audio_variant", "standard") == "drc":
                quality_text += " • DRC"
        custom_selector = str(getattr(it, "advanced_selector", "") or "").strip()
        if it.media in ("video", "av") and not getattr(it, "exact_selector", "") and not custom_selector:
            cont = getattr(it, "smart_video_container", "auto") or "auto"
            codec = getattr(it, "smart_video_codec", "auto") or "auto"
            fmt_text = (tr(self.lang, "auto_choice") if cont == "auto" else cont.upper())
            if codec != "auto":
                fmt_text += f" • {self._smart_label('codec', codec)}"
            bits = []
            res = getattr(it, "smart_video_resolution", "auto") or "auto"
            fps = getattr(it, "smart_video_fps", "auto") or "auto"
            tier = getattr(it, "smart_video_tier", "auto") or "auto"
            if res != "auto": bits.append(f"{res}p")
            if fps != "auto": bits.append(f"{fps} FPS")
            if tier != "auto": bits.append(self._smart_label('tier', tier))
            if it.media == "av":
                af = getattr(it, "smart_audio_format", "auto") or "auto"
                aq = getattr(it, "smart_audio_quality", "best") or "best"
                if af != "auto":
                    bits.append("M4A" if af == "m4a" else "Opus")
                    av = getattr(it, "smart_audio_variant", "standard") or "standard"
                    if av == "drc":
                        bits.append("DRC")
                if aq == "256":
                    bits.append("~256 kb/s")
                elif aq == "128":
                    bits.append("~128 kb/s")
            quality_text = " • ".join(bits) if bits else tr(self.lang, "best_available")
        if getattr(it, "exact_selector", "") and not custom_selector:
            fmt_text = f"ID {it.exact_selector}"
            quality_text = it.exact_label or quality_text
        if custom_selector:
            fmt_text = f"-f {custom_selector}"
            quality_text = getattr(it, "advanced_args", "") or tr(self.lang, "advanced_title")

        return [
            mode,
            media_label(self.lang, it.media),
            fmt_text,
            quality_text,
            full,
            it.url,
            status,
        ]

    def append_row(self, it):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for i, value in enumerate(self.row_values(it)):
            cell = QTableWidgetItem(value)
            cell.setData(Qt.ItemDataRole.UserRole, it.uid)
            self.table.setItem(row, i, cell)

    def uid_for_row(self, row):
        cell = self.table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def row_for_uid(self, uid):
        for row in range(self.table.rowCount()):
            if self.uid_for_row(row) == uid:
                return row
        return -1

    def item_for_uid(self, uid):
        for it in self.queue:
            if it.uid == uid:
                return it
        return None

    def save_queue_state(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            items = []
            for it in self.queue:
                state = self.item_state.get(it.uid, "waiting")
                if state == "done":
                    continue
                if state == "downloading":
                    state = "waiting"
                err = self.errors.get(it.uid)
                items.append({
                    "item": asdict(it),
                    "state": state,
                    "error": [err[0], err[1]] if err else None,
                })
            tmp = QUEUE_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(QUEUE_FILE)
        except Exception:
            pass

    def restore_queue_state(self):
        if not QUEUE_FILE.exists():
            return 0
        restored = 0
        try:
            data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            for rec in data.get("items", []):
                raw = dict(rec.get("item") or {})
                allowed = {x.name for x in __import__("dataclasses").fields(Item)}
                raw = {k: v for k, v in raw.items() if k in allowed}
                it = Item(**raw)
                if self.item_for_uid(it.uid) is not None:
                    it.uid = uuid.uuid4().hex
                self.queue.append(it)
                state = rec.get("state", "waiting")
                if state not in {"waiting", "error", "warning"}:
                    state = "waiting"
                self.item_state[it.uid] = state
                err = rec.get("error")
                if isinstance(err, list) and len(err) >= 2:
                    self.errors[it.uid] = (str(err[0]), str(err[1]), "")
                self.append_row(it)
                row = self.row_for_uid(it.uid)
                cell = self.table.item(row, 6) if row >= 0 else None
                if cell is not None:
                    if state == "error":
                        c = self.errors.get(it.uid, ("E99", "", ""))[0]
                        cell.setText(f"{tr(self.lang, 'error')} {c}")
                    elif state == "warning":
                        c = self.errors.get(it.uid, ("W01", "", ""))[0]
                        cell.setText(f"{tr(self.lang, 'warning')} {c}")
                    else:
                        cell.setText(tr(self.lang, "waiting"))
                if state == "waiting":
                    self.enqueue_probe(it.uid)
                restored += 1
        except Exception:
            return 0
        return restored

    def apply_panel_row_limits(self):
        """Apply independent min/max visible-row limits to queue/history/log only."""
        qmin = int(self.cfg.get("queue_rows_min", 3))
        qmax = int(self.cfg.get("queue_rows_max", 5))
        hmin = int(self.cfg.get("history_rows_min", 2))
        hmax = int(self.cfg.get("history_rows_max", 3))
        lmin = int(self.cfg.get("log_rows_min", 2))
        lmax = int(self.cfg.get("log_rows_max", 3))

        # Najpierw zdejmujemy stare limity z kontenerów, aby sizeHint mógł
        # przeliczyć się poprawnie także po zwiększeniu wartości w ustawieniach.
        huge = 16777215
        for panel in (self.queue_pane, self.queue_box, self.history_box, self.log_box):
            panel.setMaximumHeight(huge)

        qmin_h = minimum_table_height(self.table, qmin)
        qmax_h = minimum_table_height(self.table, qmax)
        hmin_h = minimum_table_height(self.history_table, hmin)
        hmax_h = minimum_table_height(self.history_table, hmax)
        lmin_h = text_rows_height(self.log, lmin)
        lmax_h = text_rows_height(self.log, lmax)

        self.table.setMinimumHeight(qmin_h)
        self.table.setMaximumHeight(qmax_h)
        self.history_table.setMinimumHeight(hmin_h)
        self.history_table.setMaximumHeight(hmax_h)
        self.log.setMinimumHeight(lmin_h)
        self.log.setMaximumHeight(lmax_h)

        # Wyliczamy maksimum całego segmentu z edytorem/tabelą chwilowo
        # ustawioną dokładnie na wartość MAX. Dzięki temu domyślny sizeHint
        # QTableWidget/QPlainTextEdit nie może przypadkiem ograniczyć panelu
        # do innej liczby rzędów niż ustawiona przez użytkownika.
        self.table.setFixedHeight(qmax_h)
        self.history_table.setFixedHeight(hmax_h)
        self.log.setFixedHeight(lmax_h)
        for layout in (self.queue_box.layout(), self.queue_pane.layout(),
                       self.history_box.layout(), self.log_box.layout()):
            if layout is not None:
                layout.invalidate()
                layout.activate()

        queue_box_max = self.queue_box.sizeHint().height()
        self.queue_box.setMaximumHeight(queue_box_max)
        if self.queue_pane.layout() is not None:
            self.queue_pane.layout().invalidate()
            self.queue_pane.layout().activate()
        queue_pane_max = self.queue_pane.sizeHint().height()
        history_box_max = self.history_box.sizeHint().height()
        log_box_max = self.log_box.sizeHint().height()

        # Przywrócenie zakresu MIN–MAX pozwala layoutowi płynnie zmniejszać
        # panel wraz z oknem, aż do ustawionego minimum.
        self.table.setMinimumHeight(qmin_h)
        self.table.setMaximumHeight(qmax_h)
        self.history_table.setMinimumHeight(hmin_h)
        self.history_table.setMaximumHeight(hmax_h)
        self.log.setMinimumHeight(lmin_h)
        self.log.setMaximumHeight(lmax_h)

        self.queue_pane.setMaximumHeight(queue_pane_max)
        self.history_box.setMaximumHeight(history_box_max)
        self.log_box.setMaximumHeight(log_box_max)

        for widget in (self.table, self.history_table, self.log,
                       self.queue_pane, self.history_box, self.log_box):
            widget.updateGeometry()
        if self.content.layout() is not None:
            self.content.layout().invalidate()
            self.content.layout().activate()
        self.content.updateGeometry()

    def apply_ui_preferences(self):
        self.apply_panel_row_limits()
        self.log_box.setVisible(bool(self.cfg.get("show_log", True)))
        self.history_box.setVisible(bool(self.cfg.get("show_history", True)))
        self.preset_widget.setVisible(bool(self.cfg.get("show_presets", True)))
        self.advanced_section.setVisible(bool(self.cfg.get("show_advanced", True)))
        self.update_subtitle_state()

    def refresh_history(self):
        if not hasattr(self, "history_table"):
            return
        rows = []
        try:
            rows = self.stats_db.download_history(500)
        except Exception:
            rows = []
        self.history_table.setRowCount(0)
        for rec in rows:
            (_id, ts, url, destination, playlist, media, fmt, quality, status, code, duration, elapsed, error_desc, error_log) = rec
            r = self.history_table.rowCount()
            self.history_table.insertRow(r)
            mode = tr(self.lang, "playlist_word") if playlist else tr(self.lang, "track_movie")
            if status == "done":
                result = tr(self.lang, "ready")
            elif status == "warning":
                result = f"{tr(self.lang, 'warning')} {code}"
            else:
                result = f"{tr(self.lang, 'error')} {code}" if code else tr(self.lang, "error")
            vals = [
                time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)),
                mode, media_label(self.lang, media),
                f"{format_label(self.lang, media, fmt)} / {quality_label(self.lang, media, quality)}",
                destination, url, result, human_time(elapsed),
            ]
            for c, val in enumerate(vals):
                cell = QTableWidgetItem(str(val))
                self.history_table.setItem(r, c, cell)
            # Keep error details with the row so PPM works even after restart.
            first = self.history_table.item(r, 0)
            if first is not None:
                first.setData(Qt.ItemDataRole.UserRole, {
                    "code": code or "",
                    "desc": error_desc or "",
                    "log": error_log or "",
                    "status": status or "",
                })

    def history_context(self, pos):
        idx = self.history_table.indexAt(pos)
        if not idx.isValid():
            return
        r = idx.row()
        menu = QMenu(self)

        copy_link = QAction(tr(self.lang, "copy_link"), self)
        copy_path = QAction(tr(self.lang, "copy_path"), self)
        copy_link.triggered.connect(
            lambda: QApplication.clipboard().setText(self.history_table.item(r, 5).text() if self.history_table.item(r, 5) else "")
        )
        copy_path.triggered.connect(
            lambda: QApplication.clipboard().setText(self.history_table.item(r, 4).text() if self.history_table.item(r, 4) else "")
        )
        menu.addAction(copy_link)
        menu.addAction(copy_path)

        meta = {}
        first = self.history_table.item(r, 0)
        if first is not None:
            value = first.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, dict):
                meta = value

        code = str(meta.get("code") or "")
        status = str(meta.get("status") or "")
        if code and code != "OK" and status in {"error", "warning"}:
            menu.addSeparator()
            desc_action = QAction(tr(self.lang, "show_error_desc"), self)
            log_action = QAction(tr(self.lang, "show_log"), self)

            def show_hist_desc():
                desc = str(meta.get("desc") or "").strip()
                if not desc:
                    key = code.lower()
                    desc = tr(self.lang, key) if key in TR.get(self.lang, {}) else code
                kind = tr(self.lang, "warning") if code.startswith("W") else tr(self.lang, "error")
                TextDialog(
                    self, f"{kind} {code}", f"{code}: {desc}", self.lang
                ).exec()

            def show_hist_log():
                desc = str(meta.get("desc") or "").strip()
                if not desc:
                    key = code.lower()
                    desc = tr(self.lang, key) if key in TR.get(self.lang, {}) else code
                TextDialog(
                    self, f"{code} — {desc}",
                    str(meta.get("log") or "").strip() or tr(self.lang, "no_log"),
                    self.lang
                ).exec()

            desc_action.triggered.connect(show_hist_desc)
            log_action.triggered.connect(show_hist_log)
            menu.addAction(desc_action)
            menu.addAction(log_action)

        menu.exec(self.history_table.viewport().mapToGlobal(pos))

    def clear_download_history(self):
        if self.cfg.get("confirm_clear_history", False):
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setText(tr(self.lang, "clear_history_confirm"))
            box.setIcon(QMessageBox.Icon.Question)
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.No)
            polish_message_box(box)
            ans = box.exec()
            if ans != QMessageBox.StandardButton.Yes:
                return
        try:
            self.stats_db.clear_download_history()
        except Exception:
            pass
        self.refresh_history()

    def retranslate_queue(self):
        for it in self.queue:
            row = self.row_for_uid(it.uid)
            if row < 0:
                continue
            old_status = self.table.item(row, 6).text() if self.table.item(row, 6) else ""
            for col, value in enumerate(self.row_values(it)[:6]):
                self.table.item(row, col).setText(value)

            cell = self.table.item(row, 6)
            if old_status.startswith("Błąd ") or old_status.startswith("Error "):
                code = old_status.split()[-1]
                cell.setText(f"{tr(self.lang, 'error')} {code}")
            elif old_status.startswith("Ostrz. ") or old_status.startswith("Warn. "):
                code = old_status.split()[-1]
                cell.setText(f"{tr(self.lang, 'warning')} {code}")
            elif old_status in ("Gotowe", "Done"):
                cell.setText(tr(self.lang, "ready"))
            elif old_status in ("Gotowe • standard (brak DRC)", "Done • standard (DRC unavailable)"):
                cell.setText(tr(self.lang, "drc_fallback_status"))
            elif old_status in ("Przerwano", "Stopped"):
                cell.setText(tr(self.lang, "stopped"))
            elif old_status in ("Pobieranie…", "Downloading…"):
                cell.setText(tr(self.lang, "downloading"))
            else:
                cell.setText(tr(self.lang, "waiting"))

    def reset_form(self):
        self.url.clear()
        if hasattr(self, "exact_toggle"):
            self.exact_toggle.setChecked(False)
        self.pl.setChecked(False)
        self.select.setChecked(False)
        self.omit.setChecked(False)
        self.nums.clear()
        self.dest.setText(self.cfg.get("default_subfolder", ""))
        self.url.setFocus()
        self.update_pl()

    def remove_rows(self):
        rows = sorted(
            {x.row() for x in self.table.selectionModel().selectedRows()},
            reverse=True
        )
        blocked = False
        for r in rows:
            uid = self.uid_for_row(r)
            if not uid:
                continue
            state = self.item_state.get(uid, "waiting")
            if self.busy and state != "waiting":
                blocked = True
                continue
            self.remove_uid(uid)

        if blocked:
            QMessageBox.information(self, APP_NAME, tr(self.lang, "remove_waiting_only"))
        self.update_stats_ui()

    def remove_uid(self, uid):
        state = self.item_state.get(uid, "waiting")
        if self.busy and state != "waiting":
            return False

        if self.worker is not None and uid in self.active_batch_uids:
            self.worker.skip(uid)

        row = self.row_for_uid(uid)
        if row >= 0:
            self.table.removeRow(row)

        self.queue = [it for it in self.queue if it.uid != uid]
        self.errors.pop(uid, None)
        self.item_state.pop(uid, None)
        self.session_uids.discard(uid)
        self.probe_queue = [x for x in self.probe_queue if x != uid]
        self.update_stats_ui()
        self.save_queue_state()
        return True

    def clear_rows(self):
        if self.busy:
            return
        self.queue.clear()
        self.table.setRowCount(0)
        self.errors.clear()
        self.item_state.clear()
        self.session_uids.clear()
        self.update_stats_ui()
        self.save_queue_state()

    def context(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return

        r = idx.row()
        uid = self.uid_for_row(r)
        menu = QMenu(self)

        copy_link = QAction(tr(self.lang, "copy_link"), self)
        copy_path = QAction(tr(self.lang, "copy_path"), self)
        copy_link.triggered.connect(
            lambda: QApplication.clipboard().setText(self.table.item(r, 5).text())
        )
        copy_path.triggered.connect(
            lambda: QApplication.clipboard().setText(self.table.item(r, 4).text())
        )

        menu.addAction(copy_link)
        menu.addAction(copy_path)

        if uid and (not self.busy or self.item_state.get(uid) == "waiting"):
            remove_action = QAction(tr(self.lang, "remove_from_queue"), self)
            remove_action.triggered.connect(lambda: self.remove_uid(uid))
            menu.addSeparator()
            menu.addAction(remove_action)

        if uid in self.errors:
            menu.addSeparator()
            desc = QAction(tr(self.lang, "show_error_desc"), self)
            log = QAction(tr(self.lang, "show_log"), self)
            desc.triggered.connect(lambda: self.show_desc(uid))
            log.triggered.connect(lambda: self.show_log(uid))
            menu.addAction(desc)
            menu.addAction(log)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def show_desc(self, uid):
        code, desc, _ = self.errors[uid]
        kind = tr(self.lang, "warning") if code.startswith("W") else tr(self.lang, "error")
        TextDialog(
            self,
            f"{kind} {code}",
            f"{code}: {desc}",
            self.lang
        ).exec()

    def show_log(self, uid):
        code, desc, log = self.errors[uid]
        TextDialog(
            self,
            f"{code} — {desc}",
            log or tr(self.lang, "no_log"),
            self.lang
        ).exec()

    def open_settings(self):
        dlg = Settings(self, self.cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                v = dlg.values()
                if not v["default_download_dir"]:
                    raise ValueError(tr(v.get("language", self.lang), "default_folder_empty"))

                Path(v["default_download_dir"]).expanduser().mkdir(
                    parents=True, exist_ok=True
                )

                old_lang = self.lang
                self.cfg = v
                save_cfg(v)
                apply_app_theme(QApplication.instance(), v.get("theme", "system"))
                self.apply_ui_preferences()
                self.pl.setChecked(False)
                self.dest.setText(v.get("default_subfolder", ""))

                if self.lang != old_lang:
                    # Error descriptions already generated remain valid text,
                    # but all active UI and queue labels change immediately.
                    self.apply_language()
                else:
                    self.refresh_label()
                    self.update_pl()

                # Teksty przycisków/tytułów mogły zmienić naturalną wysokość
                # paneli; przelicz zakres jeszcze raz po odświeżeniu języka.
                self.apply_panel_row_limits()

            except ValueError as e:
                QMessageBox.warning(
                    self,
                    tr(self.lang, "settings_error"),
                    str(e)
                )

    def open_dir(self):
        p = Path(self.cfg["default_download_dir"]).expanduser()
        try:
            if not open_local_folder(p):
                raise RuntimeError(str(p))
        except Exception as exc:
            QMessageBox.warning(
                self, APP_NAME, f"{tr(self.lang, 'open_default')}: {exc}"
            )

    def set_busy(self, busy):
        self.busy = busy

        # During downloading the user may still prepare and append new items.
        # We only lock settings and operations that could change row indexes.
        for w in (
            self.settings_btn, self.clear, self.download_all
        ):
            w.setEnabled(not busy)
        self.remove.setEnabled(True)

        for w in (
            self.url, self.paste_btn, self.pl, self.media, self.fmt,
            self.dest, self.dest_paste_btn, self.dest_btn, self.add_btn,
            self.preset_combo, self.preset_save_btn, self.preset_delete_btn,
            self.subtitle_mode, self.subtitle_lang, self.subtitle_output
        ):
            w.setEnabled(True)

        self.stop.setEnabled(busy)
        self.refresh_quality(remember=False)
        self.update_pl()
        self.update_subtitle_state()
        self.preset_delete_btn.setEnabled(self.preset_combo.currentData() is not None)

    def start_dl(self):
        if self.busy:
            return

        waiting = [it for it in self.queue if self.item_state.get(it.uid, "waiting") == "waiting"]
        if not waiting:
            QMessageBox.information(self, APP_NAME, tr(self.lang, "empty_queue"))
            return

        if not tool_available("yt-dlp") or not tool_available("ffmpeg"):
            QMessageBox.critical(self, APP_NAME, tr(self.lang, "missing_tools"))
            return

        if self.thread is not None:
            QMessageBox.warning(self, APP_NAME, tr(self.lang, "thread_closing"))
            return

        try:
            cookies_args(self.cfg)
        except AppError as e:
            QMessageBox.warning(self, e.code, e.desc)
            return

        self.errors.clear()
        self.log.clear()
        self.was_stopped = False
        self.session_uids = {it.uid for it in waiting}
        self.session_started_at = time.monotonic()
        self.session_elapsed_final = 0.0
        self.session_downloaded_duration = 0.0
        self.current_uid = None
        self.current_started_at = None
        self.set_busy(True)
        self._start_batch()
        self.update_stats_ui()

    def _start_batch(self):
        waiting = [
            it for it in self.queue
            if self.item_state.get(it.uid, "waiting") == "waiting"
        ]
        if not waiting:
            return

        self.active_batch_uids = {it.uid for it in waiting}
        snap = [Item(**asdict(it)) for it in waiting]

        self.thread = QThread(self)
        self.worker = Worker(snap, dict(self.cfg))
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.started.connect(self.started)
        self.worker.metadata.connect(self.metadata_ready)
        self.worker.done.connect(self.finished)
        self.worker.all_done.connect(self.worker_finished)
        self.worker.all_done.connect(self.thread.quit)
        self.worker.all_done.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_finished)
        self.thread.start()

    def stop_dl(self):
        if self.worker:
            self.worker.stop()
            self.append_log(tr(self.lang, "stop_requested"))

    def append_log(self, text):
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum()
        )

    def started(self, uid):
        row = self.row_for_uid(uid)
        if row < 0:
            if self.worker:
                self.worker.skip(uid)
            return
        self.item_state[uid] = "downloading"
        self.current_uid = uid
        self.current_started_at = time.monotonic()
        self.table.item(row, 6).setText(tr(self.lang, "downloading"))
        self.update_stats_ui()

    def metadata_ready(self, uid, duration, entries_count):
        it = self.item_for_uid(uid)
        if it is None:
            return
        if duration > 0:
            it.duration_s = float(duration)
        if entries_count > 0:
            it.entries_count = int(entries_count)
        self.update_stats_ui()

    def finished(self, uid, ok, code, desc, log, elapsed, duration, sample_valid):
        it = self.item_for_uid(uid)
        row = self.row_for_uid(uid)
        if it is None or row < 0:
            return

        if duration > 0:
            it.duration_s = float(duration)

        cell = self.table.item(row, 6)
        if ok:
            self.item_state[uid] = "done"
            cell.setText(
                tr(self.lang, "drc_fallback_status")
                if code == "OK_DRC_FALLBACK"
                else tr(self.lang, "ready")
            )
            cell.setToolTip(desc)
        elif code == "STOP":
            # Keep it eligible for a future manual restart.
            self.item_state[uid] = "waiting"
            cell.setText(tr(self.lang, "stopped"))
            cell.setToolTip(desc)
        elif code.startswith("W"):
            self.item_state[uid] = "warning"
            cell.setText(f"{tr(self.lang, 'warning')} {code}")
            cell.setToolTip(desc + "\n" + tr(self.lang, "warning_hint"))
            self.errors[uid] = (code, desc, log)
        else:
            self.item_state[uid] = "error"
            cell.setText(f"{tr(self.lang, 'error')} {code}")
            cell.setToolTip(desc + "\n" + tr(self.lang, "error_hint"))
            self.errors[uid] = (code, desc, log)

        if (ok or code.startswith("W")) and it.duration_s > 0:
            self.session_downloaded_duration += it.duration_s
            if sample_valid:
                try:
                    self.stats_db.record(it, it.duration_s, elapsed)
                except Exception:
                    pass

        if code != "STOP":
            try:
                hist_status = "done" if ok else ("warning" if code.startswith("W") else "error")
                self.stats_db.record_download(
                    it, hist_status, code, it.duration_s, elapsed,
                    destination=str(dest_path(it.destination, self.cfg)),
                    error_desc=desc if not ok else "",
                    error_log=log if not ok else "",
                )
                if self.cfg.get("show_history", False):
                    self.refresh_history()
            except Exception:
                pass
        self.save_queue_state()

        if self.current_uid == uid:
            self.current_uid = None
            self.current_started_at = None
        self.update_stats_ui()

    def worker_finished(self, stopped):
        self.was_stopped = bool(stopped)

    def thread_finished(self):
        thread = self.thread
        self.worker = None
        self.thread = None
        self.active_batch_uids.clear()

        if thread:
            thread.deleteLater()

        waiting = [
            it for it in self.queue
            if self.item_state.get(it.uid) == "waiting"
        ]
        if not self.was_stopped and waiting:
            self.append_log("\n" + tr(self.lang, "queue_extended"))
            self._start_batch()
            return

        if self.session_started_at is not None:
            self.session_elapsed_final = max(0.0, time.monotonic() - self.session_started_at)
        self.set_busy(False)
        self.current_uid = None
        self.current_started_at = None
        self.append_log(
            "\n" + (
                tr(self.lang, "queue_stopped")
                if self.was_stopped
                else tr(self.lang, "queue_done")
            )
        )
        self.update_stats_ui()

    def enqueue_probe(self, uid):
        it = self.item_for_uid(uid)
        if it is None or it.duration_s > 0:
            return
        if uid not in self.probe_queue:
            self.probe_queue.append(uid)
        self._start_next_probe()

    def _start_next_probe(self):
        if self.probe_thread is not None:
            return
        while self.probe_queue:
            uid = self.probe_queue.pop(0)
            it = self.item_for_uid(uid)
            if it is None or it.duration_s > 0:
                continue
            snap = Item(**asdict(it))
            self.probe_thread = QThread(self)
            self.probe_worker = ProbeWorker(snap, dict(self.cfg))
            self.probe_worker.moveToThread(self.probe_thread)
            self.probe_thread.started.connect(self.probe_worker.run)
            self.probe_worker.done.connect(self.metadata_ready)
            self.probe_worker.finished.connect(self.probe_thread.quit)
            self.probe_worker.finished.connect(self.probe_worker.deleteLater)
            self.probe_thread.finished.connect(self._probe_finished)
            self.probe_thread.start()
            return

    def _probe_finished(self):
        thread = self.probe_thread
        self.probe_worker = None
        self.probe_thread = None
        if thread:
            thread.deleteLater()
        self._start_next_probe()

    def update_stats_ui(self):
        if not hasattr(self, "stats_label"):
            return

        if self.session_started_at is None:
            elapsed = self.session_elapsed_final
        elif self.busy:
            elapsed = max(0.0, time.monotonic() - self.session_started_at)
        else:
            elapsed = self.session_elapsed_final

        known_total = 0.0
        done_for_progress = 0.0
        eta_seconds = 0.0
        unknown = 0

        for uid in list(self.session_uids):
            it = self.item_for_uid(uid)
            if it is None:
                continue
            state = self.item_state.get(uid, "waiting")
            if state == "error":
                continue

            duration = float(it.duration_s or 0)
            if duration <= 0:
                if state in ("waiting", "downloading"):
                    unknown += 1
                continue

            known_total += duration
            if state in ("done", "warning"):
                done_for_progress += duration
                continue

            if state not in ("waiting", "downloading"):
                continue

            ratio = self.stats_db.ratio(it)
            if ratio is None or ratio <= 0:
                unknown += 1
                continue

            predicted = duration * ratio
            if state == "downloading" and uid == self.current_uid and self.current_started_at is not None:
                current_elapsed = max(0.0, time.monotonic() - self.current_started_at)
                eta_seconds += max(0.0, predicted - current_elapsed)
                if predicted > 0:
                    done_for_progress += duration * min(0.98, current_elapsed / predicted)
            else:
                eta_seconds += predicted

        if known_total > 0:
            progress = int(max(0.0, min(1.0, done_for_progress / known_total)) * 1000)
        else:
            progress = 0
        self.queue_progress.setValue(progress)
        self.queue_progress.setFormat(("~%p%" if unknown else "%p%"))

        if self.busy:
            if eta_seconds > 0:
                eta = "~" + human_time(eta_seconds)
                if unknown:
                    eta = tr(self.lang, "stats_eta_more", eta=eta, n=unknown)
            else:
                eta = tr(self.lang, "stats_eta_calc") if unknown else human_time(0)
        else:
            eta = tr(self.lang, "stats_idle_eta")

        first_line = "    |    ".join([
            tr(self.lang, "stats_downloaded", done=human_time(self.session_downloaded_duration)),
            tr(self.lang, "stats_work", elapsed=human_time(elapsed)),
        ])
        second_line = tr(self.lang, "stats_eta", eta=eta)
        self.stats_label.setText(first_line + "\n" + second_line)

    def closeEvent(self, event):
        self.remember_current_choices()

        if self.busy and self.thread:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                tr(self.lang, "close_running"),
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            if self.worker:
                self.worker.stop()

            self.thread.quit()
            self.thread.wait(5000)

        if self.probe_thread is not None:
            if self.probe_worker is not None:
                self.probe_worker.stop()
            self.probe_thread.quit()
            self.probe_thread.wait(5000)

        if self.format_probe_thread is not None:
            if self.format_probe_worker is not None:
                self.format_probe_worker.stop()
            self.format_probe_thread.quit()
            self.format_probe_thread.wait(5000)

        self.save_window_geometry()
        self.save_queue_state()
        event.accept()

def runtime_self_test():
    """Small non-GUI smoke test used by AppImage CI.

    It deliberately exercises the cookie command path because missing helper
    functions there are runtime errors that py_compile cannot detect.
    """
    errors = []
    try:
        no_cookie = cookies_args({
            "language": "en",
            "cookies_browser": "none",
            "cookies_file_enabled": False,
        })
        if no_cookie != []:
            errors.append(f"unexpected no-cookie args: {no_cookie!r}")
        defaults = load_cfg()
        # In CI/AppImage this may read a portable config only if one exists;
        # fresh builds should still expose the factory defaults below.
        if not CFG_FILE.exists():
            if defaults.get("language") != "en":
                errors.append(f"fresh default language is not English: {defaults.get('language')!r}")
            expected_rows = (4, 5, 3, 4, 3, 4)
            got_rows = (
                defaults.get("queue_rows_min"), defaults.get("queue_rows_max"),
                defaults.get("history_rows_min"), defaults.get("history_rows_max"),
                defaults.get("log_rows_min"), defaults.get("log_rows_max"),
            )
            if got_rows != expected_rows:
                errors.append(f"fresh row defaults are wrong: {got_rows!r}")
    except Exception as exc:
        errors.append(f"cookies_args(no login) failed: {exc!r}")

    if os.name != "nt":
        try:
            browser_cookie = cookies_args({
                "language": "en",
                "cookies_browser": "brave",
                "cookies_profile": "",
                "cookies_keyring": "",
                "cookies_file_enabled": False,
            })
            if not (len(browser_cookie) == 2 and browser_cookie[0] == "--cookies-from-browser" and str(browser_cookie[1]).startswith("brave")):
                errors.append(f"unexpected browser-cookie args: {browser_cookie!r}")

            # Regression check for 0.4.37: automatic mode must remain true
            # yt-dlp auto-detection even on Plasma 6.  Explicit kwallet6 is
            # used only when the user selects it in Settings.
            old_desktop = os.environ.get("XDG_CURRENT_DESKTOP")
            old_kde = os.environ.get("KDE_SESSION_VERSION")
            try:
                os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
                os.environ["KDE_SESSION_VERSION"] = "6"
                kde_cookie = cookies_args({
                    "language": "en",
                    "cookies_browser": "brave",
                    "cookies_profile": "",
                    "cookies_keyring": "",
                    "cookies_file_enabled": False,
                })
                if kde_cookie != ["--cookies-from-browser", "brave"]:
                    errors.append(f"automatic KDE6 mode unexpectedly forced a keyring: {kde_cookie!r}")

                explicit_kde_cookie = cookies_args({
                    "language": "en",
                    "cookies_browser": "brave",
                    "cookies_profile": "",
                    "cookies_keyring": "kwallet6",
                    "cookies_file_enabled": False,
                })
                if explicit_kde_cookie != ["--cookies-from-browser", "brave+kwallet6"]:
                    errors.append(f"explicit KDE6 cookie args are wrong: {explicit_kde_cookie!r}")
            finally:
                if old_desktop is None:
                    os.environ.pop("XDG_CURRENT_DESKTOP", None)
                else:
                    os.environ["XDG_CURRENT_DESKTOP"] = old_desktop
                if old_kde is None:
                    os.environ.pop("KDE_SESSION_VERSION", None)
                else:
                    os.environ["KDE_SESSION_VERSION"] = old_kde
        except Exception as exc:
            errors.append(f"cookies_args(brave) failed: {exc!r}")

    try:
        if audio_stream_selector("m4a", "128", "drc") != "140-drc/140":
            errors.append("M4A DRC selector/fallback is wrong")
        if audio_stream_selector("opus", "128", "drc") != "251-drc/251":
            errors.append("Opus DRC selector/fallback is wrong")
        drc_test_item = Item(
            playlist=False, destination="", url="https://example.invalid",
            media="audio", fmt="m4a", quality="128", audio_variant="drc",
        )
        if not drc_fallback_used(drc_test_item, "[info] x: Downloading 1 format(s): 140"):
            errors.append("DRC fallback detection missed standard format")
        if drc_fallback_used(drc_test_item, "[info] x: Downloading 1 format(s): 140-drc"):
            errors.append("DRC fallback detection misclassified DRC format")
    except Exception as exc:
        errors.append(f"DRC selector self-test failed: {exc!r}")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "language": "en",
                "default_download_dir": tmp,
                "cookies_browser": "none",
                "cookies_file_enabled": False,
                "no_overwrites": True,
                "embed_metadata": True,
                "embed_thumbnail": True,
            }
            preview_item = Item(
                playlist=False,
                destination="Echo",
                url="https://www.youtube.com/watch?v=VIDEO_ID",
                media="audio",
                fmt="m4a",
                quality="128",
            )
            build_cmd(preview_item, cfg)
            if (Path(tmp) / "Echo").exists():
                errors.append("build_cmd created destination during command preview")
    except Exception as exc:
        errors.append(f"side-effect-free command preview test failed: {exc!r}")

    try:
        ctx = github_ssl_context()
        if ctx is None:
            errors.append("GitHub SSL context was not created")
        if getattr(sys, "frozen", False):
            if certifi is None:
                errors.append("certifi is missing from frozen AppImage")
            else:
                ca_file = Path(certifi.where())
                if not ca_file.is_file():
                    errors.append(f"bundled certifi CA bundle not found: {ca_file}")
    except Exception as exc:
        errors.append(f"GitHub SSL context self-test failed: {exc!r}")

    if PORTABLE and os.environ.get("APPIMAGE"):
        for tool in ("yt-dlp", "ffmpeg", "ffprobe", "deno"):
            if not tool_available(tool):
                errors.append(f"bundled tool not found: {tool}")

        # PyInstaller changes LD_LIBRARY_PATH for the frozen GUI.  External
        # subprocesses must see the original host value instead.
        if getattr(sys, "frozen", False):
            current = os.environ.get("LD_LIBRARY_PATH")
            original = os.environ.get("LD_LIBRARY_PATH_ORIG")
            sanitized = external_subprocess_env().get("LD_LIBRARY_PATH")
            expected = original if original else None
            if sanitized != expected:
                errors.append(
                    f"external subprocess environment not sanitized: "
                    f"current={current!r}, original={original!r}, sanitized={sanitized!r}"
                )

    if errors:
        for error in errors:
            print(f"SELF-TEST ERROR: {error}", file=sys.stderr)
        return 1

    print(f"YT-Downloader {VERSION} self-test: OK")
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        ensure_portable_dirs()
        raise SystemExit(runtime_self_test())

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    try:
        app.setDesktopFileName("yt-downloader")
    except Exception:
        pass

    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))

    # Keep Open/Cancel, Yes/No, Close etc. consistent in every Qt dialog.
    app._dialog_button_metrics_filter = DialogButtonMetricsFilter(app)
    app.installEventFilter(app._dialog_button_metrics_filter)

    # Geometry is enforced with setFixedHeight() in both Main and Settings.
    # The stylesheet only normalizes horizontal breathing room and restores a
    # subtle, rounded frame for text fields in themes where the native frame
    # is nearly invisible. Palette roles keep it usable in light/dark/system.
    app.setStyleSheet("""
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top center;
            padding-left: 4px;
            padding-right: 4px;
        }
        QGroupBox#primarySectionTitle::title {
            font-size: 11pt;
            font-weight: 500;
        }
        QPushButton {
            min-width: 0px;
            padding-left: 10px;
            padding-right: 10px;
        }
        QComboBox {
            padding-left: 0px;
            padding-right: 10px;
        }
        QComboBox QAbstractItemView {
            padding-left: 8px;
            padding-right: 6px;
        }
        QSpinBox {
            padding-left: 9px;
            padding-right: 7px;
        }
        QLineEdit {
            border: 1px solid rgba(127, 127, 127, 150);
            border-radius: 5px;
            background-color: palette(base);
            padding-left: 9px;
            padding-right: 9px;
        }
        QLineEdit:focus {
            border: 1px solid palette(highlight);
        }
        QLineEdit:disabled {
            border-color: rgba(127, 127, 127, 80);
            background-color: palette(window);
            color: palette(mid);
        }
    """)

    ensure_portable_dirs()
    window = Main()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
