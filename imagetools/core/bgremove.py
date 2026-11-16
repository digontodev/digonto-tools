"""Background removal. All functions work on BGR / BGRA numpy arrays."""
import cv2
import numpy as np


def flood_fill_transparent(img_bgr, tolerance=30):
    h, w = img_bgr.shape[:2]
    alpha = np.full((h, w), 255, np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    combined = np.zeros((h + 2, w + 2), np.uint8)
    for cx, cy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        m = np.zeros((h + 2, w + 2), np.uint8)
        cv2.floodFill(img_bgr.copy(), m, (cx, cy), 0, loDiff=(tolerance,) * 3,
                      upDiff=(tolerance,) * 3, flags=flags)
        combined = cv2.bitwise_or(combined, m)
    alpha[combined[1:-1, 1:-1] > 0] = 0
    b, g, r = cv2.split(img_bgr)
    return cv2.merge([b, g, r, alpha])


def click_remove(img_bgra, points, tolerance=30):
    """Manual mode: floods from each clicked (x, y); matches bg_remove.py."""
    out = img_bgra.copy()
    bgr = np.ascontiguousarray(out[:, :, :3])
    h, w = bgr.shape[:2]
    for (x, y) in points:
        if not (0 <= x < w and 0 <= y < h):
            continue
        m = np.zeros((h + 2, w + 2), np.uint8)
        cv2.floodFill(bgr.copy(), m, (int(x), int(y)), 0, loDiff=(tolerance,) * 3,
                      upDiff=(tolerance,) * 3,
                      flags=4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8))
        out[:, :, 3][m[1:-1, 1:-1] > 0] = 0
    return out


def ai_available():
    try:
        import rembg  # noqa: F401
        return True
    except Exception:
        return False


AI_MODELS = [
    ("u2net", "u2net - same as the first version"),
    ("isnet-general-use", "isnet - more accurate edges (recommended)"),
    ("birefnet-general-lite", "birefnet lite - most accurate, slower"),
    ("u2netp", "u2netp - fast and small"),
]
_sessions = {}


def _session(name):
    from rembg import new_session
    if name not in _sessions:
        _sessions[name] = new_session(name)
    return _sessions[name]


def ai_remove_bg(img_bgr, edge_threshold=200, erode=2, model="u2net", soft=False, keep_largest=False):
    """Only the alpha comes from the AI; RGB stays byte-identical to the input.

    Small pictures (icons) are enlarged before the AI sees them - the AI works on a ~320 px grid, so a 60 px icon
    would otherwise get a blurry mask - and the mask is shrunk back afterwards.
    soft=False: hard edge (best for templates).  soft=True: smooth edge (best for photos)."""
    from rembg import remove
    h, w = img_bgr.shape[:2]
    big = max(h, w)
    k = 1.0 if big >= 640 else min(8.0, 640.0 / big)
    work = img_bgr if k == 1.0 else cv2.resize(img_bgr, (round(w * k), round(h * k)), interpolation=cv2.INTER_CUBIC)
    ok, enc = cv2.imencode(".png", work)
    mask_bytes = remove(enc.tobytes(), only_mask=True, post_process_mask=not soft, session=_session(model))
    mask = cv2.imdecode(np.frombuffer(mask_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_AREA)
    if soft:
        lo = max(0, edge_threshold - 120)
        hi = min(255, edge_threshold + 40)
        m = np.clip((mask.astype(np.float32) - lo) * 255.0 / max(1, hi - lo), 0, 255).astype(np.uint8)
        if erode > 0:
            m = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=erode)
            m = cv2.GaussianBlur(m, (0, 0), 0.8)
    else:
        _, m = cv2.threshold(mask, edge_threshold, 255, cv2.THRESH_BINARY)
        if erode > 0:
            m = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=erode)
    if keep_largest:
        n, lab, stats, _c = cv2.connectedComponentsWithStats((m > 127).astype(np.uint8), connectivity=8)
        if n > 2:
            top = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            m[(lab != top) & (lab > 0)] = 0
    b, g, r = cv2.split(img_bgr)
    return cv2.merge([b, g, r, m])


def grow_mask(bgra, grow):
    if grow <= 0:
        return bgra
    b, g, r, a = cv2.split(bgra)
    a = cv2.dilate(a, np.ones((3, 3), np.uint8), iterations=grow)
    return cv2.merge([b, g, r, a])


def quality_report(bgra):
    """Returns (kept, total, std, warning_text_or_None)."""
    a = bgra[:, :, 3]
    keep = a > 10
    n = int(keep.sum())
    if n == 0:
        return 0, a.size, 0.0, "Nothing is left opaque - the whole image was removed as background."
    lum = cv2.cvtColor(bgra[:, :, :3], cv2.COLOR_BGR2GRAY)[keep].astype(float)
    std = float(lum.std())
    warn = None
    if std < 50:
        warn = ("The kept pixels are almost one flat colour, so this template will likely score LOW "
                "when matching. Fix: set 'Grow edge' to 1 (and with AI also set 'Erode' to 0).")
    return n, a.size, std, warn


def checkerboard(w, h, size=8):
    ys, xs = np.mgrid[0:h, 0:w]
    c = (((xs // size) + (ys // size)) % 2 * 40 + 200).astype(np.uint8)
    return np.dstack([c, c, c])


def composite_preview(bgra):
    """BGRA -> RGB uint8 over a checkerboard (for display)."""
    h, w = bgra.shape[:2]
    a = bgra[:, :, 3:4].astype(np.float32) / 255.0
    bg = checkerboard(w, h).astype(np.float32)
    rgb = bgra[:, :, 2::-1].astype(np.float32)
    return (rgb * a + bg * (1 - a)).astype(np.uint8)
