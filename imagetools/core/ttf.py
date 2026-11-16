"""Minimal TrueType / OpenType reader (own code, no libraries).

Used for: listing fonts (names, styles), checking which characters a font has,
and turning text into vector outlines (TrueType 'glyf' fonts).
OpenType fonts with CFF outlines are listed and can be drawn as pictures, but
cannot be converted to outlines (has_outlines == False).
"""
import struct

_U16 = struct.Struct(">H")
_I16 = struct.Struct(">h")
_U32 = struct.Struct(">I")


def _u16(b, o):
    return _U16.unpack_from(b, o)[0]


def _i16(b, o):
    return _I16.unpack_from(b, o)[0]


def _u32(b, o):
    return _U32.unpack_from(b, o)[0]


def font_count(data):
    if data[:4] == b"ttcf":
        return _u32(data, 8)
    return 1


class Font:
    """One font face. `data` is the whole file as bytes; `index` selects a face in a .ttc."""

    def __init__(self, data, index=0):
        self.data = data
        self.tables = {}
        base = 0
        if data[:4] == b"ttcf":
            n = _u32(data, 8)
            if index >= n:
                raise ValueError("no such face in collection")
            base = _u32(data, 12 + 4 * index)
        tag = data[base:base + 4]
        if tag not in (b"\x00\x01\x00\x00", b"true", b"OTTO", b"typ1"):
            raise ValueError("not a TrueType/OpenType font")
        self.is_cff = tag == b"OTTO"
        num = _u16(data, base + 4)
        for i in range(num):
            o = base + 12 + 16 * i
            t = data[o:o + 4].decode("latin-1")
            self.tables[t] = (_u32(data, o + 8), _u32(data, o + 12))
        self._cmap = None
        self._loca = None
        self._hm = None
        self._cache = {}
        head = self.tables.get("head")
        self.units_per_em = _u16(data, head[0] + 18) if head else 1000
        self._loca_long = _i16(data, head[0] + 50) if head else 0
        self.mac_style = _u16(data, head[0] + 44) if head else 0
        self.has_outlines = "glyf" in self.tables and "loca" in self.tables
        hhea = self.tables.get("hhea")
        self.ascent = _i16(data, hhea[0] + 4) if hhea else int(self.units_per_em * 0.8)
        self.descent = _i16(data, hhea[0] + 6) if hhea else -int(self.units_per_em * 0.2)
        self.line_gap = _i16(data, hhea[0] + 8) if hhea else 0
        self.num_hmetrics = _u16(data, hhea[0] + 34) if hhea else 0
        maxp = self.tables.get("maxp")
        self.num_glyphs = _u16(data, maxp[0] + 4) if maxp else 0
        os2 = self.tables.get("OS/2")
        self.weight = _u16(data, os2[0] + 4) if os2 and os2[1] >= 6 else (700 if self.mac_style & 1 else 400)

    # ---- names ---------------------------------------------------------
    def names(self):
        t = self.tables.get("name")
        res = {}
        if not t:
            return res
        d, off = self.data, t[0]
        count, soff = _u16(d, off + 2), _u16(d, off + 4)
        for i in range(count):
            r = off + 6 + 12 * i
            pid, eid, lid, nid, ln, so = (_u16(d, r + k) for k in (0, 2, 4, 6, 8, 10))
            raw = d[off + soff + so: off + soff + so + ln]
            try:
                s = raw.decode("utf-16-be") if pid in (0, 3) else raw.decode("mac_roman")
            except Exception:
                continue
            # prefer English Windows names, then anything
            pri = 2 if (pid == 3 and lid == 0x409) else (1 if pid in (0, 1) else 0)
            if nid not in res or pri > res[nid][0]:
                res[nid] = (pri, s)
        return {k: v[1] for k, v in res.items()}

    def family_style(self):
        n = self.names()
        fam = n.get(16) or n.get(1) or "Unknown"
        sty = n.get(17) or n.get(2) or "Regular"
        return fam.strip(), sty.strip()

    @property
    def italic(self):
        return bool(self.mac_style & 2)

    @property
    def bold(self):
        return bool(self.mac_style & 1)

    # ---- characters ----------------------------------------------------
    def cmap(self):
        if self._cmap is not None:
            return self._cmap
        res = {}
        t = self.tables.get("cmap")
        if t:
            d, off = self.data, t[0]
            n = _u16(d, off + 2)
            subs = []
            for i in range(n):
                pid, eid, so = _u16(d, off + 4 + 8 * i), _u16(d, off + 6 + 8 * i), _u32(d, off + 8 + 8 * i)
                fmt = _u16(d, off + so)
                rank = {(3, 10): 0, (0, 4): 0, (0, 6): 0, (3, 1): 1, (0, 3): 1}.get((pid, eid), 5 if pid == 0 else 9)
                if fmt in (4, 12):
                    subs.append((rank, off + so, fmt))
            subs.sort()
            for _rank, so, fmt in subs[:2]:
                if fmt == 12:
                    ng = _u32(d, so + 12)
                    for g in range(ng):
                        s, e, gid = (_u32(d, so + 16 + 12 * g + k) for k in (0, 4, 8))
                        for c in range(s, min(e, s + 0x20000) + 1):
                            res.setdefault(c, gid + c - s)
                else:
                    segx2 = _u16(d, so + 6)
                    sg = segx2 // 2
                    endo, starto = so + 14, so + 16 + segx2
                    deltao, rangeo = starto + segx2, starto + 2 * segx2
                    for i in range(sg):
                        e, s = _u16(d, endo + 2 * i), _u16(d, starto + 2 * i)
                        dl, ro = _i16(d, deltao + 2 * i), _u16(d, rangeo + 2 * i)
                        if s == 0xFFFF:
                            continue
                        for c in range(s, e + 1):
                            if ro == 0:
                                g = (c + dl) & 0xFFFF
                            else:
                                p = rangeo + 2 * i + ro + 2 * (c - s)
                                if p + 2 > len(d):
                                    continue
                                g = _u16(d, p)
                                if g:
                                    g = (g + dl) & 0xFFFF
                            if g:
                                res.setdefault(c, g)
        self._cmap = res
        return res

    def cmap_mac(self):
        """Mac Roman (platform 1) cmap, formats 0 and 6: byte code -> glyph (used by PDF fonts)."""
        res = {}
        t = self.tables.get("cmap")
        if not t:
            return res
        d, off = self.data, t[0]
        for i in range(_u16(d, off + 2)):
            pid, so = _u16(d, off + 4 + 8 * i), _u32(d, off + 8 + 8 * i)
            if pid != 1:
                continue
            fmt = _u16(d, off + so)
            if fmt == 0:
                for c in range(256):
                    g = d[off + so + 6 + c]
                    if g:
                        res[c] = g
            elif fmt == 6:
                first, cnt = _u16(d, off + so + 6), _u16(d, off + so + 8)
                for c in range(cnt):
                    g = _u16(d, off + so + 10 + 2 * c)
                    if g:
                        res[first + c] = g
        return res

    def has_char(self, ch):
        return ord(ch) in self.cmap()

    def covers(self, text):
        cm = self.cmap()
        return all((ord(c) in cm) or c.isspace() or c in "\r\n\t" for c in text)

    # ---- metrics -------------------------------------------------------
    def advance(self, gid):
        t = self.tables.get("hmtx")
        if not t or not self.num_hmetrics:
            return self.units_per_em // 2
        i = min(gid, self.num_hmetrics - 1)
        return _u16(self.data, t[0] + 4 * i)

    # ---- outlines ------------------------------------------------------
    def _loca_offsets(self, gid):
        t = self.tables["loca"]
        d = self.data
        if self._loca_long:
            a, b = _u32(d, t[0] + 4 * gid), _u32(d, t[0] + 4 * gid + 4)
        else:
            a, b = 2 * _u16(d, t[0] + 2 * gid), 2 * _u16(d, t[0] + 2 * gid + 2)
        return a, b

    def glyph_contours(self, gid, depth=0):
        """List of contours; each contour is a list of (x, y, on_curve) in font units (y up)."""
        if gid in self._cache:
            return self._cache[gid]
        if depth > 6 or gid >= self.num_glyphs or not self.has_outlines:
            return []
        d = self.data
        a, b = self._loca_offsets(gid)
        if b <= a:
            self._cache[gid] = []
            return []
        g = self.tables["glyf"][0] + a
        nc = _i16(d, g)
        contours = []
        if nc >= 0:
            p = g + 10
            ends = [_u16(d, p + 2 * i) for i in range(nc)]
            p += 2 * nc
            ilen = _u16(d, p)
            p += 2 + ilen
            npts = (ends[-1] + 1) if ends else 0
            flags = []
            while len(flags) < npts:
                f = d[p]
                p += 1
                flags.append(f)
                if f & 8:
                    r = d[p]
                    p += 1
                    flags.extend([f] * r)
            flags = flags[:npts]
            xs, x = [], 0
            for f in flags:
                if f & 2:
                    dx = d[p]
                    p += 1
                    x += dx if f & 16 else -dx
                elif not f & 16:
                    x += _i16(d, p)
                    p += 2
                xs.append(x)
            ys, y = [], 0
            for f in flags:
                if f & 4:
                    dy = d[p]
                    p += 1
                    y += dy if f & 32 else -dy
                elif not f & 32:
                    y += _i16(d, p)
                    p += 2
                ys.append(y)
            s = 0
            for e in ends:
                contours.append([(xs[i], ys[i], bool(flags[i] & 1)) for i in range(s, e + 1)])
                s = e + 1
        else:
            p = g + 10
            while True:
                fl, gi = _u16(d, p), _u16(d, p + 2)
                p += 4
                if fl & 1:
                    a1, a2 = _i16(d, p), _i16(d, p + 2)
                    p += 4
                else:
                    a1, a2 = struct.unpack_from(">bb", d, p)
                    p += 2
                dx, dy = (a1, a2) if fl & 2 else (0, 0)
                m = [1.0, 0.0, 0.0, 1.0]
                if fl & 8:
                    m[0] = m[3] = _i16(d, p) / 16384.0
                    p += 2
                elif fl & 0x40:
                    m[0], m[3] = _i16(d, p) / 16384.0, _i16(d, p + 2) / 16384.0
                    p += 4
                elif fl & 0x80:
                    m = [_i16(d, p + 2 * k) / 16384.0 for k in range(4)]
                    p += 8
                for c in self.glyph_contours(gi, depth + 1):
                    contours.append([(m[0] * x + m[2] * y + dx, m[1] * x + m[3] * y + dy, on) for x, y, on in c])
                if not fl & 0x20:
                    break
        self._cache[gid] = contours
        return contours


