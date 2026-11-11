"""Admin controls for the tunable values (app/tuning.py): sliders, switches and a zone map."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from app.tuning import AREA_WIDTH_FACTOR, NUMBERS, SWITCHES, NumberSpec, Tuning
from ui.widgets import make_label

# Colours of the dark Admin theme (ui/styles/theme.qss).
TALK_COLOR = QColor("#4ade80")
GREET_COLOR = QColor("#d6155f")
RELEASE_COLOR = QColor("#a99ec2")
GRID_COLOR = QColor("#2a2340")
LABEL_COLOR = QColor("#bdb2d6")
ROBOT_COLOR = QColor("#f4f0fa")
OTHER_PERSON_COLOR = QColor("#fbbf24")


class SliderField(QWidget):
    """A labelled slider over a decimal range, with its value and a short explanation."""

    value_changed = Signal(float)

    def __init__(self, spec: NumberSpec) -> None:
        super().__init__()
        self._spec = spec
        self._value_label = make_label("", "slider-value")
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, round((spec.maximum - spec.minimum) / spec.step))
        self._slider.setPageStep(max(1, self._slider.maximum() // 10))
        self._slider.valueChanged.connect(self._on_slider)

        title_row = QHBoxLayout()
        title_row.addWidget(make_label(spec.label, "field-title"), 1)
        title_row.addWidget(self._value_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 6)
        layout.setSpacing(0)
        layout.addLayout(title_row)
        layout.addWidget(self._slider)
        if spec.hint:
            layout.addWidget(make_label(spec.hint, "hint", word_wrap=True))
        self._show(self.value())

    def value(self) -> float:
        spec = self._spec
        return round(spec.minimum + self._slider.value() * spec.step, spec.decimals)

    def set_value(self, value: float) -> None:
        """Values outside the range are shown at the nearest end."""
        spec = self._spec
        self._slider.setValue(round((value - spec.minimum) / spec.step))
        self._show(self.value())

    def _on_slider(self, _position: int) -> None:
        value = self.value()
        self._show(value)
        self.value_changed.emit(value)

    def _show(self, value: float) -> None:
        self._value_label.setText(f"{value:.{self._spec.decimals}f} {self._spec.unit}".strip())


class TuningEditors(QObject):
    """Editors for some of the tuning values; a page places them where they belong."""

    edited = Signal()

    def __init__(self, names: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sliders: dict[str, SliderField] = {}
        self._switches: dict[str, QCheckBox] = {}
        for name in names:
            if name in NUMBERS:
                slider = SliderField(NUMBERS[name])
                slider.value_changed.connect(lambda _value: self.edited.emit())
                self._sliders[name] = slider
            else:
                switch = QCheckBox(SWITCHES[name])
                switch.toggled.connect(lambda _checked: self.edited.emit())
                self._switches[name] = switch

    def widget(self, name: str) -> QWidget:
        return self._sliders.get(name) or self._switches[name]

    def load(self, tuning: Tuning) -> None:
        self.blockSignals(True)
        try:
            for name, slider in self._sliders.items():
                slider.set_value(getattr(tuning, name))
            for name, switch in self._switches.items():
                switch.setChecked(getattr(tuning, name))
        finally:
            self.blockSignals(False)
        self.edited.emit()

    def apply_to(self, tuning: Tuning) -> Tuning:
        """``tuning`` with the values shown in these editors."""
        values = {name: slider.value() for name, slider in self._sliders.items()}
        values.update({name: switch.isChecked() for name, switch in self._switches.items()})
        return replace(tuning, **values)

    def differs_from(self, tuning: Tuning) -> bool:
        """True if what is shown is not ``tuning`` (compared at the precision shown)."""
        for name, slider in self._sliders.items():
            decimals = NUMBERS[name].decimals
            if round(getattr(tuning, name), decimals) != slider.value():
                return True
        return any(switch.isChecked() != getattr(tuning, name) for name, switch in self._switches.items())


class ZoneView(QWidget):
    """Top-down map in front of the robot: the talk and greet zones, and the people seen.

    The zones are drawn exactly as the presence rules test them — distance
    straight ahead and distance to the side — so what the Admin sees is what
    the robot decides.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tuning: Tuning | None = None
        self._people: list[tuple[float, float]] = []
        self.setMinimumSize(300, 300)

    def set_tuning(self, tuning: Tuning) -> None:
        self._tuning = tuning
        self.update()

    def set_people(self, people: list[tuple[float, float]]) -> None:
        """(x_m to the side, z_m straight ahead) of everyone in view."""
        self._people = people
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._tuning is None:
            return
        tuning = self._tuning
        talk_half = tuning.vision_lateral_limit_m
        area_half = talk_half * AREA_WIDTH_FACTOR
        depth = max(3.0, tuning.vision_release_distance_m + 0.6)
        half_width = max(1.5, area_half + 0.6)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        margin_bottom, margin_top = 34.0, 10.0
        scale = min(self.width() / (2 * half_width), (self.height() - margin_bottom - margin_top) / depth)
        origin = QPointF(self.width() / 2, self.height() - margin_bottom)

        def point(x_m: float, z_m: float) -> QPointF:
            return QPointF(origin.x() + x_m * scale, origin.y() - z_m * scale)

        def zone(half: float, distance: float) -> QRectF:
            return QRectF(point(-half, distance), point(half, 0.0))

        # one-metre grid
        painter.setPen(QPen(GRID_COLOR, 1))
        metre = 1
        while metre <= depth:
            y = origin.y() - metre * scale
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
            painter.setPen(LABEL_COLOR)
            painter.drawText(QPointF(6, y - 4), f"{metre} ม.")
            painter.setPen(QPen(GRID_COLOR, 1))
            metre += 1

        release = QPen(RELEASE_COLOR, 1.5, Qt.PenStyle.DashLine)
        painter.setPen(release)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(zone(area_half, tuning.vision_release_distance_m))
        for color, half, distance in (
            (GREET_COLOR, area_half, tuning.vision_greet_distance_m),
            (TALK_COLOR, talk_half, tuning.vision_engage_distance_m),
        ):
            fill = QColor(color)
            fill.setAlpha(38)
            painter.setPen(QPen(color, 2))
            painter.setBrush(fill)
            painter.drawRect(zone(half, distance))

        painter.setBrush(ROBOT_COLOR)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(origin.x() - 16, origin.y(), 32, 14), 4, 4)
        painter.setPen(LABEL_COLOR)
        painter.drawText(
            QRectF(0, origin.y() + 14, self.width(), 18), Qt.AlignmentFlag.AlignCenter, "หุ่นยนต์"
        )

        for x_m, z_m in self._people:
            in_talk = z_m <= tuning.vision_engage_distance_m and abs(x_m) <= talk_half
            in_greet = z_m <= tuning.vision_greet_distance_m and abs(x_m) <= area_half
            color = TALK_COLOR if in_talk else GREET_COLOR if in_greet else OTHER_PERSON_COLOR
            centre = point(max(-half_width, min(half_width, x_m)), min(depth, z_m))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(color)
            painter.drawEllipse(centre, 9, 9)
            painter.setPen(color)
            painter.drawText(centre + QPointF(13, 5), f"{z_m:.1f} ม.")
        painter.end()


def zone_legend() -> QWidget:
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(14)
    for color, text in (
        (TALK_COLOR, "ระยะสนทนา"),
        (GREET_COLOR, "ระยะทักทาย"),
        (RELEASE_COLOR, "ขอบที่ถือว่าเดินออก (เส้นประ)"),
    ):
        swatch = QLabel()
        swatch.setFixedSize(14, 14)
        swatch.setStyleSheet(f"background: {color.name()}; border-radius: 3px;")
        row.addWidget(swatch)
        row.addWidget(make_label(text, "hint"))
    row.addStretch(1)
    return box
