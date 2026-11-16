"""Drag a rectangle on a frozen screenshot (used when the system has no area-capture helper)."""
from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .view import qimage_from_pil


class AreaPicker(QWidget):
    def __init__(self, shot):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.shot = shot
        self.qi = qimage_from_pil(shot)
        self.a = self.b = None
        self.result = None
        self.loop = QEventLoop()
        self.setCursor(Qt.CrossCursor)

    def run(self):
        scr = QGuiApplication.primaryScreen()
        self.setGeometry(scr.geometry())
        self.showFullScreen()
        self.activateWindow()
        self.loop.exec()
        return self.result

    def _rect(self):
        return QRect(self.a, self.b).normalized() if self.a and self.b else None

    def paintEvent(self, _e):
        p = QPainter(self)
        p.drawImage(self.rect(), self.qi)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        r = self._rect()
        if r:
            p.drawImage(r, self.qi, self._src(r))
            p.setPen(QPen(QColor("#00E5FF"), 2))
            p.drawRect(r)
            p.setPen(Qt.white)
            f = self.qi.width() / max(1, self.width())
            p.drawText(r.topLeft() + QPoint(4, -6), f"{int(r.width() * f)} x {int(r.height() * f)}")
        else:
            p.setPen(Qt.white)
            p.drawText(self.rect().adjusted(0, 24, 0, 0), Qt.AlignHCenter | Qt.AlignTop, "Drag the area to capture - Esc cancels")

    def _src(self, r):
        f = self.qi.width() / max(1, self.width())
        return QRect(int(r.x() * f), int(r.y() * f), int(r.width() * f), int(r.height() * f))

    def mousePressEvent(self, e):
        self.a = self.b = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.a:
            self.b = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        r = self._rect()
        if r and r.width() > 3 and r.height() > 3:
            s = self._src(r)
            self.result = self.shot.crop((s.x(), s.y(), s.x() + s.width(), s.y() + s.height()))
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, e):
        self.loop.quit()
        super().closeEvent(e)


def pick_area(_parent, shot):
    return AreaPicker(shot).run()
