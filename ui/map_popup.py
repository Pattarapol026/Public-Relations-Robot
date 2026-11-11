"""The event's site plan, as a big pop-up over the conversation and as a small picture in the chat.

The pop-up springs up over a blurred, darkened copy of the screen behind it. It goes away when it is touched anywhere, or
(``MapPopup.age_ms`` is read by the screen for this) when the visitor starts a new sentence.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QEasingCurve, QElapsedTimer, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

MAP_PATH = Path(__file__).resolve().parent / "images" / "venue_map.png"
OPEN_MS = 380
CLOSE_MS = 200
BLUR_DIVISOR = 14  # the screen is shrunk this many times and stretched back: a soft blur that costs nothing
HINT = "แตะที่ใดก็ได้เพื่อปิด  ·  Tap anywhere to close"


def load_map() -> QPixmap:
    return QPixmap(str(MAP_PATH))


def blurred(screen: QPixmap) -> QPixmap:
    """A soft, blurred copy of ``screen`` (shrunk, then stretched back with smoothing)."""
    if screen.isNull():
        return screen
    small = screen.scaled(
        max(1, screen.width() // BLUR_DIVISOR), max(1, screen.height() // BLUR_DIVISOR),
        Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
    )
    return small.scaled(screen.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


class MapPopup(QWidget):
    """Covers its parent. ``open_over(screen)`` shows it; ``closed`` is emitted when it has gone."""

    closed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._map = load_map()
        self._backdrop = QPixmap()
        self._progress = 0.0
        self._clock = QElapsedTimer()
        self._closing = False
        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.finished.connect(self._on_animation_finished)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    # animated 0 (not there) .. 1 (open); the spring-up overshoots a little past 1
    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = value
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    @property
    def is_open(self) -> bool:
        return self.isVisible() and not self._closing

    @property
    def age_ms(self) -> int:
        """How long it has been open (a visitor's first words as it opens are still about the question before)."""
        return self._clock.elapsed() if self._clock.isValid() else 0

    def open_over(self, screen: QPixmap) -> None:
        if self._map.isNull():
            return
        self._backdrop = blurred(screen)
        self._closing = False
        self._clock.start()
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self._animate(self._progress if self.isVisible() and self._progress > 0 else 0.0, 1.0, OPEN_MS,
                      QEasingCurve.Type.OutBack)

    def close_popup(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._animate(min(self._progress, 1.0), 0.0, CLOSE_MS, QEasingCurve.Type.InCubic)

    def hide_now(self) -> None:
        """Gone at once, without the animation (the conversation was reset)."""
        self._animation.stop()
        self._closing = False
        self._progress = 0.0
        self.hide()

    def _animate(self, start: float, end: float, duration: int, curve: QEasingCurve.Type) -> None:
        self._animation.stop()
        self._animation.setDuration(duration)
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.setEasingCurve(curve)
        self._animation.start()

    def _on_animation_finished(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()
            self.closed.emit()

    def mousePressEvent(self, event) -> None:
        self.close_popup()
        event.accept()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        fade = max(0.0, min(1.0, self._progress))
        painter.setOpacity(fade)
        if not self._backdrop.isNull():
            painter.drawPixmap(self.rect(), self._backdrop)
        painter.fillRect(self.rect(), QColor(6, 12, 24, 170))  # darkened, so the plan stands out

        margin_x, margin_top, margin_bottom = self.width() * 0.04, self.height() * 0.05, self.height() * 0.09
        room_w = self.width() - 2 * margin_x
        room_h = self.height() - margin_top - margin_bottom
        ratio = self._map.width() / self._map.height()
        width = min(room_w, room_h * ratio)
        height = width / ratio
        scale = 0.80 + 0.20 * self._progress  # springs up from a little smaller, past full size, and settles
        width, height = width * scale, height * scale
        x = (self.width() - width) / 2
        y = margin_top + (room_h - height) / 2
        frame = QRectF(x, y, width, height)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120))  # a soft shadow
        painter.drawRoundedRect(frame.translated(0, 8).adjusted(-4, -4, 4, 4), 20, 20)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 16, 16)
        painter.save()
        painter.setClipPath(clip)
        painter.drawPixmap(frame.toRect(), self._map)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 230), 3))
        painter.drawRoundedRect(frame, 16, 16)

        font = QFont(painter.font())
        font.setPointSize(max(11, int(self.height() * 0.022)))
        painter.setFont(font)
        painter.setPen(QColor(235, 242, 255))
        painter.drawText(
            QRectF(0, self.height() - margin_bottom, self.width(), margin_bottom),
            Qt.AlignmentFlag.AlignCenter, HINT,
        )


class MapThumbnail(QLabel):
    """The small picture under the answer: touching it opens the pop-up again."""

    clicked = Signal()

    def __init__(self, pixmap: QPixmap, width: int = 280) -> None:
        super().__init__()
        self.setPixmap(pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("border: 2px solid #5b9bd5; border-radius: 10px; background: #0d1b2e; padding: 2px;")
        self.setToolTip("แตะเพื่อดูแผนผังขยาย")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        event.accept()
