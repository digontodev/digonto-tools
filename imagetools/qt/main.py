"""Digonto Tools - main window (same layout as Digonto Images: tool column, picture tabs, panel column)."""
import os
import platform
import subprocess
import sys
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDockWidget, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox,
                               QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget)

import digonto_ui as ui

from .. import APP_NAME, __version__
from ..core import adbcap, screencap
from .areapick import pick_area
from .common import IMG_FILTER, run_bg
from .icons import icon
from .settings import Settings, default_workdir
from .setup_page import SetupPage
from .view import ImageView

SLUG = "digonto-tools"
HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

TOOLS = (("screen", "Screen capture", "Take a screenshot of this computer"),
         ("phone", "Phone (ADB)", "Take a screenshot of an Android phone"),
         ("crop", "Crop & coordinates", "Cut a template and get tap offsets"),
         ("verify", "Verify match", "Find a template in a screenshot"),
         ("bg", "Remove background", "Make the background transparent"),
         ("setup", "Setup", "Components, folders and updates"))



def bring_app_to_front():
    """make this program the active one again (macOS: after the system screenshot tool had the focus)"""
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        send_bool = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(("objc_msgSend", objc))
        app = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        if app:
            send_bool(app, objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
    except Exception:                                   # noqa: BLE001 - never fail a capture because of this
        pass
    # newer macOS lets an app come to the front by itself only right after the user used it; after a slow
    # selection it refuses. Asking macOS to "open" our own app always works: it brings the running window back.
    bundle = os.path.abspath(sys.executable)
    for _ in range(3):
        bundle = os.path.dirname(bundle)
    if bundle.endswith(".app"):
        try:
            subprocess.Popen(["open", bundle], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

class Doc:
    def __init__(self, path, pil):
        self.path, self.pil = path, pil
        self.sel = None
        self.tap = None
        self.match = None
        self.bg_src = None
        self.bg_result = None
        self.bg_points = []
        self.show_orig = False

    @property
    def name(self):
        return os.path.basename(self.path) if self.path else "picture"


def pil_from_bgr(arr):
    from PIL import Image
    import cv2
    if arr.ndim == 3 and arr.shape[2] == 4:
        return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA))
    if arr.ndim == 3:
        return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
    return Image.fromarray(arr)


def note(text):
    l = QLabel(text)
    l.setWordWrap(True)
    l.setStyleSheet("color:#6B6075;")
    return l


def hbox(*ws, stretch=False):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        h.addWidget(x, 1)
    return w


def big(text, fn, primary=False):
    b = QPushButton(text)
    b.clicked.connect(lambda _=False: fn())
    b.setMinimumHeight(32)
    if primary:
        ui.primary(b)
    return b


