"""The offline AI helper of Digonto Studio (background removal).

Digonto Studio carries the helper for each system in its install folder (ai/<system>-<chip>/digonto-ai) and the
AI models in ai/models.  "Install AI background removal" on the Setup page checks the helper and switches it on -
it takes everything from that folder, nothing is downloaded.  The helper is a separate program, so the
Apple-chip version runs natively even when Studio itself runs through Rosetta.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading

NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}


def _user_dir():
    base = os.environ.get("DIGONTO_BASE")
    if not base:
        if sys.platform == "win32":
            base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Digonto")
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support/Digonto")
        else:
            base = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "digonto")
    os.makedirs(base, exist_ok=True)
    return base


FLAG = "ai-enabled.json"


def apple_chip():
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "1"
    except Exception:                                          # noqa: BLE001
        return False


def ai_folder():
    """the 'ai' folder of the Studio install, or None (single-app downloads and developer runs)"""
    cands = [os.environ.get("DIGONTO_AI_DIR", "")]
    meipass = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(os.path.realpath(sys.executable))
    if meipass:
        cands += [os.path.join(meipass, "ai"), os.path.join(meipass, "..", "Resources", "ai")]
    cands += [os.path.join(exe_dir, "_internal", "ai"), os.path.join(exe_dir, "..", "Resources", "ai")]
    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))       # .../apps/digonto-tools/imagetools/core
    cands.append(os.path.join(here, "..", "..", "..", "..", "ai"))
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "models")):
            return os.path.realpath(c)
    return None


def helper_path():
    ai = ai_folder()
    if not ai:
        return None
    if sys.platform == "darwin":
        order = ["macos-arm64", "macos-x86_64"] if apple_chip() else ["macos-x86_64"]
    elif sys.platform == "win32":
        order = ["windows-x86_64"]
    else:
        order = ["linux-x86_64"]
    for d in order:
        exe = os.path.join(ai, d, "digonto-ai", "digonto-ai" + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(exe):
            return exe
    return None


def models():
    ai = ai_folder()
    if not ai:
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(os.path.join(ai, "models")) if f.endswith(".onnx"))


def enabled():
    try:
        with open(os.path.join(_user_dir(), FLAG), encoding="utf-8") as f:
            info = json.load(f)
        return info.get("on") and helper_path() is not None
    except (OSError, ValueError):
        return False


def available():
    """True when the helper exists here (even if it is not switched on yet)"""
    return helper_path() is not None


def enable(log=print):
    """check the helper and switch it on. Returns True on success."""
    exe = helper_path()
    if not exe:
        log("The AI files are not in this installation. Reinstall Digonto Studio with Digonto Tools selected.")
        return False
    log("Checking the AI helper: " + exe)
    try:
        r = subprocess.run([exe, "check"], capture_output=True, text=True, timeout=180, **NO_WINDOW)
    except Exception as e:                                       # noqa: BLE001
        log(f"The AI helper did not start: {e}")
        return False
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out.startswith("ok"):
        log("The AI helper did not answer correctly:\n" + out[-800:] + (r.stderr or "")[-800:])
        return False
    log(f"AI engine {out.split()[1] if len(out.split()) > 1 else ''} ready. Models: {', '.join(models())}")
    with open(os.path.join(_user_dir(), FLAG), "w", encoding="utf-8") as f:
        json.dump({"on": True, "helper": exe}, f)
    return True


def disable():
    try:
        os.remove(os.path.join(_user_dir(), FLAG))
    except OSError:
        pass
    _server.close()


class _Server:
    """keeps the helper running between pictures, so each model is loaded only once"""

    def __init__(self):
        self.proc = None
        self.serve = True                 # older helpers have no 'serve' mode: then one call per picture
        self.lock = threading.Lock()

    def _start(self, exe):
        self.proc = subprocess.Popen([exe, "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1, **NO_WINDOW)
        first = self.proc.stdout.readline().strip()
        if first != "ready":
            self.close()
            self.serve = False

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=3)
            except Exception:                                    # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:                                # noqa: BLE001
                    pass
            self.proc = None

    def mask(self, png_bytes, model, soft):
        exe = helper_path()
        if not exe:
            raise RuntimeError("The AI helper is missing - reinstall Digonto Studio.")
        if model not in models():
            raise RuntimeError(f"The AI model '{model}' is not in this installation.")
        with self.lock, tempfile.TemporaryDirectory(prefix="digonto-ai-") as tmp:
            src, dst = os.path.join(tmp, "in.png"), os.path.join(tmp, "mask.png")
            with open(src, "wb") as f:
                f.write(png_bytes)
            if self.serve:
                if self.proc is None or self.proc.poll() is not None:
                    self._start(exe)
            if self.serve and self.proc is not None:
                self.proc.stdin.write(json.dumps({"in": src, "out": dst, "model": model, "soft": soft}) + "\n")
                self.proc.stdin.flush()
                ans = self.proc.stdout.readline().strip()
                if ans != "ok":
                    if not ans:
                        self.close()
                    raise RuntimeError(ans[6:] if ans.startswith("error") else "The AI helper stopped unexpectedly.")
            else:
                cmd = [exe, "mask", src, dst, "--model", model] + (["--soft"] if soft else [])
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **NO_WINDOW)
                if r.returncode != 0:
                    raise RuntimeError(((r.stderr or "") + (r.stdout or "")).strip()[-400:] or "AI helper failed")
            with open(dst, "rb") as f:
                return f.read()


_server = _Server()


def mask(png_bytes, model="u2net", soft=False):
    return _server.mask(png_bytes, model, soft)
