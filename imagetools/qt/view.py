"""Fast zoom / pan / select view for pictures (QPainter, no per-frame image copies)."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


def qimage_from_pil(img):
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qi = QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888)
    return qi.copy()           # own the memory


class ImageView(QWidget):
    selected = Signal(int, int, int, int)       # x0, y0, x1, y1 (picture pixels)
    clicked = Signal(int, int)
    moved = Signal(object, object, object)      # x, y, (r,g,b,a) or None
    zoomChanged = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(200, 160)
        self.setFocusPolicy(Qt.StrongFocus)
        self.pil = None
        self.qimg = None
        self.zoom = 1.0
        self.ox = 0.0                   # picture point at the view's top-left corner
        self.oy = 0.0
        self.mode = "pan"               # pan | select | click
        self.overlays = []
        self.selection = None
        self._drag = None
        self._pan = None
        self._fit_pending = False
        self._checker = self._make_checker()

    @staticmethod
    def _make_checker():
        pm = QPixmap(16, 16)
        pm.fill(QColor("#FFFFFF"))
        p = QPainter(pm)
        p.fillRect(0, 0, 8, 8, QColor("#D9D9D9"))
        p.fillRect(8, 8, 8, 8, QColor("#D9D9D9"))
        p.end()
        return QBrush(pm)

    # ---- API
    def set_image(self, pil, keep_view=False):
        self.pil = pil.convert("RGBA") if pil is not None else None
        self.qimg = qimage_from_pil(self.pil) if self.pil is not None else None
        self.selection = None
        if not keep_view:
            self.overlays = []
            self._fit_pending = True
            self.fit()
        self.update()

    def clear_overlays(self):
        self.overlays = []
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.setCursor(Qt.CrossCursor if mode in ("select", "click") else Qt.ArrowCursor)

    def fit(self):
        if self.qimg is None or self.width() < 20:
            return
        z = min(self.width() / self.qimg.width(), self.height() / self.qimg.height(), 1.0 if self.qimg.width() > self.width() else 8.0)
        z = min(z * 0.98, 64.0)
        self._fit_pending = False
        self.zoom = max(z, 0.02)
        self.ox = (self.qimg.width() - self.width() / self.zoom) / 2
        self.oy = (self.qimg.height() - self.height() / self.zoom) / 2
        self.zoomChanged.emit(self.zoom)
        self.update()

    def actual_size(self):
        if self.qimg is None:
            return
        cx = self.ox + self.width() / 2 / self.zoom
        cy = self.oy + self.height() / 2 / self.zoom
        self.zoom = 1.0
        self.ox, self.oy = cx - self.width() / 2, cy - self.height() / 2
        self.zoomChanged.emit(self.zoom)
        self.update()

    def to_img(self, p):
        return self.ox + p.x() / self.zoom, self.oy + p.y() / self.zoom

    def to_view(self, x, y):
        return QPointF((x - self.ox) * self.zoom, (y - self.oy) * self.zoom)

    # ---- painting
    def resizeEvent(self, e):
        if self._fit_pending or (self.qimg is not None and self.zoom == 0):
            self.fit()
        super().resizeEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#2B2B2B"))
        if self.qimg is None:
            p.setPen(QColor("#9A8FA8"))
            p.drawText(self.rect(), Qt.AlignCenter, "Capture a screenshot or open a picture\n(Ctrl+O, or drop a file here)")
            return
        z = self.zoom
        dest = QRectF(-self.ox * z, -self.oy * z, self.qimg.width() * z, self.qimg.height() * z)
        if self._has_alpha():
            p.fillRect(dest.intersected(QRectF(self.rect())), self._checker)
        p.setRenderHint(QPainter.SmoothPixmapTransform, z < 1.0)
        p.drawImage(dest, self.qimg)
        self._paint_overlays(p)

    def _has_alpha(self):
        if not hasattr(self, "_alpha_cache") or self._alpha_cache[0] is not self.qimg:
            self._alpha_cache = (self.qimg, self.qimg.hasAlphaChannel() and self.pil.getchannel("A").getextrema()[0] < 255)
        return self._alpha_cache[1]

    def _paint_overlays(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        for o in self.overlays:
            col = QColor(o.get("color", "#00E5FF"))
            p.setPen(QPen(col, 2))
            p.setBrush(Qt.NoBrush)
            k = o["kind"]
            if k == "rect":
                a, b = self.to_view(o["x0"], o["y0"]), self.to_view(o["x1"], o["y1"])
                p.drawRect(QRectF(a, b))
                if o.get("label"):
                    p.setPen(col)
                    p.drawText(a + QPointF(2, -5), o["label"])
            elif k == "cross":
                c = self.to_view(o["x"], o["y"])
                r = o.get("r", 8)
                p.drawLine(c + QPointF(-r, 0), c + QPointF(r, 0))
                p.drawLine(c + QPointF(0, -r), c + QPointF(0, r))
                if o.get("label"):
                    p.setPen(col)
                    p.drawText(c + QPointF(r + 3, -3), o["label"])
            elif k == "dot":
                c = self.to_view(o["x"], o["y"])
                p.setBrush(col)
                p.drawEllipse(c, 5, 5)
                if o.get("label"):
                    p.setPen(col)
                    p.drawText(c + QPointF(8, -4), o["label"])
        sel = self._drag_rect() or self.selection
        if sel:
            a, b = self.to_view(sel[0], sel[1]), self.to_view(sel[2], sel[3])
            pen = QPen(QColor("#00E5FF"), 1.6, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(0, 229, 255, 40))
            p.drawRect(QRectF(a, b))

    # ---- mouse
    def _drag_rect(self):
        if not self._drag:
            return None
        (x0, y0), (x1, y1) = self._drag
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def _clampi(self, x, y):
        return (max(0, min(self.qimg.width(), x)), max(0, min(self.qimg.height(), y)))

    def mousePressEvent(self, e):
        if self.qimg is None:
            return
        self.setFocus()
        x, y = self.to_img(e.position())
        if e.button() == Qt.LeftButton and self.mode == "select":
            self._drag = [self._clampi(x, y), self._clampi(x, y)]
        elif e.button() == Qt.LeftButton and self.mode == "click":
            if 0 <= x < self.qimg.width() and 0 <= y < self.qimg.height():
                self.clicked.emit(int(x), int(y))
        else:
            self._pan = (e.position(), self.ox, self.oy)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self.qimg is None:
            return
        x, y = self.to_img(e.position())
        if self._drag:
            self._drag[1] = self._clampi(x, y)
            self.update()
        elif self._pan:
            p0, ox, oy = self._pan
            d = e.position() - p0
            self.ox, self.oy = ox - d.x() / self.zoom, oy - d.y() / self.zoom
            self.update()
        ix, iy = int(x), int(y)
        if 0 <= ix < self.qimg.width() and 0 <= iy < self.qimg.height():
            c = QColor(self.qimg.pixel(ix, iy))
            self.moved.emit(ix, iy, (c.red(), c.green(), c.blue(), c.alpha()))
        else:
            self.moved.emit(None, None, None)

    def mouseReleaseEvent(self, e):
        if self._drag:
            r = self._drag_rect()
            self._drag = None
            x0, y0, x1, y1 = (int(round(v)) for v in r)
            if x1 - x0 >= 2 and y1 - y0 >= 2:
                self.selection = (x0, y0, x1, y1)
                self.selected.emit(x0, y0, x1, y1)
            self.update()
        if self._pan:
            self._pan = None
            self.set_mode(self.mode)

    def mouseDoubleClickEvent(self, e):
        if self.mode == "pan":
            self.fit()

    def leaveEvent(self, _e):
        self.moved.emit(None, None, None)

    def wheelEvent(self, e):
        if self.qimg is None:
            return
        f = 1.25 if e.angleDelta().y() > 0 else 0.8
        z = max(0.05, min(64.0, self.zoom * f))
        pos = e.position()
        ix, iy = self.to_img(pos)
        self.zoom = z
        self.ox, self.oy = ix - pos.x() / z, iy - pos.y() / z
        self.zoomChanged.emit(z)
        self.update()
