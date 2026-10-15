"""Settings persistence (presets + last-used values) and the Windows 'start at login' toggle."""
import json
import logging
import os
import sys
import winreg
from pathlib import Path

APP_NAME = "OwlCamControl"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
SETTINGS_FILE = APP_DIR / "settings.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAX_PRESET_NAME = 40

log = logging.getLogger(__name__)


def default_settings():
    """Return a fresh settings dict."""
    return {"camera_name": "Owl", "last": {}, "presets": {}, "active_preset": None, "default_preset": None,
            "virtual_camera": False,
            "meeting_popups": True, "auto_off_minutes": 3,
            "meeting_ignore_apps": ["Wispr Flow"]}  # dictation tools use the mic but aren't meetings


def load(path=SETTINGS_FILE):
    """Read settings from path. Missing file -> defaults; corrupt file -> backed up, then defaults."""
    data = default_settings()
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("settings root is not an object")
        data.update({k: v for k, v in loaded.items() if k in data})
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as e:
        log.warning("Settings file unreadable (%s); backing up and starting fresh", e)
        Path(path).replace(Path(path).with_suffix(".corrupt.json"))
    return data


def save(data, path=SETTINGS_FILE):
    """Write settings atomically (temp file + rename) so a crash can't corrupt them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def clean_preset_name(name):
    """Return a trimmed preset name, or raise ValueError if it is empty or too long."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Preset name can't be empty.")
    if len(name) > MAX_PRESET_NAME:
        raise ValueError(f"Preset name must be {MAX_PRESET_NAME} characters or fewer.")
    return name


def _launch_command():
    """Command line that starts this app without a console window."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).with_name("app.pyw").resolve()}"'


def startup_enabled():
    """True if the app is registered to start at Windows login."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False


def set_startup(enabled):
    """Add or remove the HKCU Run entry that launches the app at login."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
