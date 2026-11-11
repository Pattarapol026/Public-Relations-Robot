"""Bar chart of questions by hour of day (one series: one hue, no legend).

Thin bars rounded at the top and anchored to the baseline, a few recessive grid
lines, hour labels every three hours, a value label on the busiest hour only,
and the exact number for any bar on hover.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QToolTip, QWidget

# Colours of the Admin theme (ui/styles/admin.qss).
BAR_COLOR = QColor("#ff5c8a")
BAR_HOVER_COLOR = QColor("#ffd0e0")
BAR_BASE_COLOR = QColor("#d6155f")
GRID_COLOR = QColor("#2a2340")
AXIS_COLOR = QColor("#4a3d66")
LABEL_COLOR = QColor("#bdb2d6")  # text stays in ink colours, never the bar colour
VALUE_COLOR = QColor("#ffffff")

MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 44, 12, 26, 30
BAR_GAP = 2.0
BAR_RADIUS = 4.0


class HourChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: tuple[int, ...] = (0,) * 24
        self._hovered = -1
        self.setMinimumHeight(190)
        self.setMouseTracking(True)

    def set_values(self, values: tuple[int, ...]) -> None:
        self._values = tuple(values)
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(
            MARGIN_LEFT, MARGIN_TOP,
            max(1.0, self.width() - MARGIN_LEFT - MARGIN_RIGHT),
            max(1.0, self.height() - MARGIN_TOP - MARGIN_BOTTOM),
        )

    def _axis_top(self) -> int:
        """A round number at or above the largest value, so grid lines land on whole counts."""
        peak = max(self._values)
        for step in (4, 8, 12, 20, 40, 80, 120, 200, 400, 800, 1200, 2000):
            if peak <= step:
                return step
        return peak

    def _bar_rect(self, hour: int, plot: QRectF, top_value: int) -> QRectF:
        slot = plot.width() / 24
        height = plot.height() * self._values[hour] / top_value
        return QRectF(
            plot.left() + hour * slot + BAR_GAP / 2, plot.bottom() - height,
            slot - BAR_GAP, height,
        )

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        font = QFont(self.font())
        font.setPixelSize(13)
        painter.setFont(font)

        if not any(self._values):
            painter.setPen(LABEL_COLOR)
            painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, "ยังไม่มีคำถามในช่วงเวลานี้")
            painter.end()
            return

        top_value = self._axis_top()
        for fraction in (0.5, 1.0):  # two grid lines are enough to read heights
            y = plot.bottom() - plot.height() * fraction
            painter.setPen(QPen(GRID_COLOR, 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(LABEL_COLOR)
            painter.drawText(
                QRectF(0, y - 10, MARGIN_LEFT - 8, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(round(top_value * fraction)),
            )

        peak_hour = max(range(24), key=lambda hour: self._values[hour])
        painter.setPen(Qt.PenStyle.NoPen)
        for hour, value in enumerate(self._values):
            if not value:
                continue
            bar = self._bar_rect(hour, plot, top_value)
            radius = min(BAR_RADIUS, bar.width() / 2, bar.height())
            path = QPainterPath()  # rounded at the data end, square on the baseline
            path.moveTo(bar.bottomLeft())
            path.lineTo(bar.left(), bar.top() + radius)
            path.quadTo(bar.topLeft(), QPointF(bar.left() + radius, bar.top()))
            path.lineTo(bar.right() - radius, bar.top())
            path.quadTo(bar.topRight(), QPointF(bar.right(), bar.top() + radius))
            path.lineTo(bar.bottomRight())
            path.closeSubpath()
            top = BAR_HOVER_COLOR if hour == self._hovered else BAR_COLOR
            fade = QLinearGradient(bar.topLeft(), bar.bottomLeft())  # bright at the top, deep magenta at the base
            fade.setColorAt(0.0, top)
            fade.setColorAt(1.0, BAR_BASE_COLOR)
            painter.fillPath(path, fade)

        painter.setPen(QPen(AXIS_COLOR, 1))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())

        painter.setPen(LABEL_COLOR)
        slot = plot.width() / 24
        for hour in range(0, 24, 3):
            painter.drawText(
                QRectF(plot.left() + hour * slot - 20, plot.bottom() + 6, slot + 40, 18),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                f"{hour:02d}",
            )

        peak_bar = self._bar_rect(peak_hour, plot, top_value)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(VALUE_COLOR)
        painter.drawText(
            QRectF(peak_bar.center().x() - 30, peak_bar.top() - 20, 60, 18),
            Qt.AlignmentFlag.AlignCenter,
            str(self._values[peak_hour]),
        )
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        plot = self._plot()
        position = event.position()
        hour = int((position.x() - plot.left()) / (plot.width() / 24))
        inside = plot.left() <= position.x() < plot.right() and 0 <= hour < 24
        hovered = hour if inside and self._values[hour] else -1
        if hovered != self._hovered:
            self._hovered = hovered
            self.update()
        if hovered >= 0:
            # The whole column is the hover target, not just the bar.
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{hovered:02d}:00–{hovered:02d}:59 น.  {self._values[hovered]} คำถาม",
                self,
            )
        else:
            QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        self._hovered = -1
        self.update()
        super().leaveEvent(event)
