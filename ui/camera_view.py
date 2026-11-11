"""Live camera picture with a frame and distance label around each detected person.

The picture is mirrored, so people see themselves the way they do in a mirror
and step the way they expect. It is only drawn — never saved.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.vision_controller import VisionController

CORNER_RADIUS = 20.0
COLOR_BACKDROP_TOP = QColor("#0f1b33")
COLOR_BACKDROP_BOTTOM = QColor("#0b1220")
COLOR_VISITOR = QColor("#22d3ee")  # the person the robot is attending to
COLOR_OTHER = QColor(255, 255, 255, 150)  # seen, but too far or off to the side
COLOR_LABEL_TEXT = QColor("#0b1220")


class CameraView(QWidget):
    def __init__(
        self,
        vision: VisionController,
        compact: bool = False,
        aspect: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """``aspect`` (height / width) makes the view as tall as its width asks for,
        within its minimum and maximum height; without it the view takes what it is given."""
        super().__init__(parent)
        self._vision = vision
        self._compact = compact
        self._aspect = aspect
        self._caption = ""
        self._placeholder = ""
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if aspect is not None:
            policy.setVerticalPolicy(QSizePolicy.Policy.Preferred)
            policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumSize(160, 120)
        vision.frame_changed.connect(self._on_frame_changed)

    def hasHeightForWidth(self) -> bool:
        return self._aspect is not None

    def heightForWidth(self, width: int) -> int:
        if self._aspect is None:
            return super().heightForWidth(width)
        return max(self.minimumHeight(), min(self.maximumHeight(), round(width * self._aspect)))

    def set_caption(self, text: str) -> None:
        """One line of text shown over the bottom of the picture."""
        if text != self._caption:
            self._caption = text
            self.update()

    def set_placeholder(self, text: str) -> None:
        """Shown in the middle when there is no picture."""
        if text != self._placeholder:
            self._placeholder = text
            self.update()

    def _on_frame_changed(self) -> None:
        if self.isVisible():
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, CORNER_RADIUS, CORNER_RADIUS)
        painter.setClipPath(clip)

        backdrop = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        backdrop.setColorAt(0.0, COLOR_BACKDROP_TOP)
        backdrop.setColorAt(1.0, COLOR_BACKDROP_BOTTOM)
        painter.fillRect(bounds, backdrop)

        frame = self._vision.frame
        if frame.isNull():
            self._draw_text_centered(painter, bounds, self._placeholder)
        else:
            # Fill the widget, cropping whichever dimension overflows.
            scale = max(bounds.width() / frame.width(), bounds.height() / frame.height())
            picture = QRectF(0, 0, frame.width() * scale, frame.height() * scale)
            picture.moveCenter(bounds.center())

            painter.save()
            painter.translate(bounds.width(), 0)
            painter.scale(-1, 1)  # mirror
            painter.drawImage(picture, frame)
            painter.restore()

            for person in self._vision.people:
                if person.box is None:
                    continue
                xmin, ymin, xmax, ymax = person.box
                # Mirrored picture: the left edge of the box comes from xmax.
                box = QRectF(
                    QPointF(picture.left() + (1 - xmax) * picture.width(),
                            picture.top() + ymin * picture.height()),
                    QPointF(picture.left() + (1 - xmin) * picture.width(),
                            picture.top() + ymax * picture.height()),
                )
                self._draw_person(painter, box.intersected(bounds), person.z_m,
                                  self._vision.is_at_conversation_distance(person))

        if self._caption:
            self._draw_caption(painter, bounds)
        painter.end()

    def _draw_person(self, painter: QPainter, box: QRectF, distance_m: float, visitor: bool) -> None:
        color = COLOR_VISITOR if visitor else COLOR_OTHER
        painter.setPen(QPen(color, 4.0 if visitor else 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box.adjusted(2, 2, -2, -2), 14, 14)

        font = QFont(self.font())
        font.setPixelSize(13 if self._compact else 18)
        font.setBold(True)
        painter.setFont(font)
        text = f"{distance_m:.1f} ม."
        metrics = painter.fontMetrics()
        label = QRectF(0, 0, metrics.horizontalAdvance(text) + 20, metrics.height() + 8)
        label.moveTopLeft(QPointF(box.left() + 10, box.top() + 10))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(label, label.height() / 2, label.height() / 2)
        painter.setPen(COLOR_LABEL_TEXT)
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_caption(self, painter: QPainter, bounds: QRectF) -> None:
        font = QFont(self.font())
        font.setPixelSize(13 if self._compact else 22)
        font.setBold(True)
        painter.setFont(font)
        height = painter.fontMetrics().height() * (2.0 if self._compact else 2.4)
        band = QRectF(bounds.left(), bounds.bottom() - height, bounds.width(), height)
        shade = QLinearGradient(band.topLeft(), band.bottomLeft())
        shade.setColorAt(0.0, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 200))
        painter.fillRect(band, shade)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            band.adjusted(16, 0, -16, -8),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
            self._caption,
        )

    def _draw_text_centered(self, painter: QPainter, bounds: QRectF, text: str) -> None:
        font = QFont(self.font())
        font.setPixelSize(13 if self._compact else 22)
        painter.setFont(font)
        painter.setPen(QColor("#94a3b8"))
        painter.drawText(
            bounds.adjusted(16, 16, -16, -16),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            text,
        )
