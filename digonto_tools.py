#!/usr/bin/env python3
"""Digonto Tools - launcher. Run:  python3 digonto_tools.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    try:
        import macname
        macname.mac_app_name("Digonto Tools")
    except Exception:
        pass
    if os.name == "nt":
        try:   # own taskbar icon instead of the Python icon
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Digonto.Tools")
        except Exception:
            pass
    try:
        import PySide6.QtWidgets  # noqa: F401
        import cv2, numpy, PIL  # noqa: F401,E401
    except Exception as e:
        print("A required package is missing (" + str(e) + ").\nRun the installer that came with Digonto Tools again\n"
              "(install-linux.sh / install-macos.command / install-windows.bat).", file=sys.stderr)
        sys.exit(1)
    from imagetools.qt.main import main as run
    run()


if __name__ == "__main__":
    main()
