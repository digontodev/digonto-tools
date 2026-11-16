"""
Standalone interactive calibration helper.

Opens a real screenshot on YOUR screen and lets YOU click/drag to get
exact tap coordinates or crop out a template icon - no bot project
dependencies required, just opencv-python.

Run this from your project's ROOT folder (the one containing your
templates/ subfolder) - every path below is resolved automatically:
  - screenshots: looked for as given, or inside templates/ if not
    found where you are
  - saved crops: always land in templates/ (or --out-dir if you set one)

USAGE

  Get a tap coordinate (just click one point):
      python calibrate.py screenshot.png

  Crop a template image (drag a box around a button/icon):
      python calibrate.py screenshot.png --save-as search_icon.png

  Change where cropped templates get saved (default: ./templates/):
      python calibrate.py screenshot.png --save-as search_icon.png --out-dir my_templates

Take the screenshot first with:
      adb shell screencap /sdcard/screen.png
      adb pull /sdcard/screen.png
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


def main():
    parser = argparse.ArgumentParser(description="Interactive coordinate/template picker")
    parser.add_argument("screenshot", help="Path to a screenshot PNG (e.g. pulled via adb)")
    parser.add_argument(
        "--save-as",
        help="Filename to save the selected region as a template, e.g. search_icon.png. "
             "If omitted, just prints the center coordinate.",
    )
    parser.add_argument(
        "--out-dir",
        default="templates",
        help="Folder to save cropped templates into (default: ./templates/)",
    )
    parser.add_argument(
        "--templates-dir",
        default="templates",
        help="Folder to look for the input screenshot in, if not found as given (default: ./templates/)",
    )
    args = parser.parse_args()

    screenshot_path = resolve_path(args.screenshot, args.templates_dir)
    img = cv2.imread(screenshot_path)
    if img is None:
        print(f"Could not load '{args.screenshot}' - checked that path and '{args.templates_dir}/{args.screenshot}'.")
        return

    print("\nA window will open showing your screenshot.")
    print("Drag a box tightly around the button/icon you want.")
    print("Press ENTER or SPACE to confirm, or 'c' to cancel.\n")

    window_name = "Drag a box around the target, then press ENTER"
    r = cv2.selectROI(window_name, img, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()

    x, y, w, h = r
    if w == 0 or h == 0:
        print("No region selected - nothing to do.")
        return

    center_x = x + w // 2
    center_y = y + h // 2

    print(f"Center coordinate (for 'tap' steps): x={center_x}, y={center_y}")
    print(f"Box: x={x}, y={y}, width={w}, height={h}")

    if args.save_as:
        crop = img[y:y + h, x:x + w]
        os.makedirs(args.out_dir, exist_ok=True)
        out_path = os.path.join(args.out_dir, args.save_as)
        cv2.imwrite(out_path, crop)
        print(f"Saved template -> {out_path}")
        print("Use this in config.yaml as: template: \"" + args.save_as + "\"")


if __name__ == "__main__":
    main()