class ToolsColumn(QWidget):
    """the narrow column of tool buttons on the left"""
    def __init__(self, on_pick):
        super().__init__()
        self.setFixedWidth(58)
        self.setStyleSheet("ToolsColumn{background:#F3EFF6;} QPushButton{border:1px solid transparent; border-radius:5px; background:transparent;}"
                           "QPushButton:hover{background:#EFE7F5;} QPushButton:checked{background:#FBDCE5; border:1px solid #C2395C;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for key, title, tip in TOOLS:
            if key == "setup":
                v.addStretch(1)
            b = QPushButton()
            b.setCheckable(True)
            b.setIcon(icon(key, 28))
            b.setIconSize(QSize(28, 28))
            b.setFixedSize(42, 40)
            b.setToolTip(f"{title}\n{tip}")
            b.clicked.connect(lambda _=False, k=key: on_pick(k))
            self.group.addButton(b)
            self.buttons[key] = b
            v.addWidget(b)

    def select(self, key):
        self.buttons[key].setChecked(True)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.icon = ui.app_icon(HERE, SLUG)
        self.setWindowIcon(self.icon)
        self.settings = Settings()
        self._setup_dirs(self.settings.get("workdir") or default_workdir())
        self.docs = []
        self.tool = "screen"
        self.last_template = None
        self.current_screenshot = None
        self.win_list = []
        self.setAcceptDrops(True)

        self.view = ImageView()
        self.view.selected.connect(self._selected)
        self.view.clicked.connect(self._clicked)
        self.view.moved.connect(self._moved)
        self.view.zoomChanged.connect(lambda z: self.zoom_lbl.setText(f"{z * 100:.0f}%"))

        self.tabs = QTabBar()
        self.tabs.setTabsClosable(True)
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_doc)

        self.setup_page = SetupPage(self)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.view)
        self.pages.addWidget(self.setup_page)

        col = QWidget()
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.tabs)
        cv.addWidget(self.pages, 1)

        self.tools = ToolsColumn(self.pick_tool)
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self.tools)
        bl.addWidget(col, 1)

        self.inner = QMainWindow()
        self.inner.setWindowFlags(Qt.Widget)
        self.inner.setCentralWidget(body)

        hd = ui.make_header(APP_NAME, "Capture, crop, match and remove backgrounds", self.icon, self.about)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(hd)
        cl.addWidget(self._options_bar())
        cl.addWidget(self.inner, 1)
        self.setCentralWidget(central)

        self._panels()
        self._menus()
        self.cursor_lbl = QLabel("")
        self.statusBar().addPermanentWidget(self.cursor_lbl)
        self.folder_btn = QPushButton()
        self.folder_btn.setFlat(True)
        self.folder_btn.clicked.connect(self.change_workdir)
        self.statusBar().addPermanentWidget(self.folder_btn)
        self._show_folder()
        ui.fit_window(self, 1320, 860)
        self.tools.select("screen")
        self.pick_tool("screen")
        QTimer.singleShot(400, self.phone_refresh)

    # ------------------------------------------------------------ folders
    def _setup_dirs(self, root):
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            root = default_workdir()
            os.makedirs(root, exist_ok=True)
        self.dirs = {"workdir": root}
        for n in ("screenshots", "templates", "output"):
            self.dirs[n] = os.path.join(root, n)
            os.makedirs(self.dirs[n], exist_ok=True)

    def _show_folder(self):
        p = self.dirs["workdir"]
        self.folder_btn.setText("Folder: " + (p if len(p) <= 48 else "…" + p[-47:]) + "   (change)")

    def change_workdir(self):
        d = QFileDialog.getExistingDirectory(self, "Working folder", self.dirs["workdir"])
        if d:
            self.settings.set("workdir", d)
            self._setup_dirs(d)
            self._show_folder()
            self.reload_templates()
            self.setup_page.refresh()
            self.status("Working folder: " + d)

    def open_workdir(self):
        self._open_folder(self.dirs["workdir"])

    def _open_folder(self, path):
        if not path or not os.path.isdir(path):
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:  # noqa: BLE001
            pass

    def status(self, text, ms=6000):
        self.statusBar().showMessage(text, ms)

    # ------------------------------------------------------------ chrome
    def _options_bar(self):
        bar = QFrame()
        bar.setStyleSheet("QFrame#opt{background:#F3EFF6; border-bottom:1px solid #DDD5E5;}")
        bar.setObjectName("opt")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 4, 10, 4)
        self.tool_title = QLabel("")
        self.tool_title.setStyleSheet("font-weight:bold; color:#C2395C;")
        self.tool_title.setMinimumWidth(150)
        h.addWidget(self.tool_title)
        h.addSpacing(14)
        self.mode_btns = {}
        grp = QButtonGroup(self)
        for key, text, tip in (("pan", "Move", "Drag to move the picture, wheel to zoom"),
                               ("select", "Select", "Drag a rectangle"),
                               ("click", "Point", "Click a point")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            grp.addButton(b)
            self.mode_btns[key] = b
            h.addWidget(b)
        h.addSpacing(14)
        self.view_btns = []
        for text, fn in (("Fit", lambda: self.view.fit()), ("100 %", lambda: self.view.actual_size())):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            h.addWidget(b)
            self.view_btns.append(b)
        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setMinimumWidth(48)
        h.addWidget(self.zoom_lbl)
        h.addStretch(1)
        ob = QPushButton("Open picture…")
        ob.clicked.connect(self.open_dialog)
        h.addWidget(ob)
        return bar

    def _menus(self):
        fm = ui.file_menu(self, [("Open picture…", "Ctrl+O", self.open_dialog), ("Save a copy…", "Ctrl+Shift+S", self.save_copy),
                                 ("Close project", "Ctrl+W", lambda: self.close_doc(self.tabs.currentIndex())),
                                 ("Change working folder…", None, self.change_workdir), ("Open working folder", None, self.open_workdir)])
        vm = self.menuBar().addMenu("View")
        for text, sc, fn in (("Fit to window", "Ctrl+0", lambda: self.view.fit()), ("Actual size", "Ctrl+1", lambda: self.view.actual_size())):
            a = QAction(text, self)
            a.setShortcut(sc)
            a.triggered.connect(lambda _=False, f=fn: f())
            vm.addAction(a)

    def about(self):
        info = (f"Files are kept in: {self.dirs.get('workdir', '')}<br>"
                f"System: {platform.system()} {platform.release()} · Python {platform.python_version()}")
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          "<p>Screenshots of this computer or an Android phone, cropping templates with tap coordinates, template matching "
                          "and background removal (automatic, manual or AI).</p>"
                          "<p>The picture editor is Digonto Images, the vector editor is Digonto Vector.</p>"
                          f"<p style='color:#6B6075'>{info}</p>")

    # ------------------------------------------------------------ panels (right column)
    def _panels(self):
        self.stack = ui.PanelStack("Digonto", "Tools")
        self.opts = QStackedWidget()
        self.builders = {"screen": self._p_screen, "phone": self._p_phone, "crop": self._p_crop,
                         "verify": self._p_verify, "bg": self._p_bg, "setup": self._p_setup}
        self.opt_index = {}
        for key, _t, _tip in TOOLS:
            w = self.builders[key]()
            self.opt_index[key] = self.opts.addWidget(w)
        self.stack.add("Options", self.opts)

        def _fit_page(i):
            from PySide6.QtWidgets import QSizePolicy
            for k in range(self.opts.count()):
                pg = self.opts.widget(k)
                pol = pg.sizePolicy()
                pol.setVerticalPolicy(QSizePolicy.Preferred if k == i else QSizePolicy.Ignored)
                pg.setSizePolicy(pol)
            self.opts.updateGeometry()
        self.opts.currentChanged.connect(_fit_page)
        _fit_page(self.opts.currentIndex())
        self.info_lbl = QLabel("No picture open.")
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setContentsMargins(10, 8, 10, 8)
        self.stack.add("Picture", self.info_lbl)
        self.result_lbl = QLabel("")
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_lbl.setContentsMargins(10, 8, 10, 8)
        self.stack.add("Result", self.result_lbl)
        self.stack.restore()
        ui.shrinkable(self.stack)
        from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QSizePolicy
        for lab in self.stack.findChildren(QLabel):
            lab.setWordWrap(True)
        for kind in (QAbstractSpinBox, QLineEdit):
            for sp in self.stack.findChildren(kind):
                sp.setMinimumWidth(40)
                pol = sp.sizePolicy()
                pol.setHorizontalPolicy(QSizePolicy.Ignored)
                sp.setSizePolicy(pol)
        d = QDockWidget("Panels", self.inner)
        d.setObjectName("Panels")
        d.setTitleBarWidget(QWidget())
        d.setFeatures(QDockWidget.NoDockWidgetFeatures)
        d.setWidget(ui.ScrollColumn(self.stack))
        self.inner.addDockWidget(Qt.RightDockWidgetArea, d)
        self.d_side = d
        self.inner.resizeDocks([d], [ui.dock_width_for(self.width(), self.stack.minimumSizeHint().width())], Qt.Horizontal)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "d_side"):
            self.inner.resizeDocks([self.d_side], [ui.dock_width_for(self.width(), self.stack.minimumSizeHint().width())], Qt.Horizontal)

    def _page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        return w, v

    def _head(self, v, text):
        l = QLabel(text)
        l.setStyleSheet("font-weight:bold; color:#4A1F6E; margin-top:6px;")
        v.addWidget(l)

    # ---- Screen capture
    def _p_screen(self):
        w, v = self._page()
        v.addWidget(note("Take a screenshot of this computer: the whole screen, an area you drag, or one window."))
        f = QFormLayout()
        self.delay = QSpinBox()
        self.delay.setRange(0, 30)
        f.addRow("Delay (seconds)", self.delay)
        v.addLayout(f)
        self.hide_win = QCheckBox("Hide window while capturing")
        self.hide_win.setChecked(True)
        v.addWidget(self.hide_win)
        v.addWidget(big("Full screen", lambda: self.start_capture("full"), True))
        v.addWidget(big("Select area (drag)", lambda: self.start_capture("area")))
        self._head(v, "Window")
        if screencap.kind() == "windows":
            self.win_combo = QComboBox()
            v.addWidget(self.win_combo)
            v.addWidget(hbox(big("Refresh list", self.refresh_windows), big("Capture this window", lambda: self.start_capture("window"))))
            QTimer.singleShot(500, self.refresh_windows)
        else:
            v.addWidget(big("Click a window to capture", lambda: self.start_capture("window")))
        v.addWidget(note(self._env_note()))
        v.addStretch(1)
        return w

    def _env_note(self):
        k = screencap.kind()
        if k == "wayland":
            ok = any(screencap.shutil.which(t) for t in ("gnome-screenshot", "spectacle", "grim"))
            return ("Wayland session: capture uses the system screenshot helper." if ok else
                    "Wayland session: a screenshot helper is missing. Reinstall Digonto Tools from Digonto Studio, or run: sudo apt install gnome-screenshot")
        if k == "mac":
            return "macOS: the first time, allow Digonto Tools under System Settings > Privacy & Security > Screen Recording."
        return ""

    def refresh_windows(self):
        try:
            self.win_list = [x for x in screencap.list_windows() if x[0]]
        except Exception:  # noqa: BLE001
            self.win_list = []
        self.win_combo.clear()
        for t, _h, (l, tp, r, b) in self.win_list:
            self.win_combo.addItem(f"{t}  ({r - l}×{b - tp})")

    def start_capture(self, mode):
        d = self.delay.value()
        if d:
            self._countdown(d, mode)
        else:
            self._go(mode)

    def _countdown(self, n, mode):
        if n <= 0:
            self.statusBar().clearMessage()
            self._go(mode)
            return
        self.statusBar().showMessage(f"Capturing in {n} …")
        QTimer.singleShot(1000, lambda: self._countdown(n - 1, mode))

    def _go(self, mode):
        wayland = screencap.kind() == "wayland"
        hide = self.hide_win.isChecked() and not wayland
        if hide:
            self.hide()
            QApplication.processEvents()
        if wayland:
            self.status("Choose “Window” in the desktop's screenshot bar, then click the window" if mode == "window"
                        else "Waiting for the system screenshot … click Share / Allow if your desktop asks")

        def work():
            if hide:
                time.sleep(0.4)
            if mode == "full":
                return screencap.grab_full()
            if mode == "area":
                if screencap.uses_native_area():
                    return screencap.native_area()
                return ("pick", screencap.grab_full())
            if screencap.kind() == "windows":
                i = self.win_combo.currentIndex()
                if i < 0:
                    raise screencap.CaptureError("Choose a window in the list first.")
                return screencap.grab_window_windows(self.win_list[i][1])
            return screencap.window_click_pick()

        def finish():
            if hide:
                self.show()
            # macOS's own screenshot tool (area / window) keeps the focus when it ends: bring Digonto Tools back
            # to the front, so the new picture is seen at once (also when the window was not hidden)
            self.showNormal() if self.isMinimized() else None
            self.raise_()
            self.activateWindow()
            bring_app_to_front()
            QTimer.singleShot(400, lambda: (self.raise_(), self.activateWindow()))

        def done(img):
            finish()
            if isinstance(img, tuple) and img and img[0] == "pick":
                img = pick_area(self, img[1])
            self._store_capture(img, "screen")

        def fail(e):
            finish()
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "Capture failed", str(e))
        run_bg(work, done, fail)

    def _store_capture(self, img, prefix):
        if img is None:
            self.status("Capture cancelled")
            return
        out = os.path.join(self.dirs["screenshots"], time.strftime(f"{prefix}_%Y%m%d_%H%M%S.png"))
        img.save(out)
        self.open_path(out)
        self.status(f"Saved {out}")

    # ---- Phone
    def _p_phone(self):
        w, v = self._page()
        v.addWidget(note("Take a screenshot of an Android phone or emulator over USB or Wi-Fi (adb)."))
        self._head(v, "Add a device by IP address")
        self.addr = QComboBox()
        self.addr.setEditable(True)
        self.addr.addItems(self.settings.get("adb_recent", []))
        self.addr.setCurrentText("")
        v.addWidget(hbox(self.addr, big("Connect", self.adb_connect)))
        v.addWidget(note("Example 192.168.1.20:5555  (phone: Settings > Developer options > Wireless debugging)"))
        self.pair_chk = QCheckBox("Pair (Android 11+)")
        self.pair_chk.toggled.connect(lambda on: self.pair_box.setVisible(on))
        v.addWidget(hbox(big("Disconnect", self.adb_disconnect), self.pair_chk))
        self.pair_box = QWidget()
        pf = QFormLayout(self.pair_box)
        pf.setContentsMargins(0, 0, 0, 0)
        self.pair_addr, self.pair_code = QLineEdit(), QLineEdit()
        pf.addRow("Pairing address (ip:port)", self.pair_addr)
        pf.addRow("Pairing code (6 digits)", self.pair_code)
        pf.addRow(big("Pair", self.adb_pair))
        self.pair_box.hide()
        v.addWidget(self.pair_box)
        self._head(v, "Devices")
        self.dev = QComboBox()
        v.addWidget(self.dev)
        v.addWidget(big("Refresh devices", self.phone_refresh))
        self.cap_btn = big("Capture screenshot", self.adb_capture, True)
        v.addWidget(self.cap_btn)
        self.phone_msg = note("")
        v.addWidget(self.phone_msg)
        v.addStretch(1)
        return w

    def _remember_addr(self, a):
        rec = [x for x in self.settings.get("adb_recent", []) if x != a]
        rec.insert(0, a)
        self.settings.set("adb_recent", rec[:8])
        self.addr.clear()
        self.addr.addItems(rec[:8])
        self.addr.setCurrentText(a)

    def adb_connect(self):
        text = self.addr.currentText()
        a = adbcap.normalize_addr(text)
        self.status("Connecting…")

        def done(res):
            ok, msg = res
            self.status(msg.splitlines()[0] if msg else "")
            if ok and a:
                self._remember_addr(a)
            self.phone_refresh()
            self.phone_msg.setText(msg)
        run_bg(lambda: adbcap.connect(text), done)

    def adb_disconnect(self):
        ok, msg = adbcap.disconnect(self.addr.currentText() or None)
        self.phone_refresh()
        self.phone_msg.setText(msg)

    def adb_pair(self):
        self.status("Pairing…")
        a, c = self.pair_addr.text(), self.pair_code.text()
        run_bg(lambda: adbcap.pair(a, c), lambda r: (self.phone_msg.setText(r[1]), self.status(r[1].splitlines()[0] if r[1] else "")))

    def phone_refresh(self):
        devs = adbcap.devices()
        self.dev.clear()
        if devs is None:
            self.phone_msg.setText("adb is not installed.\nUbuntu: sudo apt install adb\nmacOS: brew install android-platform-tools\n"
                                   "(You can still open pictures from disk.)")
            self.cap_btn.setEnabled(False)
            return
        self.cap_btn.setEnabled(True)
        good = [d for d, s in devs if s == "device"]
        bad = [f"{d} ({s})" for d, s in devs if s != "device"]
        self.dev.addItems(good)
        extra = f"\nNot ready: {', '.join(bad)}" if bad else ""
        self.phone_msg.setText(f"{len(good)} device(s) found." + extra if good else
                               "No device found. Enable USB debugging, plug it in and accept the prompt on the phone." + extra)

    def adb_capture(self):
        dev = self.dev.currentText() or None
        self.cap_btn.setEnabled(False)
        self.status("Capturing…")

        def done(data):
            self.cap_btn.setEnabled(True)
            out = os.path.join(self.dirs["screenshots"], time.strftime("screenshot_%Y%m%d_%H%M%S.png"))
            with open(out, "wb") as f:
                f.write(data)
            self.open_path(out)
            self.status(f"Saved {out}")

        def err(e):
            self.cap_btn.setEnabled(True)
            QMessageBox.critical(self, "Capture failed", str(e))
        run_bg(lambda: adbcap.screencap_png(dev), done, err)

    # ---- Crop
    def _p_crop(self):
        w, v = self._page()
        v.addWidget(note("Drag a rectangle around the icon or button to cut a template. Optionally pick the tap point to get offset_x / offset_y."))
        self._head(v, "What the mouse does")
        self.crop_modes = {}
        grp = QButtonGroup(w)
        for key, text in (("select", "Drag: select area"), ("click", "Click: set tap point"), ("pan", "Drag: move (wheel zooms)")):
            r = QRadioButton(text)
            r.toggled.connect(lambda on, k=key: on and self.set_mode(k))
            grp.addButton(r)
            self.crop_modes[key] = r
            v.addWidget(r)
        self.crop_modes["select"].setChecked(True)
        self._head(v, "Selection")
        self.sel_lbl = QLabel("—")
        v.addWidget(self.sel_lbl)
        self._head(v, "Template name")
        self.tpl_name = QLineEdit("icon")
        v.addWidget(self.tpl_name)
        v.addWidget(big("Save template", self.crop_save, True))
        self._head(v, "Tap point → offsets")
        self.tap_lbl = QLabel("Switch to “Click = set tap point” and click\nwhere the bot should tap.")
        v.addWidget(self.tap_lbl)
        v.addWidget(big("Copy snippet", self.crop_copy))
        v.addStretch(1)
        return w

    def crop_offsets(self, d):
        x0, y0, x1, y1 = d.sel
        return d.tap[0] - (x0 + (x1 - x0) // 2), d.tap[1] - (y0 + (y1 - y0) // 2)

    def crop_update(self):
        d = self.cur()
        if not d:
            return
        if d.sel:
            x0, y0, x1, y1 = d.sel
            w, h = x1 - x0, y1 - y0
            self.sel_lbl.setText(f"x={x0}  y={y0}  w={w}  h={h}\ncentre = ({x0 + w // 2}, {y0 + h // 2})")
        else:
            self.sel_lbl.setText("—")
        if d.tap and d.sel:
            ox, oy = self.crop_offsets(d)
            self.tap_lbl.setText(f"tap = ({d.tap[0]}, {d.tap[1]})\noffset_x = {ox}\noffset_y = {oy}\n(tap = matched centre + offset)")
        elif d.tap:
            self.tap_lbl.setText(f"tap = ({d.tap[0]}, {d.tap[1]})\nSelect the template area too to get offsets.")
        else:
            self.tap_lbl.setText("Switch to “Click = set tap point” and click\nwhere the bot should tap.")

    def crop_save(self):
        d = self.cur()
        if not d or not d.sel:
            QMessageBox.information(self, APP_NAME, "Open a picture and drag a rectangle first.")
            return
        name = self.tpl_name.text().strip() or "icon"
        if not name.lower().endswith(".png"):
            name += ".png"
        out = os.path.join(self.dirs["templates"], name)
        if os.path.exists(out) and QMessageBox.question(self, "Overwrite?", f"{name} already exists. Replace it?") != QMessageBox.Yes:
            return
        d.pil.convert("RGB").crop(d.sel).save(out)
        self.last_template = out
        self.reload_templates()
        self.tpl_combo.setCurrentText(out)
        self.status(f"Saved template {out}")

    def crop_copy(self):
        d = self.cur()
        if not d or not d.sel:
            return
        x0, y0, x1, y1 = d.sel
        txt = f"x={x0}, y={y0}, w={x1 - x0}, h={y1 - y0}"
        if d.tap:
            ox, oy = self.crop_offsets(d)
            txt += f", offset_x={ox}, offset_y={oy}"
        QGuiApplication.clipboard().setText(txt)
        self.status("Copied: " + txt)

    # ---- Verify
    def _p_verify(self):
        from ..core import matching  # noqa: F401
        w, v = self._page()
        v.addWidget(note("Checks that a template is found in a screenshot, exactly like the bot does it."))
        self._head(v, "Template")
        self.tpl_combo = QComboBox()
        self.tpl_combo.setEditable(True)
        v.addWidget(self.tpl_combo)
        v.addWidget(hbox(big("Browse…", self.verify_browse), big("Reload list", self.reload_templates)))
        self._head(v, "Threshold")
        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(50, 100)
        self.thr.setValue(90)
        self.thr_lbl = QLabel("0.90")
        self.thr.valueChanged.connect(lambda x: (self.thr_lbl.setText(f"{x / 100:.2f}"), self.verify_show()))
        v.addWidget(hbox(self.thr, self.thr_lbl))
        self._head(v, "Tap point (optional)")
        self.tap_chk = QCheckBox("Set tap point (click)")
        self.tap_chk.toggled.connect(lambda on: self.set_mode("click" if on else "pan"))
        v.addWidget(self.tap_chk)
        self.vtap_lbl = note("Tick it, click where the bot should tap, then press Find template.")
        v.addWidget(self.vtap_lbl)
        v.addWidget(big("Clear tap point", self.verify_clear_tap))
        f = QFormLayout()
        self.ox, self.oy = QSpinBox(), QSpinBox()
        for s in (self.ox, self.oy):
            s.setRange(-2000, 2000)
        f.addRow("offset_x", self.ox)
        f.addRow("offset_y", self.oy)
        v.addLayout(f)
        v.addWidget(big("Find template", self.verify_run, True))
        v.addWidget(big("Save result picture…", self.verify_save))
        v.addWidget(big("Copy offsets", self.verify_copy))
        v.addStretch(1)
        self.reload_templates()
        return w

    def reload_templates(self):
        d = self.dirs["templates"]
        cur = self.tpl_combo.currentText()
        names = sorted(f for f in os.listdir(d) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")))
        self.tpl_combo.clear()
        self.tpl_combo.addItems([os.path.join(d, n) for n in names])
        self.tpl_combo.setCurrentText(cur or (self.last_template or ""))

    def verify_browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Template", self.dirs["templates"], IMG_FILTER)
        if p:
            self.tpl_combo.setCurrentText(p)

    def verify_offsets(self, d):
        r = d.match
        if r and d.tap:
            return d.tap[0] - r["x"], d.tap[1] - r["y"]
        return self.ox.value(), self.oy.value()

    def verify_run(self):
        from ..core import matching
        d = self.cur()
        if not d or not self.tpl_combo.currentText():
            QMessageBox.information(self, APP_NAME, "Open a screenshot and choose a template first.")
            return
        try:
            scr = matching.read_bgr(d.path)
            tmpl = matching.read_image_unchanged(self.tpl_combo.currentText())
            d.match = matching.best_match(scr, tmpl)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME, str(e))
            return
        if d.match is None:
            self.result_lbl.setText("<span style='color:#D33'>Template is larger than the screenshot.</span>")
        self.verify_show()

    def verify_clear_tap(self):
        d = self.cur()
        if d:
            d.tap = None
        self.ox.setValue(0)
        self.oy.setValue(0)
        self.vtap_lbl.setText("Tick it, click where the bot should tap, then press Find template.")
        self.refresh_overlays()

    def verify_show(self):
        d = self.cur()
        if not d or self.tool != "verify":
            return
        self.refresh_overlays()
        r = d.match
        if not r:
            return
        thr = self.thr.value() / 100
        ok = r["score"] >= thr
        col = "#2E9D4F" if ok else "#D33"
        ox, oy = self.verify_offsets(d)
        if d.tap:
            self.ox.setValue(ox)
            self.oy.setValue(oy)
            tx, ty = d.tap
        else:
            tx, ty = r["x"] + ox, r["y"] + oy
        txt = (f"<b style='color:{col}'>{'FOUND' if ok else 'NOT FOUND'}</b> &nbsp; match {r['score']:.4f} (threshold {thr:.2f})<br>"
               f"centre ({r['x']}, {r['y']}) &nbsp; top-left ({r['left']}, {r['top']})")
        if d.tap or ox or oy:
            txt += f"<br>tap point ({tx}, {ty})<br>offset_x: {ox:+d} &nbsp; offset_y: {oy:+d}"
        if not ok:
            txt += "<br><br>Low score on a flat-coloured icon? Remake the template with Remove background and “Grow edge = 1”."
        self.result_lbl.setText(txt)

    def verify_save(self):
        from ..core import matching
        d = self.cur()
        if not d or not d.match:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save result", os.path.join(self.dirs["output"], "verify_result.png"), "PNG (*.png)")
        if not p:
            return
        import cv2
        img = matching.read_bgr(d.path)
        r = d.match
        cv2.rectangle(img, (r["left"], r["top"]), (r["left"] + r["w"], r["top"] + r["h"]), (0, 0, 255), 2)
        cv2.imwrite(p, img)
        self.status(f"Saved {p}")

    def verify_copy(self):
        d = self.cur()
        if not d or not d.match:
            return
        ox, oy = self.verify_offsets(d)
        txt = f"offset_x={ox}, offset_y={oy}"
        QGuiApplication.clipboard().setText(txt)
        self.status("Copied: " + txt)

    # ---- Background
    def _p_bg(self):
        from ..core import bgremove
        w, v = self._page()
        v.addWidget(note("Makes the background transparent while keeping every icon pixel exactly as it was."))
        self._head(v, "Method")
        self.bg_method = "auto"
        grp = QButtonGroup(w)
        for key, text in (("auto", "Auto (from corners)"), ("manual", "Manual (click it)"),
                          ("ai", "AI (photos, complex)")):
            r = QRadioButton(text)
            r.setChecked(key == "auto")
            r.toggled.connect(lambda on, k=key: on and self.bg_set_method(k))
            grp.addButton(r)
            v.addWidget(r)
        f = QFormLayout()
        self.tol = QSpinBox(); self.tol.setRange(0, 255); self.tol.setValue(30)
        self.edge = QSpinBox(); self.edge.setRange(0, 255); self.edge.setValue(200)
        self.erode = QSpinBox(); self.erode.setRange(0, 10); self.erode.setValue(2)
        self.grow = QSpinBox(); self.grow.setRange(0, 5)
        self.grow.setToolTip("Keeps a thin ring of edge pixels. Fixes low match scores for flat single-colour icons (use 1).")
        self.tol_row = (QLabel("Tolerance"), self.tol)
        f.addRow(*self.tol_row)
        self.edge_row = (QLabel("AI edge threshold"), self.edge)
        f.addRow(*self.edge_row)
        self.erode_row = (QLabel("AI erode (px)"), self.erode)
        f.addRow(*self.erode_row)
        f.addRow("Grow edge (px)", self.grow)
        v.addLayout(f)
        self.ai_box = QWidget()
        av = QVBoxLayout(self.ai_box)
        av.setContentsMargins(0, 0, 0, 0)
        self.model = QComboBox()
        self.model.addItems([l for _k, l in bgremove.AI_MODELS])
        self.model.setCurrentIndex(1)
        av.addWidget(QLabel("AI model"))
        av.addWidget(self.model)
        self.soft = QCheckBox("Soft edge (photos)")
        self.largest = QCheckBox("Keep only the biggest object")
        av.addWidget(self.soft)
        av.addWidget(self.largest)
        v.addWidget(self.ai_box)
        self.bg_apply_btn = big("Apply", self.bg_apply, True)
        v.addWidget(self.bg_apply_btn)
        self.bg_undo_btn = big("Undo last click", self.bg_undo)
        v.addWidget(self.bg_undo_btn)
        self.orig_chk = QCheckBox("Show original")
        self.orig_chk.toggled.connect(self.bg_toggle_orig)
        v.addWidget(self.orig_chk)
        self._head(v, "Save")
        v.addWidget(big("Save as template (PNG)", self.bg_save_template))
        v.addWidget(big("Save as…", self.bg_save_as))
        v.addStretch(1)
        self.bg_set_method("auto")
        return w

    def bg_set_method(self, m):
        self.bg_method = m
        for lab, wd in (self.tol_row, self.edge_row, self.erode_row):
            pass
        self.tol.setVisible(m in ("auto", "manual")); self.tol_row[0].setVisible(m in ("auto", "manual"))
        for row in (self.edge_row, self.erode_row):
            row[0].setVisible(m == "ai"); row[1].setVisible(m == "ai")
        self.ai_box.setVisible(m == "ai")
        self.bg_undo_btn.setVisible(m == "manual")
        self.bg_apply_btn.setText("Reset clicks" if m == "manual" else "Apply")
        if self.tool == "bg":
            self.set_mode("click" if m == "manual" else "pan")

    def bg_prepare(self, d):
        from ..core import matching
        if d.bg_src is None:
            d.bg_src = matching.read_bgr(d.path)

    def bg_apply(self):
        from ..core import bgremove
        import numpy as np
        d = self.cur()
        if not d:
            QMessageBox.information(self, APP_NAME, "Open a picture first.")
            return
        self.bg_prepare(d)
        m = self.bg_method
        if m == "manual":
            d.bg_points = []
            self._bg_manual(d)
            return
        if m == "auto":
            res = bgremove.flood_fill_transparent(d.bg_src, self.tol.value())
            self._bg_show(d, bgremove.grow_mask(res, self.grow.value()))
            return
        if not bgremove.ai_available():
            QMessageBox.warning(self, "AI not installed", "The AI engine (rembg) is not installed.\nInstall it on the Setup page (Install AI background removal).")
            return
        self.bg_apply_btn.setEnabled(False)
        self.status("AI is working… (the first run of each model downloads it, 5-170 MB)", 0)
        src, e, er = d.bg_src, self.edge.value(), self.erode.value()
        label = self.model.currentText()
        mkey = next((k for k, l in bgremove.AI_MODELS if l == label), "u2net")
        soft, big_ = self.soft.isChecked(), self.largest.isChecked()

        def done(res):
            self.bg_apply_btn.setEnabled(True)
            self._bg_show(d, bgremove.grow_mask(res, self.grow.value()))
            self.status("AI done")

        def err(ex):
            self.bg_apply_btn.setEnabled(True)
            QMessageBox.critical(self, "AI failed", str(ex))
        run_bg(lambda: bgremove.ai_remove_bg(src, e, er, mkey, soft, big_), done, err)

    def _bg_manual(self, d):
        from ..core import bgremove
        import numpy as np
        bgra = np.dstack([d.bg_src, np.full(d.bg_src.shape[:2], 255, np.uint8)])
        res = bgremove.click_remove(bgra, d.bg_points, self.tol.value())
        self._bg_show(d, bgremove.grow_mask(res, self.grow.value()))

    def bg_undo(self):
        d = self.cur()
        if d and d.bg_points:
            d.bg_points.pop()
            self._bg_manual(d)

    def _bg_show(self, d, bgra):
        from ..core import bgremove
        d.bg_result = bgra
        self.orig_chk.blockSignals(True)
        self.orig_chk.setChecked(False)
        self.orig_chk.blockSignals(False)
        self.show_doc(d, keep_view=True)
        n, total, std, warn = bgremove.quality_report(bgra)
        txt = f"Kept {n}/{total} px ({100 * n / total:.0f}%), colour variation {std:.0f}"
        if warn:
            txt += f"<br><span style='color:#D68A00'>⚠ {warn}</span>"
        self.result_lbl.setText(txt)

    def bg_toggle_orig(self, on):
        d = self.cur()
        if d:
            d.show_orig = on
            self.show_doc(d, keep_view=True)

    def _bg_click(self, x, y):
        d = self.cur()
        if d and self.bg_method == "manual":
            self.bg_prepare(d)
            d.bg_points.append((x, y))
            self._bg_manual(d)

    def bg_save_template(self):
        import cv2
        d = self.cur()
        if not d or d.bg_result is None:
            QMessageBox.information(self, APP_NAME, "Nothing to save yet – press Apply first.")
            return
        out = os.path.join(self.dirs["templates"], os.path.splitext(d.name)[0] + "_nobg.png")
        cv2.imwrite(out, d.bg_result)
        self.last_template = out
        self.reload_templates()
        self.status(f"Saved {out}")

    def bg_save_as(self):
        import cv2
        d = self.cur()
        if not d or d.bg_result is None:
            QMessageBox.information(self, APP_NAME, "Nothing to save yet – press Apply first.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save as", os.path.join(self.dirs["output"], os.path.splitext(d.name)[0] + "_nobg.png"), "PNG (*.png)")
        if p:
            cv2.imwrite(p, d.bg_result)
            self.status(f"Saved {p}")

    # ---- Setup
    def _p_setup(self):
        w, v = self._page()
        v.addWidget(note("Check which parts are installed, install what is missing, and choose where your files are kept."))
        v.addStretch(1)
        return w

    # ------------------------------------------------------------ tools / modes
    def pick_tool(self, key):
        self.tool = key
        self.tools.select(key)
        self.opts.setCurrentIndex(self.opt_index[key])
        self.pages.setCurrentIndex(1 if key == "setup" else 0)
        title = next(t for k, t, _ in TOOLS if k == key)
        self.tool_title.setText(title)
        for b in list(self.mode_btns.values()) + self.view_btns:
            b.setVisible(key != "setup")
        self.zoom_lbl.setVisible(key != "setup")
        self.result_lbl.setText("")
        mode = {"crop": "select", "bg": "click" if self.bg_method == "manual" else "pan"}.get(key, "pan")
        if key == "crop":
            mode = next((k for k, r in self.crop_modes.items() if r.isChecked()), "select")
        if key == "verify" and self.tap_chk.isChecked():
            mode = "click"
        self.set_mode(mode)
        d = self.cur()
        if key == "bg" and d and d.bg_result is None and self.bg_method == "auto":
            self.bg_apply()
        self.show_doc(d, keep_view=True) if d else None
        self.refresh_overlays()
        if key == "crop":
            self.crop_update()
        if key == "verify":
            self.verify_show()

    def set_mode(self, mode):
        self.view.set_mode(mode)
        if mode in self.mode_btns:
            self.mode_btns[mode].setChecked(True)
        if self.tool == "crop" and mode in getattr(self, "crop_modes", {}):
            self.crop_modes[mode].setChecked(True)

    # ------------------------------------------------------------ pictures
    def cur(self):
        i = self.tabs.currentIndex()
        return self.docs[i] if 0 <= i < len(self.docs) else None

    def open_dialog(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Open picture", self.dirs["screenshots"], IMG_FILTER)
        for p in ps:
            self.open_path(p)

    def open_path(self, path):
        from PIL import Image
        try:
            pil = Image.open(path)
            pil.load()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Cannot open this picture:\n{e}")
            return
        d = Doc(path, pil)
        self.docs.append(d)
        i = self.tabs.addTab(d.name)
        self.tabs.setTabToolTip(i, path)
        self.tabs.setCurrentIndex(i)
        self.current_screenshot = path

    def close_doc(self, i):
        if 0 <= i < len(self.docs):
            self.docs.pop(i)
            self.tabs.removeTab(i)
            if not self.docs:
                self.view.set_image(None)
                self.view.qimg = None
                self.info_lbl.setText("No picture open.")
                self.view.update()

    def _tab_changed(self, i):
        d = self.cur()
        if not d:
            return
        self.current_screenshot = d.path
        self.show_doc(d)
        if self.tool == "bg" and d.bg_result is None and self.bg_method == "auto":
            self.bg_apply()
        self.crop_update()
        self.refresh_overlays()
        self.result_lbl.setText("")
        if self.tool == "verify":
            self.verify_show()

    def show_doc(self, d, keep_view=False):
        if d is not self.cur():
            return
        if self.tool == "bg" and d.bg_result is not None and not d.show_orig:
            img = pil_from_bgr(d.bg_result)
        else:
            img = d.pil
        self.view.set_image(img, keep_view=keep_view)
        w, h = d.pil.size
        self.info_lbl.setText(f"<b>{d.name}</b><br>{w} × {h} pixels<br><span style='color:#6B6075'>{d.path}</span>")
        self.refresh_overlays()

    def refresh_overlays(self):
        d = self.cur()
        ov = []
        if d and self.tool == "crop":
            if d.sel:
                x0, y0, x1, y1 = d.sel
                ov.append(dict(kind="cross", x=x0 + (x1 - x0) // 2, y=y0 + (y1 - y0) // 2, color="#00E5FF", label="centre"))
            if d.tap:
                ov.append(dict(kind="dot", x=d.tap[0], y=d.tap[1], color="#FF3B30", label="tap"))
        elif d and self.tool == "verify":
            r = d.match
            if r:
                ok = r["score"] >= self.thr.value() / 100
                col = "#2ECC71" if ok else "#FF3B30"
                ov.append(dict(kind="rect", x0=r["left"], y0=r["top"], x1=r["left"] + r["w"], y1=r["top"] + r["h"], color=col, label=f"{r['score']:.3f}"))
                ov.append(dict(kind="cross", x=r["x"], y=r["y"], color=col, r=6))
                ox, oy = self.verify_offsets(d)
                if d.tap:
                    ov.append(dict(kind="dot", x=d.tap[0], y=d.tap[1], color="#FF3B30", label="tap"))
                elif ox or oy:
                    ov.append(dict(kind="dot", x=r["x"] + ox, y=r["y"] + oy, color="#FFCC00", label="tap"))
            elif d.tap:
                ov.append(dict(kind="dot", x=d.tap[0], y=d.tap[1], color="#FF3B30", label="tap"))
        self.view.overlays = ov
        if d and self.tool == "crop" and d.sel:
            self.view.selection = d.sel
        elif self.tool != "crop":
            self.view.selection = None
        self.view.update()

    def _selected(self, x0, y0, x1, y1):
        d = self.cur()
        if d and self.tool == "crop":
            d.sel = (x0, y0, x1, y1)
            self.crop_update()
            self.refresh_overlays()

    def _clicked(self, x, y):
        d = self.cur()
        if not d:
            return
        if self.tool == "crop":
            d.tap = (x, y)
            self.crop_update()
            self.refresh_overlays()
        elif self.tool == "verify":
            d.tap = (x, y)
            self.vtap_lbl.setText(f"tap point ({x}, {y}) – press Find template to get the offsets.")
            if d.match:
                self.verify_show()
            else:
                self.refresh_overlays()
        elif self.tool == "bg":
            self._bg_click(x, y)

    def _moved(self, x, y, px):
        self.cursor_lbl.setText("" if x is None else f"x={x}  y={y}   RGBA={tuple(px)}")

    def save_copy(self):
        d = self.cur()
        if not d:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save a copy", os.path.join(self.dirs["output"], os.path.splitext(d.name)[0] + ".png"),
                                           "PNG (*.png);;JPEG (*.jpg)")
        if p:
            d.pil.convert("RGB" if p.lower().endswith((".jpg", ".jpeg")) else "RGBA").save(p)
            self.status(f"Saved {p}")

    # ------------------------------------------------------------ drag & drop, closing
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.open_path(u.toLocalFile())

    def closeEvent(self, e):
        try:
            self.stack.save()
            adbcap.shutdown()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)
        ui.force_exit_soon()


def main():
    ui.mac_app_name(APP_NAME)
    app = QApplication(sys.argv)
    ui.setup_app(app, APP_NAME, SLUG, ui.app_icon(HERE, SLUG), theme=True)
    w = Main()
    w.show()
    for a in sys.argv[1:]:
        if os.path.isfile(a):
            w.open_path(a)
    os._exit(app.exec())
