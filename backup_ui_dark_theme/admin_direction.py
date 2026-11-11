"""Admin page for the microphone array's direction: live dial, calibration, and how the robot listens.

Nothing on this page changes how the robot listens until the microphone has been calibrated and a
mode other than "off" is saved. The dial shows what the board reports right now, so the setup can
be checked by eye: speak from the left, the right and the front and watch the dot.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.direction_controller import (
    MAX_TOLERANCE_DEG,
    MIN_TOLERANCE_DEG,
    MODE_LABELS,
    MODES,
    CaptureResult,
    DirectionController,
    DirectionError,
    DirectionSettings,
)
from audio.xvf3800 import STALE_S
from ui.widgets import make_button, make_label, make_section, set_style_property, ThaiLabel

_INK = QColor("#f4f0fa")
_MUTED = QColor("#8f82ad")
_GRID = QColor("#2a2340")
_TEAL = QColor("#d6155f")
_AMBER = QColor("#fbbf24")
_RED = QColor("#ff7a66")
_VOICE_HOLD_S = 2.0  # the dot stays this long after the voice stops, then fades


class CompassWidget(QWidget):
    """The microphone seen from above. Straight ahead (the camera's front) is at the top."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(280, 280)
        self._target: float | None = None
        self._tolerance = 30.0
        self._voice: tuple[float, float] | None = None  # (mic-frame angle, age in seconds)
        self._speaking = False
        self._calibrated = False

    def set_state(
        self, target: float | None, tolerance: float, voice: tuple[float, float] | None,
        speaking: bool, calibrated: bool,
    ) -> None:
        state = (target, tolerance, voice, speaking, calibrated)
        if state == (self._target, self._tolerance, self._voice, self._speaking, self._calibrated):
            return
        self._target, self._tolerance, self._voice = target, tolerance, voice
        self._speaking, self._calibrated = speaking, calibrated
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 60
        centre = QPointF(self.width() / 2, self.height() / 2)
        rect = QRectF(centre.x() - side / 2, centre.y() - side / 2, side, side)
        painter.setPen(QPen(_GRID, 2))
        painter.setBrush(QColor("#0f0c19"))
        painter.drawEllipse(rect)
        painter.drawEllipse(rect.adjusted(side * 0.25, side * 0.25, -side * 0.25, -side * 0.25))
        painter.drawLine(QPointF(centre.x(), rect.top()), QPointF(centre.x(), rect.bottom()))
        painter.drawLine(QPointF(rect.left(), centre.y()), QPointF(rect.right(), centre.y()))

        if self._target is not None:
            wedge = QColor(_TEAL)
            wedge.setAlpha(70)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(wedge)
            # Qt measures arcs counter-clockwise from 3 o'clock in 1/16 degrees; ours is clockwise from 12.
            start = (90.0 - (self._target + self._tolerance)) * 16
            painter.drawPie(rect, int(start), int(2 * self._tolerance * 16))

        font = QFont(self.font())
        font.setPointSizeF(11.0)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(_INK)
        painter.drawText(QRectF(centre.x() - 40, 0, 80, 22), Qt.AlignmentFlag.AlignCenter, "หน้า")
        painter.drawText(QRectF(self.width() - 44, centre.y() - 11, 44, 22), Qt.AlignmentFlag.AlignCenter, "ขวา")
        painter.drawText(QRectF(0, centre.y() - 11, 44, 22), Qt.AlignmentFlag.AlignCenter, "ซ้าย")
        painter.drawText(QRectF(centre.x() - 40, self.height() - 22, 80, 22), Qt.AlignmentFlag.AlignCenter, "หลัง")

        if self._voice is not None:
            angle, age = self._voice
            fade = max(0.0, min(1.0, 1.0 - age / _VOICE_HOLD_S))
            if fade > 0.0:
                radius = side / 2 * 0.78
                x = centre.x() + radius * math.sin(math.radians(angle))
                y = centre.y() - radius * math.cos(math.radians(angle))
                color = QColor(_AMBER if self._calibrated else _MUTED)
                if self._speaking:
                    color = QColor(_RED if self._calibrated else _AMBER)
                color.setAlpha(int(70 + 185 * fade))
                painter.setPen(QPen(color, 3))
                painter.drawLine(centre, QPointF(x, y))
                painter.setBrush(color)
                painter.drawEllipse(QPointF(x, y), 11, 11)
        painter.setBrush(_INK)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(centre, 6, 6)


class DirectionPanel(QWidget):
    def __init__(self, controller: DirectionController) -> None:
        super().__init__()
        self._controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_live())
        layout.addWidget(self._build_calibration())
        layout.addWidget(self._build_mode())
        layout.addStretch(1)

        controller.board_changed.connect(self._refresh_live)
        controller.changed.connect(self._on_controller_changed)
        controller.capture_progress.connect(self._on_capture_progress)
        controller.capture_finished.connect(self._on_capture_finished)
        self.reload()

    # --- sections ---

    def _build_live(self) -> QWidget:
        section, layout = make_section(
            "สถานะบอร์ดและทิศของเสียงตอนนี้",
            "จุดบนวงกลมคือทิศที่บอร์ด ReSpeaker XVF3800 ได้ยินเสียงพูดล่าสุด (ด้านบน = หน้ากล้อง) "
            "ลองพูดจากซ้าย ขวา และหน้า แล้วดูว่าจุดไปทางเดียวกับที่ยืน",
        )
        row = QHBoxLayout()
        row.setSpacing(20)
        self._compass = CompassWidget()
        row.addWidget(self._compass, 0)
        info = QVBoxLayout()
        info.setSpacing(8)
        self._board_status = ThaiLabel()
        self._board_status.setProperty("role", "message")
        self._reading = ThaiLabel()
        self._beam_status = ThaiLabel()
        self._calibration_state = ThaiLabel()
        for label in (self._board_status, self._reading, self._beam_status, self._calibration_state):
            info.addWidget(label)
        info.addStretch(1)
        row.addLayout(info, 1)
        layout.addLayout(row)
        return section

    def _build_calibration(self) -> QWidget:
        section, layout = make_section(
            "ปรับเทียบทิศ (ทำครั้งเดียวหลังติดตั้ง และทุกครั้งที่ขยับไมค์หรือกล้อง)",
            "บอร์ดบอกทิศเป็นองศาในแบบของตัวเอง ต้องบอกระบบว่าทิศไหนคือ \"ตรงหน้ากล้อง\" และเดินไปทางขวาแล้วค่าขึ้นหรือลง "
            "อย่าเดาค่าเอง ให้วัดจากคนจริง",
        )
        layout.addWidget(make_label(
            "ขั้นที่ 1  ให้คนยืนตรงหน้ากล้อง ห่างประมาณ 1.5 เมตร กดปุ่ม แล้วพูดต่อเนื่อง เช่น นับ 1 ถึง 10",
            "field-label", word_wrap=True,
        ))
        self._front_button = make_button("1) วัดทิศหน้า", "primary", lambda: self._start("front"))
        layout.addLayout(self._button_row(self._front_button))
        layout.addWidget(make_label(
            "ขั้นที่ 2  ให้คนเดิมเดินไปยืนเยื้องไปทางขวาของกล้องประมาณ 45° (ห่างประมาณ 1.5 เมตร) กดปุ่ม แล้วพูดต่อเนื่อง",
            "field-label", word_wrap=True,
        ))
        self._right_button = make_button("2) วัดทิศขวา", "primary", lambda: self._start("right"))
        self._forget_button = make_button("ล้างค่าปรับเทียบ", "secondary", self._forget)
        layout.addLayout(self._button_row(self._right_button, self._forget_button))
        self._capture_status = make_label("", "message", word_wrap=True)
        layout.addWidget(self._capture_status)
        return section

    def _build_mode(self) -> QWidget:
        section, layout = make_section(
            "การฟังตามทิศ",
            "ฟังเฉพาะเสียงของคนที่พูดอยู่ ใช้ได้เลยโดยไม่ต้องปรับเทียบ: หุ่นยนต์จำทิศของคนที่เริ่มพูดในรอบนั้น "
            "ถ้ามีเสียงจากทิศอื่นแทรกต่อเนื่อง จะไม่นับว่าผู้ใช้ยังพูดอยู่ แล้วตอบทันทีเมื่อผู้ใช้หยุด "
            "ส่วนโหมดล็อกด้านหน้า / ตามผู้ใช้ ต้องปรับเทียบก่อน ค่าที่บันทึกมีผลทันที "
            "และไม่ถูกเขียนลงบอร์ดถาวร (ถอดสาย USB แล้วบอร์ดกลับเป็นค่าเดิม)",
        )
        self._mode = QComboBox()
        for mode in MODES:
            self._mode.addItem(MODE_LABELS[mode], mode)
        self._tolerance = QSpinBox()
        self._tolerance.setRange(int(MIN_TOLERANCE_DEG), int(MAX_TOLERANCE_DEG))
        self._tolerance.setPrefix("± ")
        self._tolerance.setSuffix(" °")
        self._tolerance.setMinimumWidth(120)
        self._end_turn = QCheckBox("ฟังเฉพาะเสียงของคนที่พูดอยู่ (เสียงจากคนอื่นไม่ทำให้หุ่นรอต่อ)")
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("โหมดไมค์", self._mode)
        form.addRow("ช่วงยอมรับ", self._tolerance)
        layout.addLayout(form)
        layout.addWidget(self._end_turn)
        layout.addWidget(make_label(
            "ล็อกด้านหน้า = บอร์ดฟังเฉพาะทิศหน้ากล้อง · ตามผู้ใช้ = บอร์ดหันตามคนที่กล้องเห็นในระยะสนทนา "
            "(ต้องวัดทิศขวาแล้ว) · ฟังเฉพาะเสียงของคนที่พูดอยู่ = แก้อาการที่คนรอบข้างคุยกัน "
            "แล้วหุ่นยนต์รอฟังนานเกินไป ถ้าไม่แน่ใจทิศ ระบบจะฟังเหมือนเดิม",
            "hint", word_wrap=True,
        ))
        self._save_button = make_button("บันทึก", "primary", self._save)
        self._cancel_button = make_button("ยกเลิกการแก้ไข", "secondary", self.reload)
        layout.addLayout(self._button_row(self._save_button, self._cancel_button))
        self._message = make_label("", "message", word_wrap=True)
        layout.addWidget(self._message)
        return section

    @staticmethod
    def _button_row(*buttons) -> QHBoxLayout:
        row = QHBoxLayout()
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return row

    # --- the editable-page protocol (the Admin asks before leaving with unsaved edits) ---

    def reload(self) -> None:
        s = self._controller.settings
        self._mode.setCurrentIndex(max(0, self._mode.findData(s.mode)))
        self._tolerance.setValue(int(round(s.tolerance_deg)))
        self._end_turn.setChecked(s.end_turn)
        self._show_message("")
        self._refresh_live()

    def has_unsaved_changes(self) -> bool:
        s = self._controller.settings
        return (
            self._mode.currentData() != s.mode
            or self._tolerance.value() != int(round(s.tolerance_deg))
            or self._end_turn.isChecked() != s.end_turn
        )

    def discard_changes(self) -> None:
        self.reload()

    # --- actions ---

    def _start(self, kind: str) -> None:
        try:
            self._controller.start_capture(kind)
        except DirectionError as exc:
            self._show_capture(str(exc), "error")

    def _forget(self) -> None:
        self._controller.forget_calibration()
        self.reload()
        self._show_capture("ล้างค่าปรับเทียบแล้ว หุ่นยนต์กลับไปฟังแบบเดิม", "info")

    def save_changes(self) -> bool:
        return self._save()

    def _save(self) -> bool:
        settings = DirectionSettings(
            mode=self._mode.currentData(),
            front_deg=self._controller.settings.front_deg,
            front_known=self._controller.settings.front_known,
            sign=self._controller.settings.sign,
            sign_known=self._controller.settings.sign_known,
            tolerance_deg=float(self._tolerance.value()),
            end_turn=self._end_turn.isChecked(),
        )
        if settings.mode == "follow" and not settings.sign_known:
            self._show_message("โหมดตามผู้ใช้ต้องวัดทิศขวา (ขั้นที่ 2) ก่อน", "error")
            return False
        try:
            self._controller.save(settings)
        except DirectionError as exc:
            self._show_message(str(exc), "error")
            return False
        self._show_message("บันทึกแล้ว มีผลทันที")
        return True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_live()

    # --- live state ---

    def _on_controller_changed(self) -> None:
        self._refresh_live()

    def _on_capture_progress(self, text: str) -> None:
        self._show_capture(text, "info")
        self._set_capture_buttons()

    def _on_capture_finished(self, result: CaptureResult) -> None:
        self._show_capture(result.message, "info" if result.ok else "error")
        self._set_capture_buttons()
        self._refresh_live()

    def _set_capture_buttons(self) -> None:
        busy = self._controller.capturing
        self._front_button.setEnabled(not busy)
        self._right_button.setEnabled(not busy and self._controller.settings.front_known)

    def _refresh_live(self) -> None:
        if not self.isVisible():
            return
        controller = self._controller
        snap = controller.snapshot()
        s = controller.settings
        if snap.connected:
            self._board_status.setText(f"เชื่อมต่อบอร์ดแล้ว · เฟิร์มแวร์ {snap.version}")
            set_style_property(self._board_status, "level", "info")
        else:
            self._board_status.setText(
                f"ยังไม่เชื่อมต่อบอร์ด: {snap.error or 'กำลังค้นหา'} "
                "(ไมค์ยังใช้อัดเสียงได้ตามปกติ เพียงแต่ไม่มีข้อมูลทิศ)"
            )
            set_style_property(self._board_status, "level", "error")

        now = time.monotonic()
        voice = controller.source.last_voice()
        shown = None
        if voice is not None:
            shown = (voice[1], now - voice[0])
        speaking = snap.fresh(now) and snap.speech
        if snap.fresh(now) and snap.board_degrees is not None:
            mic = s.orientation.to_mic_frame(snap.board_degrees)
            self._reading.setText(
                f"บอร์ดอ่านได้ {snap.board_degrees}° (ค่าดิบ) → ทิศเทียบกับหน้ากล้อง {mic:+.0f}° · "
                + ("ตอนนี้ได้ยินเสียงพูด" if snap.speech else "ตอนนี้เงียบ (ค่าที่เห็นคือเสียงล่าสุด)")
            )
        else:
            self._reading.setText("ยังไม่มีค่าจากบอร์ด")
        if snap.beam_locked and snap.beam_degrees is not None:
            self._beam_status.setText(f"ลำรับเสียง: ล็อกที่ {snap.beam_degrees:.0f}° (ค่าของบอร์ด)")
        else:
            self._beam_status.setText("ลำรับเสียง: บอร์ดหันหาเสียงดังที่สุดเอง")
        if s.front_known:
            sign = "ขวา = ค่าเพิ่มขึ้น" if s.sign > 0 else "ขวา = ค่าลดลง"
            tail = f" · ทิศทางหมุน {s.sign:+d} ({sign})" if s.sign_known else " · ยังไม่ได้วัดทิศขวา"
            self._calibration_state.setText(f"ปรับเทียบแล้ว: ทิศหน้า = {s.front_deg:.0f}°{tail}")
        else:
            self._calibration_state.setText("ยังไม่ได้ปรับเทียบ: ระบบฟังเหมือนเดิมและยังไม่ใช้ทิศช่วยตัดสินอะไร")
        self._compass.set_state(
            controller.target_mic_degrees if controller.in_force else (0.0 if s.front_known else None),
            s.tolerance_deg, shown, speaking, s.front_known,
        )
        self._set_capture_buttons()

    def _show_capture(self, text: str, level: str = "info") -> None:
        self._capture_status.setText(text)
        set_style_property(self._capture_status, "level", level)

    def _show_message(self, text: str, level: str = "info") -> None:
        self._message.setText(text)
        set_style_property(self._message, "level", level)


__all__ = ["CompassWidget", "DirectionPanel", "STALE_S"]
