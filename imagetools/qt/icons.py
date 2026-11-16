"""Tool icons drawn in code (same style as Digonto Images)."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

INK = QColor("#1E2B25")
ACCENT = QColor("#C2395C")
LIGHT = QColor("#DBC6EB")


def icon(key, size=32):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 32.0, size / 32.0)
    pen = QPen(INK, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if key == "screen":
        p.setBrush(LIGHT)
        p.drawRoundedRect(QRectF(4, 6, 24, 16), 2, 2)
        p.drawLine(16, 22, 16, 26)
        p.drawLine(10, 26, 22, 26)
        p.setPen(QPen(ACCENT, 1.8, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(9, 10, 10, 7))
    elif key == "phone":
        p.setBrush(LIGHT)
        p.drawRoundedRect(QRectF(9, 3, 14, 26), 3, 3)
        p.setBrush(Qt.white)
        p.drawRect(QRectF(11, 7, 10, 15))
        p.setBrush(ACCENT)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(16, 25.5), 1.4, 1.4)
    elif key == "crop":
        p.drawPolyline(QPolygonF([QPointF(9, 3), QPointF(9, 23), QPointF(29, 23)]))
        p.drawPolyline(QPolygonF([QPointF(3, 9), QPointF(23, 9), QPointF(23, 29)]))
        p.setPen(QPen(ACCENT, 2.4, Qt.SolidLine, Qt.RoundCap))
        p.drawPoint(QPointF(16, 16))
    elif key == "verify":
        p.setBrush(Qt.white)
        p.drawEllipse(QPointF(14, 14), 8, 8)
        p.setPen(QPen(INK, 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(20, 20, 28, 28)
        p.setPen(QPen(ACCENT, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(10, 14), QPointF(13, 17), QPointF(18.5, 11)]))
    elif key == "bg":
        p.setBrush(QColor("#D9D9D9"))
        p.drawRoundedRect(QRectF(4, 5, 24, 22), 2, 2)
        p.setPen(Qt.NoPen)
        p.setBrush(Qt.white)
        for x, y in ((4, 5), (12, 5), (20, 5), (8, 13), (16, 13), (24, 13), (4, 21), (12, 21), (20, 21)):
            pass
        p.setBrush(ACCENT)
        p.drawEllipse(QPointF(16, 16), 6, 6)
        p.setPen(QPen(INK, 1.8))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4, 5, 24, 22), 2, 2)
    elif key == "setup":
        p.setBrush(LIGHT)
        path = QPainterPath()
        import math
        for i in range(16):
            r = 11 if i % 2 == 0 else 8.5
            a = i * math.pi / 8
            pt = QPointF(16 + r * math.cos(a), 16 + r * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(Qt.white)
        p.drawEllipse(QPointF(16, 16), 4, 4)
    elif key == "open":
        p.setBrush(LIGHT)
        p.drawPolygon(QPolygonF([QPointF(4, 8), QPointF(13, 8), QPointF(15, 11), QPointF(28, 11), QPointF(28, 25), QPointF(4, 25)]))
    p.end()
    return QIcon(pm)
