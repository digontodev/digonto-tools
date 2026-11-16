"""Android screenshots over adb."""
import os
import shutil
import sys
import subprocess


def platform_tools_root():
    """Where the Digonto installers (and the Setup tab) keep Google's platform-tools."""
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA", home), "Digonto", "digonto-tools")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Digonto", "digonto-tools")
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share")), "digonto", "digonto-tools")


def adb_path():
    found = shutil.which("adb")
    if found:
        return found
    exe = "adb.exe" if os.name == "nt" else "adb"
    cands = [os.path.join(platform_tools_root(), "platform-tools", exe)]
    base = os.environ.get("LOCALAPPDATA")
    if base:
        cands.append(os.path.join(base, "ImageTools", "platform-tools", "adb.exe"))       # older version
    home = os.path.expanduser("~")
    cands += [os.path.join(home, "Library", "Application Support", "ImageTools", "platform-tools", "adb"),
              "/opt/homebrew/bin/adb", "/usr/local/bin/adb"]
    for cand in cands:
        if os.path.exists(cand):
            return cand
    return None


_NOWIN = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no console flash on Windows
_used = False


def shutdown():
    """adb leaves a background server running after its first command; stop it when the program quits
    (only the adb that Digonto installed itself - an adb you installed yourself is left alone)"""
    exe = adb_path()
    if not (_used and exe and os.path.abspath(exe).startswith(os.path.abspath(platform_tools_root()))):
        return
    try:
        subprocess.run([exe, "kill-server"], capture_output=True, timeout=5, **_NOWIN)
    except Exception:
        pass


def devices():
    global _used
    _used = True
    exe = adb_path()
    if not exe:
        return None  # adb not installed
    out = subprocess.run([exe, "devices"], capture_output=True, text=True, timeout=15, **_NOWIN).stdout
    res = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            res.append((parts[0], parts[1]))
    return res


def screencap_png(device=None):
    exe = adb_path()
    if not exe:
        raise RuntimeError("adb is not installed")
    cmd = [exe] + (["-s", device] if device else []) + ["exec-out", "screencap", "-p"]
    r = subprocess.run(cmd, capture_output=True, timeout=30, **_NOWIN)
    if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
        raise RuntimeError((r.stderr or b"").decode(errors="replace").strip() or "adb returned no image")
    return r.stdout


# ----------------------------------------------------------------------
# Wireless debugging: add a device by ip:port
# ----------------------------------------------------------------------

def normalize_addr(text, default_port=5555):
    """'192.168.1.20' -> '192.168.1.20:5555'. Returns None if it does not look like an address."""
    t = (text or "").strip()
    if not t:
        return None
    if ":" not in t:
        t = f"{t}:{default_port}"
    host, _, port = t.rpartition(":")
    if not host or not port.isdigit() or not (1 <= int(port) <= 65535):
        return None
    return t


def _run(args, timeout=25):
    global _used
    _used = True
    exe = adb_path()
    if not exe:
        return False, "adb is not installed"
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout, **_NOWIN)
    except subprocess.TimeoutExpired:
        return False, "adb did not answer in time (is the phone on the same Wi-Fi?)"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    low = out.lower()
    ok = r.returncode == 0 and "failed" not in low and "cannot" not in low and "unable" not in low and "error" not in low
    return ok, out or "(no answer)"


def connect(addr):
    a = normalize_addr(addr)
    if not a:
        return False, "Type the address like 192.168.1.20:5555 (IP address, colon, port)."
    return _run(["connect", a], timeout=20)


def disconnect(addr=None):
    a = normalize_addr(addr) if addr else None
    return _run(["disconnect"] + ([a] if a else []))


def pair(addr, code):
    """Android 11+: Developer options > Wireless debugging > Pair device with pairing code."""
    a = normalize_addr(addr)
    if not a:
        return False, "Type the pairing address like 192.168.1.20:37215."
    code = (code or "").strip()
    if not code:
        return False, "Type the 6-digit pairing code shown on the phone."
    ok, out = _run(["pair", a, code], timeout=30)
    return ok and "success" in out.lower(), out
