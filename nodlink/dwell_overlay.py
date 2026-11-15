"""鼠标旁的悬停进度圆环；透明、不接收点击、不抢夺窗口焦点。"""

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class DwellProgressOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(104, 78)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.progress = 0
        self.caption = ""
        self.held = False

    def display(self, position, progress, held, caption):
        self.progress = min(1, max(0, progress))
        self.held, self.caption = held, caption
        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry()
        scale = screen.devicePixelRatio()
        # 当前头控使用主屏幕物理像素；Qt 浮层使用逻辑像素。
        point = geometry.topLeft() + QPoint(round(position[0] / scale), round(position[1] / scale))
        x = min(max(geometry.left(), point.x() + 16), geometry.right() - self.width() + 1)
        y = min(max(geometry.top(), point.y() + 16), geometry.bottom() - self.height() + 1)
        self.move(x, y)
        self.show()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QRectF(8, 5, 42, 42)
        painter.setPen(QPen(QColor(30, 45, 65, 110), 5))
        painter.drawEllipse(ring)
        painter.setPen(QPen(QColor("#ed9b18" if self.held else "#2475f5"), 5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.progress >= 1:
            painter.drawEllipse(ring)
        else:
            painter.drawArc(ring, 90 * 16, -round(self.progress * 360 * 16))
        painter.setPen(QColor("#172d45"))
        painter.fillRect(0, 52, self.width(), 22, QColor(255, 255, 255, 225))
        painter.drawText(QRectF(0, 52, self.width(), 22), Qt.AlignmentFlag.AlignCenter, self.caption)
