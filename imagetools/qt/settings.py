import json
import os
import platform


def config_dir():
    s = platform.system()
    if s == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif s == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    d = os.path.join(base, "DigontoTools")
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        old = next((o for o in (os.path.join(base, "DigontoImages", "settings.json"),
                                os.path.join(base, "ImageTools", "settings.json")) if os.path.exists(o)), "")
        if old:
            try:
                import shutil
                shutil.copy2(old, os.path.join(d, "settings.json"))
            except OSError:
                pass
    return d


def default_workdir():
    """Keep using ~/ImageTools if an older version created it (your templates are there)."""
    home = os.path.expanduser("~")
    old = os.path.join(home, "ImageTools")
    if os.path.isdir(old):
        return old
    prev = os.path.join(home, "Digonto Images")
    return prev if os.path.isdir(prev) else os.path.join(home, "Digonto Tools")


class Settings:
    def __init__(self):
        self.path = os.path.join(config_dir(), "settings.json")
        self.data = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:  # noqa: BLE001
            pass

    def get(self, k, default=None):
        return self.data.get(k, default)

    def set(self, k, v):
        self.data[k] = v
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:  # noqa: BLE001
            pass
