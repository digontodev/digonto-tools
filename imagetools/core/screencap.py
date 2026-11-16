"""Screenshots of this computer: whole screen, a dragged area, or one window.

Windows / macOS / Linux (X11): Pillow's grab. Linux Wayland and macOS use the system's own tools
(gnome-screenshot, spectacle, grim, screencapture) because programs may not read the screen directly there.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageGrab

_NOWIN = {"creationflags": 0x08000000} if os.name == "nt" else {}


class CaptureError(Exception):
    pass


def enable_dpi_awareness():
    """Windows: make Tk and the screen grab use real pixels (call once, before creating the window)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def is_wayland():
    return sys.platform.startswith("linux") and (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
                                                 or bool(os.environ.get("WAYLAND_DISPLAY")))


def kind():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "wayland" if is_wayland() else "x11"


def helper_tools():
    """Which system tools exist (for the Setup tab). Returns list of (name, found, purpose)."""
    k = kind()
    if k == "wayland":
        return [("gnome-screenshot / spectacle / grim", any(shutil.which(t) for t in ("gnome-screenshot", "spectacle", "grim")),
                 "screen capture on Wayland")]
    if k == "x11":
        return [("xwininfo", bool(shutil.which("xwininfo")), "pick a window to capture (x11-utils)")]
    return []


def _tmp_png():
    fd, p = tempfile.mkstemp(suffix=".png", prefix="digonto_cap_")
    os.close(fd)
    return p


def _run_tool(args, out, timeout=120):
    try:
        subprocess.run(args, capture_output=True, timeout=timeout, **_NOWIN)
    except subprocess.TimeoutExpired:
        raise CaptureError("The system screenshot helper did not answer. On Ubuntu (Wayland) use 'Full screen' or "
                           "'Select area' - choosing one window is not supported by the desktop there.")
    except Exception as e:
        raise CaptureError(str(e))
    if os.path.exists(out) and os.path.getsize(out) > 100:
        img = Image.open(out)
        img.load()
        try:
            os.remove(out)
        except OSError:
            pass
        return img.convert("RGB")
    return None            # cancelled


_PORTAL_SCRIPT = r"""
import sys, os
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
interactive = sys.argv[1] == '1'
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
sender = bus.get_unique_name()[1:].replace('.', '_')
token = 'digonto%d' % os.getpid()
path = '/org/freedesktop/portal/desktop/request/%s/%s' % (sender, token)
loop = GLib.MainLoop()
res = {}
def on_resp(conn, sname, opath, iface, signame, params, data):
    code, r = params.unpack()
    res['code'] = code
    res['uri'] = r.get('uri', '')
    loop.quit()
bus.signal_subscribe('org.freedesktop.portal.Desktop', 'org.freedesktop.portal.Request', 'Response', path, None,
                     Gio.DBusSignalFlags.NONE, on_resp, None)
opts = {'interactive': GLib.Variant('b', interactive), 'handle_token': GLib.Variant('s', token)}
bus.call_sync('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop', 'org.freedesktop.portal.Screenshot',
              'Screenshot', GLib.Variant('(sa{sv})', ('', opts)), None, Gio.DBusCallFlags.NONE, -1, None)
GLib.timeout_add_seconds(90, loop.quit)
loop.run()
print(res.get('code', 'timeout'), res.get('uri', ''))
"""


def _portal_shot(interactive):
    """Screenshot through the desktop portal (the official way on GNOME / KDE Wayland).
    Uses the system Python's GLib bindings (python3-gi, part of every GNOME desktop)."""
    import urllib.parse
    py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else shutil.which("python3")
    if not py:
        return "unavailable"
    try:
        r = subprocess.run([py, "-c", _PORTAL_SCRIPT, "1" if interactive else "0"], capture_output=True, text=True, timeout=100)
    except subprocess.TimeoutExpired:
        raise CaptureError("The screenshot permission window was not answered in time.")
    if r.returncode != 0:
        return _portal_shot_gdbus(interactive)   # no python3-gi: use the gdbus tool instead
    parts = (r.stdout.strip().splitlines() or [""])[-1].split(" ", 1)
    if parts[0] == "timeout":
        raise CaptureError("The desktop did not answer the screenshot request. Try again and click Share / Allow if it asks.")
    if parts[0] != "0" or len(parts) < 2 or not parts[1]:
        return None                      # cancelled / denied
    path = urllib.parse.unquote(urllib.parse.urlparse(parts[1]).path)
    img = Image.open(path)
    img.load()
    try:
        os.remove(path)
    except OSError:
        pass
    return img.convert("RGB")


