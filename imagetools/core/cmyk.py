"""CMYK for print: ICC profile discovery, soft-proof preview, and CMYK export (TIFF / JPG / PDF / PSD).

Uses Pillow's littleCMS (ImageCms). Pictures with transparency are put on a background colour (white by default) because
CMYK files cannot carry transparency - keep PNG for a RIP that makes its own white underbase.
"""
import io
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageCms

INTENTS = {"Perceptual": 0, "Relative colorimetric (best for most prints)": 1, "Saturation": 2, "Absolute colorimetric (paper simulation)": 3}
SYSTEM_DIRS = ["/usr/share/color/icc", "/usr/local/share/color/icc", "/var/lib/colord/icc", os.path.expanduser("~/.color/icc"),
               os.path.expanduser("~/.local/share/color/icc"), "/Library/ColorSync/Profiles", "/System/Library/ColorSync/Profiles",
               os.path.expanduser("~/Library/ColorSync/Profiles"), "/usr/share/texlive/texmf-dist/tex/generic/colorprofiles",
               os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "System32", "spool", "drivers", "color")]
RIP_NAMES = ("def-cmyk.icc", "dtfpro.icc", "dtfpro-cmyk.icc")
_cache = {}


def _flag(name):
    F = getattr(ImageCms, "Flags", None)
    if F is not None and hasattr(F, name):
        return int(getattr(F, name))
    F = getattr(ImageCms, "FLAGS", {})
    return int(F.get(name.upper(), 0))


def _is_cmyk(path):
    try:
        p = ImageCms.getOpenProfile(path)
        return p.profile.xcolor_space.strip() == "CMYK"
    except Exception:
        return False


def profile_name(path):
    try:
        d = ImageCms.getProfileDescription(ImageCms.getOpenProfile(path)).strip()
        return d or os.path.basename(path)
    except Exception:
        return os.path.basename(path)


def find_profiles(extra=None, deep=False):
    """-> list of (label, path) of CMYK profiles. `deep` also searches the home folder for the RIP's profile (def-CMYK.icc)."""
    found, seen = [], set()

    def add(p):
        p = os.path.abspath(p)
        if p in seen or not os.path.isfile(p):
            return
        seen.add(p)
        if p.lower().endswith((".icc", ".icm")) and "gray_to_k" not in p.lower() and _is_cmyk(p):
            found.append((f"{profile_name(p)}   [{os.path.basename(p)}]", p))
    for p in (extra or []):
        if p:
            add(p)
    for d in SYSTEM_DIRS:
        if os.path.isdir(d):
            for root, dirs, files in os.walk(d):
                if root.count(os.sep) - d.count(os.sep) > 3:
                    dirs[:] = []
                for f in files:
                    if f.lower().endswith((".icc", ".icm")):
                        add(os.path.join(root, f))
    if deep:
        home = os.path.expanduser("~")
        for root, dirs, files in os.walk(home):
            dirs[:] = [x for x in dirs if not x.startswith(".cache") and x not in ("node_modules", ".git", "proc")]
            if root.count(os.sep) - home.count(os.sep) > 7:
                dirs[:] = []
            for f in files:
                if f.lower() in RIP_NAMES or f.lower().startswith("dtfpro") or ("cmyk" in f.lower() and f.lower().endswith((".icc", ".icm"))):
                    add(os.path.join(root, f))
    return found


def default_profile(settings=None):
    p = settings.get("cmyk_profile") if settings else None
    if p and os.path.isfile(p):
        return p
    for lab, path in find_profiles():
        if "fogra39" in path.lower() or "fogra39" in lab.lower():
            return path
    for lab, path in find_profiles():
        if "default_cmyk" in path.lower():
            return path
    f = find_profiles()
    return f[0][1] if f else None


def _srgb():
    if "srgb" not in _cache:
        _cache["srgb"] = ImageCms.createProfile("sRGB")
    return _cache["srgb"]


def srgb_icc_bytes():
    return ImageCms.ImageCmsProfile(_srgb()).tobytes()


