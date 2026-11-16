"""
Standalone ADB screenshot capture tool.

Grabs a screenshot straight off a connected Android device/emulator via
adb and saves it as a PNG in the current folder - no bot-project
dependencies, just a working `adb` on PATH. Meant to feed calibrate.py
(crop templates out of it) and verify_template.py (test a template
against it) without needing the bot itself running.

Usage:
    python cap.py
    python cap.py my_screenshot.png
    python cap.py -s 127.0.0.1:7555
    python cap.py -s 127.0.0.1:7555 castle_screen.png

If no filename is given, saves as screenshot_<timestamp>.png in the
current folder. -s/--device is only needed when more than one device/
emulator is connected (adb devices to list serials).

Captured via `adb exec-out screencap -p`, read as raw bytes straight
from the subprocess (never routed through a text-mode pipe or shell
redirection), to avoid the classic adb screenshot corruption bug where
\\r\\n gets silently inserted into the binary PNG data on some
platforms/adb versions.
"""

import subprocess
import argparse
from datetime import datetime


def capture(device=None, out_path=None):
    cmd = ["adb"]
    if device:
        cmd += ["-s", device]
    cmd += ["exec-out", "screencap", "-p"]

    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError:
        print("adb not found on PATH - install Android platform-tools first.")
        raise SystemExit(1)

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        print(f"adb screencap failed (exit {result.returncode}){': ' + stderr if stderr else ''}")
        if device is None:
            print("If more than one device is connected, pass -s <serial_or_ip:port> (see `adb devices`).")
        raise SystemExit(1)

    data = result.stdout

    if not data.startswith(b"\x89PNG"):
        print("Warning: captured data doesn't start with a valid PNG header - this is the")
        print("classic adb-corruption symptom. Saving anyway so you can inspect it, but it's")
        print("likely unusable. If it keeps happening, try a different adb version/platform-tools build.")

    if out_path is None:
        out_path = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

    with open(out_path, "wb") as f:
        f.write(data)

    print(f"Saved -> {out_path} ({len(data) / 1024:.1f} KB)")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Capture a screenshot from an Android device/emulator via adb")
    parser.add_argument("output", nargs="?",
                         help="Output filename (default: screenshot_<timestamp>.png in the current folder)")
    parser.add_argument("-s", "--device",
                         help="adb device serial or host:port (e.g. 127.0.0.1:7555) - "
                              "only needed if more than one device is connected")
    args = parser.parse_args()

    capture(device=args.device, out_path=args.output)


if __name__ == "__main__":
    main()
