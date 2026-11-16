"""Template matching (same algorithm as verify_template.py / the bot's vision.py)."""
import cv2
import numpy as np


def prepare_template(raw):
    has_alpha = raw.ndim == 3 and raw.shape[2] == 4
    if not has_alpha:
        template = raw if raw.ndim == 3 else cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
        return {"has_alpha": False, "template": template}
    template = raw[:, :, :3]
    mask_bool = raw[:, :, 3] > 10
    if mask_bool.sum() == 0:
        return {"has_alpha": True, "empty_mask": True}
    masked = template.astype(np.float64)[mask_bool]
    centered = masked - masked.mean()
    flat = template.copy()
    flat[~mask_bool] = masked.mean(axis=0)
    return {"has_alpha": True, "empty_mask": False, "template": template,
            "mask_bool": mask_bool, "tmpl_centered": centered,
            "tmpl_norm": float(np.sqrt((centered ** 2).sum())), "flat_template": flat}


def find_template(screenshot, raw_template, threshold=0.83):
    """Returns dict(x, y, score, left, top, w, h) of the best match whose score
    >= threshold, else None. x,y = centre of the match (what the bot taps)."""
    r = best_match(screenshot, raw_template)
    if r is None or r["score"] < threshold:
        return None
    return r


def best_match(screenshot, raw_template):
    """Best match regardless of threshold (so the GUI can show 'closest was 0.69')."""
    p = prepare_template(raw_template)
    sh, sw = screenshot.shape[:2]
    if p["has_alpha"]:
        if p.get("empty_mask"):
            return None
        th, tw = p["template"].shape[:2]
        if th > sh or tw > sw:
            return None
        mask, tc, tn = p["mask_bool"], p["tmpl_centered"], p["tmpl_norm"]

        def ncc(region):
            rm = region.astype(np.float64)[mask]
            rc = rm - rm.mean()
            rn = np.sqrt((rc ** 2).sum())
            if tn == 0 or rn == 0:
                return -1.0
            return float((tc * rc).sum() / (tn * rn))

        coarse = cv2.matchTemplate(screenshot, p["flat_template"], cv2.TM_CCOEFF_NORMED)
        flat = coarse.ravel()
        n = min(40, flat.size)
        idx = np.argpartition(-flat, n - 1)[:n]
        order = idx[np.argsort(-flat[idx])]
        cw = coarse.shape[1]
        seen, best = [], None
        for i in order:
            y0, x0 = divmod(int(i), cw)
            if any(abs(cy - y0) < th // 2 and abs(cx - x0) < tw // 2 for cy, cx in seen):
                continue
            seen.append((y0, x0))
            c = ncc(screenshot[y0:y0 + th, x0:x0 + tw])
            if best is None or c > best[2]:
                best = (x0, y0, c)
        x0, y0, c = best
    else:
        t = p["template"]
        th, tw = t.shape[:2]
        if th > sh or tw > sw:
            return None
        res = cv2.matchTemplate(screenshot, t, cv2.TM_CCOEFF_NORMED)
        _, c, _, loc = cv2.minMaxLoc(res)
        x0, y0 = loc
    return {"x": x0 + tw // 2, "y": y0 + th // 2, "score": float(c),
            "left": x0, "top": y0, "w": tw, "h": th}


def read_image_unchanged(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def read_bgr(path):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def offset_for_tap(match, tap_x, tap_y):
    """offset_x/offset_y so that tap = matched centre + offset."""
    return int(tap_x - match["x"]), int(tap_y - match["y"])
