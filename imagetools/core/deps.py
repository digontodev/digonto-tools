"""Which packages are present, and installing missing ones.

Installs ONLY from the locked files next to the app:
    requirements.txt      core packages
    requirements-ai.txt   AI background removal (rembg and what it needs)
Each file lists exact versions with SHA-256 fingerprints; pip refuses any
download that does not match (--require-hashes).
"""
import importlib
import os
import platform
import subprocess
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CORE_REQ = os.path.join(APP_ROOT, "requirements.txt")
AI_REQ = os.path.join(APP_ROOT, "requirements-ai.txt")

PACKAGES = [
    # (import name, pip name, required?, purpose)
    ("numpy", "numpy", True, "image maths"),
    ("cv2", "opencv-python-headless", True, "crop / match / background removal"),
    ("PIL", "pillow", True, "images"),
    ("PySide6", "PySide6-Essentials", True, "window toolkit"),
    ("rembg", "rembg", False, "AI background removal (large download)"),
]


def ai_supported():
    """Digonto Studio carries its own AI helper (also for Intel and Apple-chip Macs). Without it, pip installs
    rembg - whose engine has no new builds for Intel Macs."""
    from . import aihelper
    if aihelper.available():
        return True
    return not (sys.platform == "darwin" and platform.machine() == "x86_64")


def status():
    out = []
    for mod, pipname, required, why in PACKAGES:
        try:
            importlib.import_module(mod)
            ok = True
        except Exception:
            ok = False
        if mod == "rembg" and not ok:
            from . import aihelper
            if aihelper.available():
                ok = aihelper.enabled()
                pipname, why = "AI helper", "AI background removal (offline, included in Digonto Studio)"
        out.append((mod, pipname, required, why, ok))
    return out


def _run(cmd, log):
    log(" ".join(cmd))
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    log((r.stdout or "")[-1500:] + (r.stderr or "")[-1500:])
    return r


def install_locked(ai, log):
    """Install the core set, plus the AI set when ai=True. Returns True on success."""
    files = [CORE_REQ] + ([AI_REQ] if ai else [])
    if ai:
        from . import aihelper
        if aihelper.available():                     # Studio: switch on the helper from the install folder
            return aihelper.enable(log)
    if os.environ.get("DIGONTO_STANDALONE") == "1":
        log("This is the standalone (offline) edition: everything it can use is already inside. "
            "AI background removal is not included; Auto and Manual background removal work.")
        return False
    if ai and not ai_supported():
        log("AI background removal is not available on Intel Macs (no builds published). "
            "Auto and Manual background removal still work.")
        return False
    for f in files:
        if not os.path.exists(f):
            log(f"Missing file: {f} - reinstall Digonto Tools.")
            return False
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--require-hashes", "--only-binary=:all:", "--no-deps"]
    args = []
    for f in files:
        args += ["-r", f]
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    r = _run(base + args, log)
    if r.returncode != 0 and not in_venv and "externally-managed" in (r.stderr or ""):
        r = _run(base + ["--user", "--break-system-packages"] + args, log)
    importlib.invalidate_caches()
    return r.returncode == 0


def pip_install(pipname, log):
    """Kept for older callers: installs the locked set that contains pipname."""
    return install_locked(pipname.startswith("rembg"), log)


# ----------------------------------------------------------------------
# System helpers (not Python packages): adb and the screenshot tools
# ----------------------------------------------------------------------
import shutil  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402

ADB_URL = "https://dl.google.com/android/repository/platform-tools-latest-{}.zip"


def _linux_session():
    return "wayland" if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or os.environ.get("WAYLAND_DISPLAY") else "x11"


def system_status():
    """[(key, label, why, ok, needed_here)] - needed_here False means 'not needed on this computer'."""
    from . import adbcap
    out = [("adb", "adb (Android)", "phone screenshots over USB / Wi-Fi", bool(adbcap.adb_path()), True)]
    if sys.platform.startswith("linux"):
        wl = _linux_session() == "wayland"
        has_shot = any(shutil.which(t) for t in ("gnome-screenshot", "spectacle", "grim"))
        out.append(("shot", "screenshot helper", "screen capture on Wayland (gnome-screenshot)" if wl else
                    "not needed on X11 (your session)", has_shot or not wl, wl))
        out.append(("xwininfo", "x11-utils (xwininfo)", "click-a-window capture on X11",
                    bool(shutil.which("xwininfo")) or wl, not wl))
    elif sys.platform == "darwin":
        out.append(("shot", "screencapture", "built into macOS", bool(shutil.which("screencapture")), True))
    return out


def install_adb(log):
    """Downloads Google's platform-tools next to the app (no administrator rights needed)."""
    import urllib.request
    from . import adbcap
    if adbcap.adb_path():
        log("adb already installed: " + adbcap.adb_path())
        return True
    plat = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    url = ADB_URL.format(plat)
    root = adbcap.platform_tools_root()
    os.makedirs(root, exist_ok=True)
    log("Downloading Android platform-tools from Google: " + url)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            z = os.path.join(tmp, "pt.zip")
            if sys.platform == "darwin" and shutil.which("curl"):
                subprocess.run(["curl", "-fsSL", "-o", z, url], check=True, timeout=600)
            else:
                urllib.request.urlretrieve(url, z)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(root)
        exe = adbcap.adb_path()
        if exe and os.name != "nt":
            for f in ("adb", "fastboot"):
                p = os.path.join(root, "platform-tools", f)
                if os.path.exists(p):
                    os.chmod(p, 0o755)
        log("adb installed" if exe else "adb download finished but adb was not found")
        return bool(exe)
    except Exception as e:  # noqa: BLE001
        log(f"adb download failed: {e}")
        return False


def install_linux_tools(log):
    """Screenshot helpers need administrator rights - asked through the normal password window (pkexec)."""
    wl = _linux_session() == "wayland"
    pk = ["gnome-screenshot"] if wl else ["x11-utils"]
    if shutil.which("apt-get"):
        cmd = ["apt-get", "install", "-y"] + pk
    elif shutil.which("dnf"):
        cmd = ["dnf", "install", "-y"] + ["gnome-screenshot" if wl else "xorg-x11-utils"]
    elif shutil.which("pacman"):
        cmd = ["pacman", "-S", "--noconfirm"] + ["gnome-screenshot" if wl else "xorg-xwininfo"]
    else:
        log("Please install: " + ", ".join(pk))
        return False
    if shutil.which("pkexec"):
        full = ["pkexec"] + cmd
    else:
        log("Run this in a terminal:  sudo " + " ".join(cmd))
        return False
    log(" ".join(full))
    r = subprocess.run(full, capture_output=True, text=True)
    log((r.stdout or "")[-800:] + (r.stderr or "")[-800:])
    return r.returncode == 0
