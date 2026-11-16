"""Finds the fonts installed on this computer and picks faces by family/style."""
import json
import mmap
import os
import sys
import threading

from . import ttf

_FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")
_lock = threading.Lock()
_db = None            # family -> {style: (path, index)}
_files = {}           # path -> list of (family, style, index, weight, italic)
_ps = {}              # lower-case PostScript / full names -> (family, style)
_font_cache = {}      # (path, index) -> ttf.Font


def font_dirs():
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        d = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        d = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
             os.path.join(home, "Library", "Fonts")]
    else:
        d = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.join(home, ".fonts"),
             os.path.join(home, ".local", "share", "fonts")]
    return [x for x in d if x and os.path.isdir(x)]


def _cache_path():
    try:
        from ..ui.app import config_dir
        return os.path.join(config_dir(), "fonts-cache.json")
    except Exception:
        return None


def _scan_file(path):
    out = []
    try:
        with open(path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)   # reads only the parts we touch
            try:
                for i in range(ttf.font_count(mm)):
                    ft = ttf.Font(mm, i)
                    fam, sty = ft.family_style()
                    nm = ft.names()
                    out.append([fam, sty, i, ft.weight, ft.italic, nm.get(6, ""), nm.get(4, "")])
            finally:
                mm.close()
    except Exception:
        pass
    return out


def scan(force=False):
    """Builds the font list (cached on disk by file size+mtime so the next start is fast)."""
    global _db, _files
    with _lock:
        if _db is not None and not force:
            return _db
        cache, cp = {}, _cache_path()
        if cp and os.path.exists(cp) and not force:
            try:
                with open(cp, encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        new_cache, files = {}, {}
        for root in font_dirs():
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    if not n.lower().endswith(_FONT_EXT):
                        continue
                    p = os.path.join(dirpath, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    key = f"v2:{st.st_size}:{int(st.st_mtime)}"
                    ent = cache.get(p)
                    if ent and ent[0] == key:
                        faces = ent[1]
                    else:
                        faces = _scan_file(p)
                    new_cache[p] = [key, faces]
                    if faces:
                        files[p] = faces
        if cp:
            try:
                with open(cp, "w", encoding="utf-8") as f:
                    json.dump(new_cache, f)
            except Exception:
                pass
        db = {}
        for p, faces in files.items():
            for fam, sty, idx, _w, _it, psn, full in faces:
                db.setdefault(fam, {}).setdefault(sty, (p, idx))
                if psn:
                    _ps[psn.lower().replace(" ", "")] = (fam, sty)
                if full:
                    _ps[full.lower().replace(" ", "")] = (fam, sty)
        _files, _db = files, db
        return db


def families():
    return sorted(scan().keys(), key=lambda s: s.lower())


_ORDER = {"regular": 0, "book": 0, "roman": 0, "normal": 0, "italic": 1, "oblique": 1, "bold": 2, "bold italic": 3,
          "bold oblique": 3}


def styles(family):
    st = list(scan().get(family, {}).keys())
    st.sort(key=lambda s: (_ORDER.get(s.lower(), 9), s.lower()))
    return st


def resolve(family, style="Regular"):
    """(path, index) of the face, or None. Falls back to the closest style, then the family's first face."""
    db = scan()
    fam = db.get(family)
    if not fam:
        return None
    if style in fam:
        return fam[style]
    for s in fam:
        if s.lower() == (style or "").lower():
            return fam[s]
    for pref in ("Regular", "Book", "Roman", "Normal"):
        if pref in fam:
            return fam[pref]
    return next(iter(fam.values()))


def default_family():
    fams = scan()
    prefer = ("Segoe UI", "Arial", "Helvetica", "Helvetica Neue", "DejaVu Sans", "Liberation Sans", "Noto Sans",
              "Ubuntu", "Open Sans", "FreeSans")
    for p in prefer:
        if p in fams:
            return p
    f = families()
    return f[0] if f else ""


def load(path, index=0):
    key = (path, index)
    ft = _font_cache.get(key)
    if ft is None:
        with open(path, "rb") as f:
            ft = ttf.Font(f.read(), index)
        _font_cache[key] = ft
    return ft


def face_for_text(family, style, text):
    """(path, index) of the requested face, or a fallback face that has every character of `text`
    (e.g. a Bengali font when you type Bengali with a Latin-only font)."""
    r = resolve(family, style)
    if r:
        try:
            if load(*r).covers(text):
                return r
        except Exception:
            return r
    need = [c for c in text if not c.isspace()]
    if not need:
        return r
    best = None
    for fam, sts in scan().items():
        p, i = sts.get("Regular") or next(iter(sts.values()))
        try:
            ft = load(p, i)
        except Exception:
            continue
        if ft.covers(text):
            return (p, i)
        cov = sum(1 for c in need if ft.has_char(c))
        if best is None or cov > best[0]:
            best = (cov, (p, i))
    return best[1] if best and best[0] > 0 else r


def match_postscript(psname):
    """Maps a Photoshop/PDF font name (e.g. 'Montserrat-Bold', 'ArialMT') to an installed (family, style).
    Returns (family, style, exact). exact is False when only a similar font was found; family may be None."""
    scan()
    key = (psname or "").lower().replace(" ", "")
    if key in _ps:
        return _ps[key][0], _ps[key][1], True
    base = key
    for suffix in ("mt", "ps"):
        if base.endswith(suffix) and base[:-len(suffix)] in _ps:
            f, st = _ps[base[:-len(suffix)]]
            return f, st, True
    fam_part, _, sty_part = (psname or "").partition("-")
    fams = {f.lower().replace(" ", ""): f for f in scan()}
    fam_key = fam_part.lower().replace(" ", "")
    for suf in ("mt", "ps"):
        if fam_key.endswith(suf) and fam_key[:-len(suf)] in fams:
            fam_key = fam_key[:-len(suf)]
    if fam_key in fams:
        fam = fams[fam_key]
        want = sty_part.lower()
        bold, ital = "bold" in want, ("italic" in want or "oblique" in want)
        best = None
        for st in scan()[fam]:
            sl = st.lower()
            score = (("bold" in sl) == bold) * 2 + (("italic" in sl or "oblique" in sl) == ital)
            if want and want.replace(" ", "") == sl.replace(" ", ""):
                score += 5
            if best is None or score > best[0]:
                best = (score, st)
        return fam, best[1], False
    return None, None, False
