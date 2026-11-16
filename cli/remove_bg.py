"""
Standalone background remover for template icons.

Takes a cropped icon PNG (e.g. straight out of calibrate.py) and makes
the background transparent, so it becomes a proper alpha-masked template
- the kind vision.py's find_template matches most reliably (only the
icon's own shape is compared, not whatever happens to be behind it).

Two modes:

1. Flood-fill from corners (default, no extra dependencies) - good for
   icons on a flat or gently-gradient background. Treats pixels near the
   four corners' colors as background and floods inward from each corner.

       python remove_bg.py icon.png
       python remove_bg.py icon.png --tolerance 40

2. AI background removal (if you `pip install rembg` first) - much
   better for icons with busy/varied backgrounds, at the cost of a
   slower first run (downloads a small model). Only the AI's alpha
   MASK is used - your icon's original pixels are never touched or
   recolored, only cut around, and the mask is hard-thresholded +
   eroded afterward so it matches well under vision.py's masked
   matching. See --edge-threshold and --erode below to tune.

       python remove_bg.py icon.png --ai
       python remove_bg.py icon.png --ai --erode 3

FLAT / SINGLE-COLOR ICONS (white X, arrows, plain glyphs): removing the
background leaves pixels that are all the same color, which matches poorly
(~0.69 even on the right spot). Keep a 1px edge instead:

       python remove_bg.py icon.png --grow 1
       python remove_bg.py icon.png --ai --erode 0 --grow 1

The tool prints a WARNING when the kept pixels look too flat.

Output is saved as <name>_nobg.png next to the input, unless
--out is given.
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


def flood_fill_transparent(img_bgr, tolerance=30):
    """Floods inward from each of the 4 corners, marking connected
    similar-colored pixels as background (alpha=0). Works well when the
    icon was cropped with some flat/gradient margin around it, which is
    the common case straight out of calibrate.py."""
    h, w = img_bgr.shape[:2]
    alpha = np.full((h, w), 255, dtype=np.uint8)

    mask = np.zeros((h + 2, w + 2), np.uint8)
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]

    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)

    combined_mask = np.zeros((h + 2, w + 2), np.uint8)
    for (cx, cy) in corners:
        seed_mask = np.zeros((h + 2, w + 2), np.uint8)
        cv2.floodFill(
            img_bgr.copy(), seed_mask, (cx, cy), 0,
            loDiff=(tolerance,) * 3, upDiff=(tolerance,) * 3, flags=flags,
        )
        combined_mask = cv2.bitwise_or(combined_mask, seed_mask)

    bg_pixels = combined_mask[1:-1, 1:-1] > 0
    alpha[bg_pixels] = 0

    b, g, r = cv2.split(img_bgr)
    return cv2.merge([b, g, r, alpha])


def ai_remove_bg(input_path, edge_threshold=200, erode=2):
    """AI removal, but built so the icon's RGB pixels stay 100% identical
    to the source file - only the alpha channel is AI-derived.

    rembg's normal cutout path (especially with alpha_matting) doesn't
    just predict a mask - it also runs foreground color estimation, which
    un-blends edge pixels and rewrites their RGB values to what it guesses
    they'd be without background bleed-through. That's good for a pretty
    picture but bad for template matching: those pixels then no longer
    match what's actually on-screen.

    So instead we ask rembg for only_mask=True (just the segmentation
    mask, no color touched at all) and merge that mask ourselves onto the
    ORIGINAL image loaded fresh via cv2.imread - guaranteeing every B/G/R
    value in the output is exactly what was in the input file."""
    try:
        from rembg import remove
    except ImportError:
        print("AI mode needs the 'rembg' package: pip install rembg")
        raise SystemExit(1)

    img = cv2.imread(input_path, cv2.IMREAD_COLOR)
    if img is None:
        return None

    with open(input_path, "rb") as f:
        input_bytes = f.read()

    # only_mask=True short-circuits rembg before it ever builds a cutout/
    # recolors anything - post_process_mask just does mask-only morphology
    # (fills small holes, drops speckle islands), still no pixel touching.
    mask_bytes = remove(input_bytes, only_mask=True, post_process_mask=True)
    mask_arr = np.frombuffer(mask_bytes, dtype=np.uint8)
    mask = cv2.imdecode(mask_arr, cv2.IMREAD_GRAYSCALE)

    if mask is None or mask.shape[:2] != img.shape[:2]:
        mask = cv2.resize(mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)

    # Hard-threshold: kill the soft/semi-transparent fringe outright rather
    # than let vision.py's alpha > 10 check pull it in as "included".
    _, mask_clean = cv2.threshold(mask, edge_threshold, 255, cv2.THRESH_BINARY)

    # Erode a couple pixels further inward - even pixels that passed the
    # hard threshold can sit right on the true boundary line.
    if erode > 0:
        kernel = np.ones((3, 3), np.uint8)
        mask_clean = cv2.erode(mask_clean, kernel, iterations=erode)

    b, g, r = cv2.split(img)
    return cv2.merge([b, g, r, mask_clean])


def grow_mask(result_bgra, grow):
    """Expands the opaque area outward by `grow` pixels. Newly included
    pixels keep their ORIGINAL colors (nothing is repainted).

    Why this exists: a background-removed template keeps only the icon's
    own pixels. For a flat single-color glyph (e.g. a white X) that leaves
    pixels that are all nearly the same color - and the matcher compares
    patterns (mean-subtracted), so a uniform patch has almost nothing to
    compare and scores low (~0.69) even on the correct spot. Growing the
    mask by 1px pulls in the thin dark anti-aliased edge around the glyph,
    giving the matcher real contrast (scores ~1.00). Growing much further
    starts including background that changes between screens, which hurts."""
    if grow <= 0:
        return result_bgra
    b, g, r, a = cv2.split(result_bgra)
    kernel = np.ones((3, 3), np.uint8)
    a = cv2.dilate(a, kernel, iterations=grow)
    return cv2.merge([b, g, r, a])


def check_mask_quality(result_bgra):
    """Warns when the kept (opaque) pixels are nearly one flat color -
    such a template matches poorly even when it is cropped perfectly."""
    b, g, r, a = cv2.split(result_bgra)
    keep = a > 10
    n = int(keep.sum())
    total = a.size
    if n == 0:
        print("WARNING: nothing is left opaque - the whole image was removed as background.")
        return
    lum = cv2.cvtColor(result_bgra[:, :, :3], cv2.COLOR_BGR2GRAY)[keep].astype(float)
    spread = lum.std()
    print(f"Kept {n}/{total} pixels ({100 * n / total:.0f}%), color variation (std) {spread:.0f}")
    if spread < 50:
        print("WARNING: the kept pixels are almost all one flat color, so this template will")
        print("         likely score LOW in verify_template.py even at the right spot.")
        print("         Fix: include the thin edge around the icon -> add  --grow 1")
        print("         (with --ai also add  --erode 0 , because eroding thin shapes removes the edge).")


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


def main():
    parser = argparse.ArgumentParser(description="Remove background from a template icon PNG")
    parser.add_argument("image", help="Path to the cropped icon PNG")
    parser.add_argument("--tolerance", type=int, default=30,
                         help="Color similarity tolerance for flood-fill mode (default 30, higher = more removed)")
    parser.add_argument("--ai", action="store_true",
                         help="Use AI background removal instead (needs: pip install rembg)")
    parser.add_argument("--edge-threshold", type=int, default=200,
                         help="[--ai only] Alpha cutoff (0-255) for keeping an edge pixel as icon - "
                              "raise if fringe/halo still shows, lower if edges look bitten-into (default 200)")
    parser.add_argument("--erode", type=int, default=2,
                         help="[--ai only] Extra pixels to shrink the mask inward after thresholding, "
                              "to strip blended edge pixels that hurt match scores (default 2, 0 to disable)")
    parser.add_argument("--grow", type=int, default=0,
                         help="Expand the kept area outward by N pixels after removal (both modes). "
                              "Use --grow 1 for flat single-color icons (white X, arrows...) so the thin "
                              "edge around them is kept - fixes low match scores (default 0)")
    parser.add_argument("--out", help="Output path (default: <name>_nobg.png, saved next to the input)")
    parser.add_argument(
        "--templates-dir",
        default="templates",
        help="Folder to look for the input in, if not found where you are (default: ./templates/)",
    )
    args = parser.parse_args()

    image_path = resolve_path(args.image, args.templates_dir)
    if not os.path.exists(image_path):
        print(f"Could not find '{args.image}' - checked that path and '{args.templates_dir}/{args.image}'.")
        return

    if args.ai:
        result = ai_remove_bg(image_path, edge_threshold=args.edge_threshold, erode=args.erode)
    else:
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if img is None:
            print(f"Could not load '{args.image}' - check the path.")
            return
        result = flood_fill_transparent(img, args.tolerance)

    if args.out:
        out_path = args.out
    else:
        # Saved next to the RESOLVED input - so a bare filename that got
        # found inside templates/ produces its output there too, not in
        # whatever folder you happened to run the script from.
        base, ext = os.path.splitext(image_path)
        out_path = f"{base}_nobg.png"

    if result is None:
        print(f"Could not load '{args.image}' - check the path.")
        return
    result = grow_mask(result, args.grow)
    check_mask_quality(result)

    cv2.imwrite(out_path, result)
    print(f"Saved -> {out_path}")
    print("Open it and check the icon's edges look clean (no background fringe,")
    print("no bites taken out of the icon itself). If it looks off, try a different")
    print("--tolerance value, or --ai for a busier background.")


if __name__ == "__main__":
    main()