class Proof:
    """Soft-proof: shows how an RGB picture will look when printed with the CMYK profile."""

    def __init__(self, path, intent=1, bpc=True):
        self.path, self.intent, self.bpc = path, intent, bpc
        prof = ImageCms.getOpenProfile(path)
        flags = _flag("SOFTPROOFING") | (_flag("BLACKPOINTCOMPENSATION") if bpc else 0)
        self.tr = ImageCms.buildProofTransform(_srgb(), _srgb(), prof, "RGB", "RGB", renderingIntent=min(intent, 1),
                                               proofRenderingIntent=intent, flags=flags)
        self.token = (path, intent, bpc)

    def apply(self, img):
        """RGB or RGBA image -> proofed copy (alpha kept)"""
        a = img.getchannel("A") if img.mode == "RGBA" else None
        out = ImageCms.applyTransform(img.convert("RGB"), self.tr)
        if a is not None:
            out = out.convert("RGBA")
            out.putalpha(a)
        return out


def to_cmyk(img, profile_path, intent=1, bpc=True, bg=(255, 255, 255)):
    """RGB(A) -> CMYK PIL image through the profile (transparency is placed on `bg`)."""
    img = img.convert("RGBA")
    base = Image.new("RGB", img.size, tuple(bg))
    base.paste(img, (0, 0), img)
    prof = ImageCms.getOpenProfile(profile_path)
    flags = _flag("BLACKPOINTCOMPENSATION") if bpc else 0
    tr = ImageCms.buildTransform(_srgb(), prof, "RGB", "CMYK", renderingIntent=intent, flags=flags)
    return ImageCms.applyTransform(base, tr)


def gamut_loss(img, proof):
    """rough share (0..1) of pixels that change noticeably when printed (sampled)"""
    im = img.convert("RGB")
    if im.width * im.height > 400000:
        k = (400000 / (im.width * im.height)) ** 0.5
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))))
    a = np.asarray(im).astype(np.int16)
    b = np.asarray(proof.apply(im)).astype(np.int16)
    d = np.abs(a - b).max(axis=2)
    return float((d > 40).mean())


# ----------------------------------------------------------------------------- writers
def _psd_cmyk(img, icc, dpi):
    w, h = img.size
    arr = 255 - np.asarray(img.convert("CMYK"), dtype=np.uint8)           # Photoshop stores CMYK inverted
    out = bytearray(b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6 + struct.pack(">HIIHH", 4, h, w, 8, 4))
    out += struct.pack(">I", 0)
    res = bytearray()
    pdi = int(dpi * 65536)
    data = struct.pack(">IHHIHH", pdi, 1, 1, pdi, 1, 1)
    res += b"8BIM" + struct.pack(">H", 1005) + b"\x00\x00" + struct.pack(">I", len(data)) + data
    if icc:
        pad = b"\x00" if len(icc) % 2 else b""
        res += b"8BIM" + struct.pack(">H", 1039) + b"\x00\x00" + struct.pack(">I", len(icc)) + icc + pad
    out += struct.pack(">I", len(res)) + res
    out += struct.pack(">I", 0)                                           # layer & mask info (flat file)
    out += struct.pack(">H", 0)                                           # raw image data
    for c in range(4):
        out += arr[:, :, c].tobytes()
    return bytes(out)


def save_cmyk(img, path, profile_path, dpi=300, intent=1, bpc=True, bg=(255, 255, 255), mirror=False, quality=95):
    """img: RGB(A). Writes .tif/.tiff, .jpg, .pdf or .psd as CMYK with the profile embedded (where the format allows)."""
    if mirror:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    c = to_cmyk(img, profile_path, intent, bpc, bg)
    with open(profile_path, "rb") as f:
        icc = f.read()
    ext = os.path.splitext(path)[1].lower()
    dpi = float(dpi)
    if ext in (".tif", ".tiff"):
        c.save(path, "TIFF", compression="tiff_lzw", dpi=(dpi, dpi), icc_profile=icc)
    elif ext in (".jpg", ".jpeg"):
        c.save(path, "JPEG", quality=int(quality), subsampling=0, dpi=(dpi, dpi), icc_profile=icc)
    elif ext == ".pdf":
        c.save(path, "PDF", resolution=dpi)
    elif ext == ".psd":
        with open(path, "wb") as f:
            f.write(_psd_cmyk(c, icc, dpi))
    else:
        raise ValueError("Unknown CMYK format " + ext)
    return c.size


def ink_totals(img_cmyk):
    """-> (max total ink %, share of pixels above 300 %) - handy check for DTF / film limits"""
    a = np.asarray(img_cmyk, dtype=np.float32) / 255.0 * 100.0
    t = a.sum(axis=2)
    return float(t.max()), float((t > 300).mean())
