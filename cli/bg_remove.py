"""
Local background removal tool - makes a clean transparent icon from a
screenshot crop, preserving the icon's ORIGINAL pixel values exactly
(no smoothing, no AI re-rendering, no vector tracing). This is what
actually matters for template matching - the icon pixels must stay
byte-for-byte identical to what's really on screen, only the
background gets cut away.

Run this from your project's ROOT folder (the one containing your
templates/ subfolder):
  - input: looked for as given, or inside templates/ if not found
    where you are
  - output: if you give a bare filename (no folder in it), it's saved
    into templates/ automatically - same as the input, you never need
    to type "templates/" yourself

USAGE:
    python bg_remove.py crop.png output.png

A window opens showing your image, zoomed in for precision. Click on
the BACKGROUND (not the icon) - click multiple spots if the background
has more than one shade/region. Press ENTER when done to save, 'r' to
reset if you click the wrong spot, 'q' to quit without saving.

Adjust TOLERANCE below if too much or too little gets removed:
  - Too much of the icon disappearing -> LOWER the tolerance
  - Background left behind around the edges -> RAISE the tolerance
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
import sys
import os

TOLERANCE = 30  # how close a pixel's color must be to your click to count as "background"
ZOOM = 4        # display scale, for easier precise clicking on small icons
TEMPLATES_DIR = "templates"

clicked_points = []


def resolve_path(path, templates_dir=TEMPLATES_DIR):
    """Accepts either a real path, or a bare filename - if it isn't
    found relative to where you're running the script from, falls back
    to looking inside templates_dir."""
    if os.path.exists(path):
        return path
    candidate = os.path.join(templates_dir, path)
    if os.path.exists(candidate):
        return candidate
    return path  # not found anywhere - let the caller's own error message handle it


def resolve_output_path(path, templates_dir=TEMPLATES_DIR):
    """If given a bare filename (no folder in it), save into
    templates_dir instead of whatever folder the script happened to be
    run from - keeps outputs landing in the same place as the input
    without you needing to type the folder name yourself."""
    if os.path.dirname(path):
        return path  # user gave an explicit folder - respect it as-is
    os.makedirs(templates_dir, exist_ok=True)
    return os.path.join(templates_dir, path)


def mouse_callback(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        # convert from the zoomed display coordinates back to real image coordinates
        real_x, real_y = x // ZOOM, y // ZOOM
        clicked_points.append((real_x, real_y))
        print(f"Marked background point at ({real_x}, {real_y})")


def main():
    if len(sys.argv) != 3:
        print("Usage: python bg_remove.py input.png output.png")
        return

    input_arg, output_arg = sys.argv[1], sys.argv[2]
    input_path = resolve_path(input_arg)
    output_path = resolve_output_path(output_arg)

    img = cv2.imread(input_path, cv2.IMREAD_COLOR)
    if img is None:
        print(f"Could not load '{input_arg}' - checked that path and '{TEMPLATES_DIR}/{input_arg}'.")
        return

    h, w = img.shape[:2]
    display = cv2.resize(img, (w * ZOOM, h * ZOOM), interpolation=cv2.INTER_NEAREST)

    window_name = "Click background pixels, ENTER to save, 'r' to reset, 'q' to quit"
    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, mouse_callback)

    print("Click on the background (click several spots if it has more than one color/shade).")
    print("Press ENTER when done.")

    while True:
        preview = display.copy()
        for (px, py) in clicked_points:
            cv2.circle(preview, (px * ZOOM, py * ZOOM), 4, (0, 0, 255), -1)
        cv2.imshow(window_name, preview)
        key = cv2.waitKey(20) & 0xFF

        if key == 13:  # ENTER
            break
        elif key == ord('r'):
            clicked_points.clear()
            print("Reset - click again.")
        elif key == ord('q'):
            cv2.destroyAllWindows()
            print("Cancelled, nothing saved.")
            return

    cv2.destroyAllWindows()

    if not clicked_points:
        print("No background points clicked - nothing to remove, exiting.")
        return

    # Build an alpha mask: start fully opaque, then flood-fill from each
    # clicked point and mark matched regions as transparent. This never
    # touches the RGB values of any pixel - only decides which ones become
    # invisible. The icon's real pixels are never altered.
    alpha = np.full((h, w), 255, dtype=np.uint8)

    for (px, py) in clicked_points:
        mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
        flood_img = img.copy()
        cv2.floodFill(
            flood_img, mask, (px, py),
            newVal=(0, 0, 0),
            loDiff=(TOLERANCE, TOLERANCE, TOLERANCE),
            upDiff=(TOLERANCE, TOLERANCE, TOLERANCE),
            flags=cv2.FLOODFILL_FIXED_RANGE,
        )
        filled = mask[1:-1, 1:-1] > 0
        alpha[filled] = 0

    b, g, r = cv2.split(img)
    rgba = cv2.merge([b, g, r, alpha])
    cv2.imwrite(output_path, rgba)

    transparent_pct = (alpha == 0).sum() / alpha.size * 100
    print(f"\nSaved '{output_path}' - {transparent_pct:.1f}% of the image is now transparent.")
    print("If that seems too high (icon got eaten) or too low (background left over),")
    print(f"adjust TOLERANCE at the top of this script (currently {TOLERANCE}) and re-run.")


if __name__ == "__main__":
    main()