def contour_to_cubics(pts):
    """TrueType quadratic contour -> ("M",x,y) ("C",x1,y1,x2,y2,x,y) ... ("Z",)."""
    n = len(pts)
    if n == 0:
        return []
    pts = list(pts)
    # start on an on-curve point
    k = next((i for i, p in enumerate(pts) if p[2]), None)
    if k is None:
        a, b = pts[0], pts[1] if n > 1 else pts[0]
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, True)
        pts = [mid] + pts[1:] + [pts[0]]
    else:
        pts = pts[k:] + pts[:k]
    start = pts[0]
    cmds = [("M", float(start[0]), float(start[1]))]
    cur = (float(start[0]), float(start[1]))
    i = 1
    pts.append(start)
    while i < len(pts):
        p = pts[i]
        if p[2]:
            cmds.append(("L", float(p[0]), float(p[1])))
            cur = (float(p[0]), float(p[1]))
            i += 1
        else:
            nxt = pts[i + 1] if i + 1 < len(pts) else start
            if nxt[2]:
                end = (float(nxt[0]), float(nxt[1]))
                i += 2
            else:
                end = ((p[0] + nxt[0]) / 2.0, (p[1] + nxt[1]) / 2.0)
                i += 1
            c = (float(p[0]), float(p[1]))
            cmds.append(("C", cur[0] + 2 / 3 * (c[0] - cur[0]), cur[1] + 2 / 3 * (c[1] - cur[1]),
                         end[0] + 2 / 3 * (c[0] - end[0]), end[1] + 2 / 3 * (c[1] - end[1]), end[0], end[1]))
            cur = end
    cmds.append(("Z",))
    return cmds


