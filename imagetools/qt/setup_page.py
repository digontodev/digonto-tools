import platform
import sys

from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..core import deps
from .common import run_bg


class SetupPage(QScrollArea):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        w = QWidget()
        self.setWidget(w)
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 14, 18, 14)
        h = QLabel("<h3>Components</h3>")
        v.addWidget(h)
        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(12)
        v.addLayout(self.rows)
        bar = QHBoxLayout()
        b = QPushButton("Re-check")
        b.clicked.connect(self.refresh)
        self.all_btn = QPushButton("Install everything that is missing")
        self.all_btn.clicked.connect(self.install_all)
        self.ai_btn = QPushButton("Install AI background removal")
        self.ai_btn.clicked.connect(lambda: self.install(True))
        for x in (b, self.all_btn, self.ai_btn):
            bar.addWidget(x)
        bar.addStretch()
        v.addLayout(bar)
        note = QLabel("Anything already installed is skipped.")
        note.setStyleSheet("color:#777")
        v.addWidget(note)
        v.addWidget(QLabel("<h3>Folders</h3>"))
        self.dir_lbl = QLabel()
        self.dir_lbl.setTextInteractionFlags(Qt_Sel)
        v.addWidget(self.dir_lbl)
        r2 = QHBoxLayout()
        o = QPushButton("Open working folder")
        o.clicked.connect(main.open_workdir)
        c = QPushButton("Change…")
        c.clicked.connect(main.change_workdir)
        r2.addWidget(o)
        r2.addWidget(c)
        r2.addStretch()
        v.addLayout(r2)
        self.sys_lbl = QLabel()
        self.sys_lbl.setStyleSheet("color:#777")
        self.sys_lbl.setWordWrap(True)
        v.addWidget(self.sys_lbl)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        v.addWidget(self.log, 1)
        self.refresh()

    def write(self, s):
        self.log.appendPlainText(s)

    def _row(self, i, ok, req, name, why):
        tag = "✔" if ok else ("✘" if req else "–")
        col = "#2E9D4F" if ok else ("#D33" if req else "#999")
        t = QLabel(f"<b style='color:{col}; font-size:15px'>{tag}</b>")
        self.rows.addWidget(t, i, 0)
        self.rows.addWidget(QLabel(name), i, 1)
        l = QLabel(why)
        l.setStyleSheet("color:#777")
        self.rows.addWidget(l, i, 2)
        self.rows.setColumnStretch(2, 1)

    def refresh(self):
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        i = 0
        for mod, pipname, req, why, ok in deps.status():
            self._row(i, ok, req, pipname, why + ("" if req else "  (optional)"))
            i += 1
        for key, label, why, ok, needed in deps.system_status():
            self._row(i, ok, needed, label, why)
            i += 1
        try:
            from ..core import fonts
            self._row(i, True, False, "fonts", f"{len(fonts.families())} font families found on this computer")
        except Exception:  # noqa: BLE001
            pass
        self.dir_lbl.setText("\n".join(f"{k}: {v}" for k, v in self.main.dirs.items()))
        self.sys_lbl.setText(f"Python {platform.python_version()} · {platform.system()} {platform.machine()} · {sys.executable}")

    def install(self, ai):
        todo = [(m, p) for m, p, req, why, ok in deps.status() if not ok and ((ai and m == "rembg") or (not ai and req))]
        if not todo:
            self.write("Nothing to install.")
            return
        if ai and not deps.ai_supported():
            self.write("AI background removal is not available on Intel Macs (no builds published). Auto and Manual background removal still work.")
            return
        self.ai_btn.setEnabled(False)
        lines = []

        self.write("Installing from the locked list (exact versions, SHA-256 checked): " + ", ".join(p for _, p in todo) + " … (can take a few minutes)")

        def work2():
            ok = deps.install_locked(ai, lines.append)
            return [(p, ok) for _, p in todo]

        def done(res):
            for line in lines:
                self.write(line)
            for p, ok in res:
                self.write(f"{'OK    ' if ok else 'FAILED'} {p}")
            self.ai_btn.setEnabled(True)
            self.refresh()
        run_bg(work2, done, lambda e: (self.write(f"Error: {e}"), self.ai_btn.setEnabled(True)))

    def install_all(self):
        self.all_btn.setEnabled(False)
        pk_missing = [p for m, p, req, why, ok in deps.status() if not ok and req]
        sysm = {k: (ok, needed) for k, _l, _w, ok, needed in deps.system_status()}
        lines = []

        def work():
            res = []
            if pk_missing:
                res.append(("Python packages", deps.install_locked(False, lines.append)))
            if not sysm["adb"][0]:
                res.append(("adb", deps.install_adb(lines.append)))
            if sys.platform.startswith("linux"):
                bad = [k for k in ("shot", "xwininfo") if k in sysm and not sysm[k][0] and sysm[k][1]]
                if bad:
                    res.append(("screenshot helper", deps.install_linux_tools(lines.append)))
            return res

        def done(res):
            for line in lines:
                self.write(line)
            if not res:
                self.write("Everything is already installed - nothing to do.")
            for n, ok in res:
                self.write(f"{'OK    ' if ok else 'FAILED'} {n}")
            self.all_btn.setEnabled(True)
            self.refresh()
        self.write("Checking what is missing …")
        run_bg(work, done, lambda e: (self.write(f"Error: {e}"), self.all_btn.setEnabled(True)))


from PySide6.QtCore import Qt  # noqa: E402
Qt_Sel = Qt.TextSelectableByMouse
