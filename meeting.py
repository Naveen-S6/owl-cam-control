"""Meeting detection from Windows' own record of which apps are using the camera / microphone.

Windows keeps, per app, LastUsedTimeStart/LastUsedTimeStop under CapabilityAccessManager\\ConsentStore.
LastUsedTimeStop == 0 means the app is using the device right now (same signal as the taskbar mic icon).
"""
import sys
import winreg
from pathlib import Path

CONSENT_STORE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
OWN_INTERPRETER_DIR = str(Path(getattr(sys, "_base_executable", sys.executable)).parent).replace("\\", "#").lower()


def app_label(key_name):
    """Friendly app name from a ConsentStore key ('C:#..#Zoom.exe' -> 'Zoom', 'MSTeams_8wek..' -> 'MSTeams')."""
    if "#" in key_name:
        return Path(key_name.replace("#", "\\")).stem
    return key_name.split("_")[0].split(".")[-1]


def _entries(capability):
    """Yield (key_name, start, stop) for every app recorded under a capability ('webcam' / 'microphone')."""
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{CONSENT_STORE}\\{capability}")
    except OSError:
        return
    with root:
        for name in _subkeys(root):
            if name == "NonPackaged":
                with winreg.OpenKey(root, name) as nonpackaged:
                    for sub in _subkeys(nonpackaged):
                        yield _read(nonpackaged, sub)
            else:
                yield _read(root, name)


def _subkeys(key):
    """List subkey names of an open registry key."""
    return [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]


def _read(parent, name):
    """Return (name, LastUsedTimeStart, LastUsedTimeStop) for one app key (missing values -> 0)."""
    with winreg.OpenKey(parent, name) as key:
        values = {}
        for value in ("LastUsedTimeStart", "LastUsedTimeStop"):
            try:
                values[value] = winreg.QueryValueEx(key, value)[0]
            except OSError:
                values[value] = 0
    return name, values["LastUsedTimeStart"], values["LastUsedTimeStop"]


def apps_in_use(ignore=()):
    """Sorted friendly names of other apps using the camera or mic right now.

    ignore: app names (case-insensitive substrings) to skip, e.g. dictation tools. This app's own
    Python interpreter is always skipped (it reads the camera for the preview / virtual camera).
    """
    ignore = [i.lower() for i in ignore]
    found = set()
    for capability in ("webcam", "microphone"):
        for name, start, stop in _entries(capability):
            lowered = name.lower()
            if not start or stop or lowered.startswith(OWN_INTERPRETER_DIR):
                continue
            label = app_label(name)
            if not any(i in label.lower() or i in lowered for i in ignore):
                found.add(label)
    return sorted(found)


class MeetingWatcher:
    """Turns periodic 'apps in use' samples into events.

    Events from update():
      ("meeting", apps)  - apps have used the camera/mic for start_delay seconds (debounces short dictation)
      ("idle_off",)      - sending is on but no meeting for idle_off seconds
    """

    def __init__(self, start_delay=8, idle_off=180):
        """start_delay / idle_off in seconds."""
        self.start_delay, self.idle_off = start_delay, idle_off
        self.active_since, self.announced, self.idle_from = None, False, None

    def update(self, apps, now, sending):
        """Feed one sample (apps in use, monotonic seconds, is 'send to apps' on); return events."""
        events = []
        if apps:
            if self.active_since is None:
                self.active_since = now
            if not self.announced and now - self.active_since >= self.start_delay:
                self.announced = True
                events.append(("meeting", apps))
        else:
            self.active_since, self.announced = None, False
        if apps or not sending or self.idle_from is None:
            self.idle_from = now  # idle clock starts when sending turns on / a meeting ends
        elif now - self.idle_from >= self.idle_off:
            self.idle_from = now
            events.append(("idle_off",))
        return events


if __name__ == "__main__":
    print("In use now:", apps_in_use() or "nothing")