def _portal_shot_gdbus(interactive):
    import urllib.parse, select, time, re
    gd = shutil.which("gdbus")
    if not gd:
        return "unavailable"
    token = "digonto%d" % os.getpid()
    mon = subprocess.Popen([gd, "monitor", "--session", "--dest", "org.freedesktop.portal.Desktop"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        time.sleep(0.4)
        opts = "{'interactive': <%s>, 'handle_token': <'%s'>}" % ("true" if interactive else "false", token)
        try:
            c = subprocess.run([gd, "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
                                "--object-path", "/org/freedesktop/portal/desktop",
                                "--method", "org.freedesktop.portal.Screenshot.Screenshot", "", opts],
                               capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return "unavailable"
        if c.returncode != 0:
            return "unavailable"
        buf = ""
        end = time.time() + 90
        while time.time() < end:
            rl, _, _ = select.select([mon.stdout], [], [], 1.0)
            if rl:
                line = mon.stdout.readline()
                if not line:
                    break
                buf += line
                if "Response" in buf and "}" in buf.split("Response", 1)[1]:
                    break
        if "Response" not in buf:
            raise CaptureError("The desktop did not answer the screenshot request. Try again and click Share / Allow if it asks.")
        m = re.search(r"Response \((?:uint32 )?(\d+),", buf)
        if not m or m.group(1) != "0":
            return None
        u = re.search(r"'uri': <'([^']+)'>", buf)
        if not u:
            return None
        path = urllib.parse.unquote(urllib.parse.urlparse(u.group(1)).path)
        img = Image.open(path)
        img.load()
        try:
            os.remove(path)
        except OSError:
            pass
        return img.convert("RGB")
    finally:
        mon.kill()


def _wayland_tool(mode):
    """mode: full | area | window"""
    if mode in ("full", "area", "window"):
        # interactive = the desktop's own screenshot picker (choose Window / Selection there)
        r = _portal_shot(interactive=(mode != "full"))
        if r != "unavailable":
            return r
    out = _tmp_png()
    if shutil.which("gnome-screenshot"):
        args = {"full": ["gnome-screenshot", "-f", out], "area": ["gnome-screenshot", "-a", "-f", out],
                "window": ["gnome-screenshot", "-w", "-f", out]}[mode]
        return _run_tool(args, out, timeout=25)
    if shutil.which("spectacle"):
        args = {"full": ["spectacle", "-b", "-n", "-f", "-o", out], "area": ["spectacle", "-b", "-n", "-r", "-o", out],
                "window": ["spectacle", "-b", "-n", "-a", "-o", out]}[mode]
        return _run_tool(args, out)
    if shutil.which("grim"):
        if mode == "full":
            return _run_tool(["grim", out], out)
        if mode == "area" and shutil.which("slurp"):
            g = subprocess.run(["slurp"], capture_output=True, text=True).stdout.strip()
            return _run_tool(["grim", "-g", g, out], out) if g else None
    raise CaptureError("On Wayland a screenshot helper is needed. Install one:  sudo apt install gnome-screenshot")


def grab_full():
    k = kind()
    if k == "wayland":
        return _wayland_tool("full")
    if k == "mac":
        out = _tmp_png()
        return _run_tool(["screencapture", "-x", out], out)
    try:
        return ImageGrab.grab().convert("RGB")
    except Exception as e:
        raise CaptureError(str(e))


def native_area():
    """System area picker (macOS, Wayland). Returns image, or None if cancelled. None-capable platforms: see needs_overlay()."""
    k = kind()
    if k == "mac":
        out = _tmp_png()
        return _run_tool(["screencapture", "-i", "-s", "-x", out], out)
    if k == "wayland":
        return _wayland_tool("area")
    raise CaptureError("no native area picker")


def uses_native_area():
    return kind() in ("mac", "wayland")


# ---- windows ---------------------------------------------------------------

def list_windows():
    """Windows only: [(title, hwnd, (l, t, r, b))]."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    res = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        rect = wintypes.RECT()
        try:   # visible frame without the invisible resize border / shadow
            ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
        except Exception:
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < 40 or rect.bottom - rect.top < 40:
            return True
        if title in ("Program Manager", "Settings") and rect.left == 0 and rect.top == 0:
            pass
        res.append((title, int(hwnd), (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(proc_t(cb), 0)
    return res


def grab_window_windows(hwnd):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)           # restore if minimized
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    rect = wintypes.RECT()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    except Exception:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).convert("RGB")


def parse_xwininfo(text):
    """Geometry (l, t, r, b) from `xwininfo` output, or None."""
    def num(label):
        m = re.search(label + r":\s*(-?\d+)", text)
        return int(m.group(1)) if m else None
    x, y = num("Absolute upper-left X"), num("Absolute upper-left Y")
    w, h = num("Width"), num("Height")
    if None in (x, y, w, h):
        return None
    return x, y, x + w, y + h


def window_click_pick():
    """X11 / macOS / Wayland: the user clicks the window to capture. Returns image or None."""
    k = kind()
    if k == "mac":
        out = _tmp_png()
        return _run_tool(["screencapture", "-i", "-w", "-x", out], out)
    if k == "wayland":
        return _wayland_tool("window")
    if not shutil.which("xwininfo"):
        raise CaptureError("Window capture needs xwininfo:  sudo apt install x11-utils")
    r = subprocess.run(["xwininfo", "-frame"], capture_output=True, text=True, timeout=120)
    geo = parse_xwininfo(r.stdout)
    if not geo:
        return None
    full = ImageGrab.grab().convert("RGB")
    l, t, rr, b = geo
    l, t = max(0, l), max(0, t)
    rr, b = min(full.width, rr), min(full.height, b)
    if rr - l < 2 or b - t < 2:
        return None
    return full.crop((l, t, rr, b))
