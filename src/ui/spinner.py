"""Custom circular spinner widget."""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush

class Spinner(QWidget):
    """Circular, endlessly animated progress indicator."""

    def __init__(self, size=20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._progress = 0
        self._fading_out = False
        self._opacity = 1.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._color = QColor(100, 100, 100)
        # Don't start timer immediately - wait for show()

    def showEvent(self, event):
        """Start animation when widget is shown."""
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(50)

    def hideEvent(self, event):
        """Stop animation when widget is hidden."""
        super().hideEvent(event)
        if self._timer.isActive():
            self._timer.stop()

    def closeEvent(self, event):
        """Ensure timer is stopped when widget is closed."""
        if self._timer.isActive():
            self._timer.stop()
        super().closeEvent(event)

    def _animate(self):
        if not self._fading_out:
            self._progress += 0.05
            if self._progress >= 1.0:
                self._progress = 1.0
                self._fading_out = True
        else:
            self._opacity -= 0.05
            if self._opacity <= 0:
                self._opacity = 1.0
                self._progress = 0
                self._fading_out = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        color = QColor(self._color)
        color.setAlphaF(self._opacity)
        
        pen = QPen(color, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        
        painter.drawArc(rect, 90 * 16, int(-self._progress * 360 * 16))
