"""
Standalone template verifier.

Verify a template actually matches the RIGHT spot on a screenshot, before
trusting it in config.yaml - no bot project dependencies required, just
opencv-python and numpy.

If the template PNG has an alpha channel (transparency), only the opaque
pixels are used for matching (masked normalized cross-correlation) -
matches fwbot's real vision.py behavior exactly, so a match/score you see
here is the same one the bot itself would get.

Usage:
    python verify_template.py screenshot.png template.png
    python verify_template.py screenshot.png template.png --threshold 0.90

Prints the match location + confidence score, and saves a copy of the
screenshot with a red box drawn around the matched area as
"verify_result.png" - open that and check with your own eyes that the box
is actually around the icon you meant, not somewhere else that happened to
score high.
"""

# --- Auto-use the local virtual environment (a "venv" or ".venv" folder next
# --- to this script), so you never have to activate it. If none exists, this
# --- does nothing and the script runs with whatever Python you started it with.
import os, sys, subprocess


def _use_local_venv():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("venv", ".venv"):
        venv_dir = os.path.join(here, name)
        for rel in (("bin", "python"), ("Scripts", "python.exe")):
            py = os.path.join(venv_dir, *rel)
            if not os.path.exists(py):
                continue
            already = os.path.realpath(sys.prefix) == os.path.realpath(venv_dir)
            if already or os.environ.get("_IMGTOOLS_REEXEC"):
                return
            os.environ["_IMGTOOLS_REEXEC"] = "1"
            if os.name == "nt":
                sys.exit(subprocess.call([py] + sys.argv))
            os.execv(py, [py] + sys.argv)


_use_local_venv()
# ---------------------------------------------------------------------------


import cv2
import numpy as np
import argparse
import os


def resolve_path(path, templates_dir="templates"):
    """Accepts either a real path, or a bare filename - if it isn't
    found relative to where you're running the script from, falls back
    to looking inside templates_dir. Lets you run every tool from the
    project root without typing 'templates/' in front of every filename."""
    if os.path.exists(path):
        return path
    candidate = os.path.join(templates_dir, path)
    if os.path.exists(candidate):
        return candidate
    return path  # not found anywhere - let the caller's own error message handle it


# ---------------------------------------------------------------------
# find_template, inlined from fwbot's core/vision.py so this script has
# zero dependencies on the rest of the bot project. Logic is unchanged.
# ---------------------------------------------------------------------

_template_cache = {}


def _prepare_template(raw):
    has_alpha = raw.ndim == 3 and raw.shape[2] == 4
    if not has_alpha:
        template = raw if raw.ndim == 3 else cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
        return {"has_alpha": False, "template": template}

    template = raw[:, :, :3]
    alpha = raw[:, :, 3]
    mask = np.where(alpha > 10, 255, 0).astype(np.uint8)
    mask_bool = mask > 0
    if mask_bool.sum() == 0:
        return {"has_alpha": True, "empty_mask": True}

    tmpl_f = template.astype(np.float64)
    tmpl_masked = tmpl_f[mask_bool]
    tmpl_centered = tmpl_masked - tmpl_masked.mean()
    tmpl_norm = float(np.sqrt((tmpl_centered ** 2).sum()))

    flat_template = template.copy()
    flat_template[~mask_bool] = tmpl_masked.mean(axis=0)

    return {
        "has_alpha": True,
        "empty_mask": False,
        "template": template,
        "mask_bool": mask_bool,
        "tmpl_centered": tmpl_centered,
        "tmpl_norm": tmpl_norm,
        "flat_template": flat_template,
    }