def text_outline(font, text, size, x=0.0, y=0.0, tracking=0.0, line_height=None, align="left"):
    """Outline commands for `text` (may contain newlines).

    (x, y) is the top-left of the text block; y grows downwards (like screens).
    Returns (cmds, width, height). Latin/Greek/Cyrillic and similar simple scripts only -
    no shaping (so Bengali/Arabic conjuncts are NOT formed here).
    """
    s = size / float(font.units_per_em)
    asc, desc = font.ascent * s, -font.descent * s
    lh = line_height if line_height else (asc + desc + font.line_gap * s)
    cm = font.cmap()
    lines = text.replace("\r", "").split("\n")
    widths = []
    for ln in lines:
        w = 0.0
        for ch in ln:
            w += font.advance(cm.get(ord(ch), 0)) * s + tracking
        widths.append(w)
    total_w = max(widths) if widths else 0.0
    cmds = []
    for li, ln in enumerate(lines):
        ox = x
        if align == "center":
            ox += (total_w - widths[li]) / 2.0
        elif align == "right":
            ox += total_w - widths[li]
        base = y + asc + li * lh
        pen = ox
        for ch in ln:
            gid = cm.get(ord(ch), 0)
            for c in font.glyph_contours(gid):
                for cmd in contour_to_cubics(c):
                    if cmd[0] == "Z":
                        cmds.append(cmd)
                    else:
                        vals = list(cmd[1:])
                        out = []
                        for j in range(0, len(vals), 2):
                            out.append(pen + vals[j] * s)
                            out.append(base - vals[j + 1] * s)
                        cmds.append((cmd[0],) + tuple(out))
            pen += font.advance(gid) * s + tracking
    return cmds, total_w, lh * len(lines) - (lh - asc - desc)