def _load_template(template_path: str):
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template image not found: {template_path}")

    mtime = os.path.getmtime(template_path)
    cached = _template_cache.get(template_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    raw = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise ValueError(f"Could not read template image: {template_path}")

    prepared = _prepare_template(raw)
    _template_cache[template_path] = (mtime, prepared)
    return prepared


def find_template(screenshot, template_path: str, threshold: float = 0.83):
    """Returns (x, y, score) center coords + match confidence of the best
    match, or None if no match scores above `threshold`."""
    prepared = _load_template(template_path)
    sh, sw = screenshot.shape[:2]

    if prepared["has_alpha"]:
        if prepared.get("empty_mask"):
            return None

        template = prepared["template"]
        mask_bool = prepared["mask_bool"]
        tmpl_centered = prepared["tmpl_centered"]
        tmpl_norm = prepared["tmpl_norm"]
        flat_template = prepared["flat_template"]
        th, tw = template.shape[:2]
        if th > sh or tw > sw:
            return None

        def masked_cross_correlation(region):
            region_f = region.astype(np.float64)
            region_masked = region_f[mask_bool]
            region_centered = region_masked - region_masked.mean()
            region_norm = np.sqrt((region_centered ** 2).sum())
            if tmpl_norm == 0 or region_norm == 0:
                return -1.0
            return float((tmpl_centered * region_centered).sum() / (tmpl_norm * region_norm))

        coarse = cv2.matchTemplate(screenshot, flat_template, cv2.TM_CCOEFF_NORMED)
        flat_scores = coarse.ravel()
        top_n = min(40, flat_scores.size)
        top_idx = np.argpartition(-flat_scores, top_n - 1)[:top_n]
        order = top_idx[np.argsort(-flat_scores[top_idx])]
        coarse_h, coarse_w = coarse.shape

        checked_boxes = []
        best = None
        for idx in order:
            y0, x0 = divmod(int(idx), coarse_w)
            too_close = any(
                abs(cy - y0) < th // 2 and abs(cx - x0) < tw // 2
                for (cy, cx) in checked_boxes
            )
            if too_close:
                continue
            checked_boxes.append((y0, x0))

            region = screenshot[y0:y0 + th, x0:x0 + tw]
            corr = masked_cross_correlation(region)

            if corr >= threshold and (best is None or corr > best[2]):
                best = (x0 + tw // 2, y0 + th // 2, corr)

        return best
    else:
        template = prepared["template"]
        th, tw = template.shape[:2]
        if th > sh or tw > sw:
            return None
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            return None

        center_x = max_loc[0] + tw // 2
        center_y = max_loc[1] + th // 2
        return (center_x, center_y, max_val)


# ---------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Verify a template's match location on a screenshot")
    parser.add_argument("screenshot", help="Path to a screenshot PNG")
    parser.add_argument("template", help="Path to the template PNG to test")
    parser.add_argument("--threshold", type=float, default=0.90, help="Match threshold (default 0.90)")
    parser.add_argument(
        "--templates-dir",
        default="templates",
        help="Folder to look for bare filenames in, for both args, if not found where you are (default: ./templates/)",
    )
    args = parser.parse_args()

    screenshot_path = resolve_path(args.screenshot, args.templates_dir)
    img = cv2.imread(screenshot_path)
    if img is None:
        print(f"Could not load '{args.screenshot}' - checked that path and '{args.templates_dir}/{args.screenshot}'.")
        return

    template_path = resolve_path(args.template, args.templates_dir)
    if not os.path.exists(template_path):
        print(f"Could not find '{args.template}' - checked that path and '{args.templates_dir}/{args.template}'.")
        return

    result = find_template(img, template_path, args.threshold)

    if result is None:
        print(f"NOT FOUND - '{args.template}' did not match anywhere above threshold {args.threshold}.")
        print("If you expected a match, try a lower --threshold to see how close it gets,")
        print("or the template may need re-cropping.")
        return

    x, y, score = result
    print(f"MATCH at ({x}, {y}), confidence {score:.4f}")

    template_img = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    th, tw = template_img.shape[:2]
    top_left = (x - tw // 2, y - th // 2)
    bottom_right = (x + tw // 2, y + th // 2)

    preview = img.copy()
    cv2.rectangle(preview, top_left, bottom_right, (0, 0, 255), 2)
    cv2.circle(preview, (x, y), 4, (0, 0, 255), -1)

    out_path = "verify_result.png"
    cv2.imwrite(out_path, preview)
    print(f"\nSaved '{out_path}' - open it and check the red box is actually")
    print("around the icon you intended, not somewhere else that happened to match.")


if __name__ == "__main__":
    main()
