"""Admin page for the camera: live status, a map of who is where, and the greet/talk distances."""
from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.tuning import Tuning, TuningController, TuningError
from app.vision_controller import VisionController
from config.settings import Settings
from ui.tuning_widgets import TuningEditors, ZoneView, zone_legend
from ui.widgets import make_button, make_label, make_section, set_style_property, ThaiLabel
from vision.interfaces import SourceStatus
from vision.signs import GESTURE_LABELS, KIND_CARD

_STATUS_TEXT = {
    SourceStatus.DISABLED: "ปิดใช้งาน",
    SourceStatus.STARTING: "กำลังเชื่อมต่อ...",
    SourceStatus.RUNNING: "ทำงานอยู่",
    SourceStatus.UNAVAILABLE: "ใช้งานไม่ได้",
}
_DISTANCE_NAMES = [
    "vision_greet_distance_m", "vision_greet_dwell_s",
    "vision_engage_distance_m", "vision_dwell_s",
    "vision_release_distance_m", "vision_leave_s",
    "vision_lateral_limit_m", "vision_auto_listen",
]


class VisionTab(QWidget):
    def __init__(self, settings: Settings, vision: VisionController, tuning: TuningController) -> None:
        super().__init__()
        self._vision = vision
        self._tuning = tuning
        self._signs_enabled = settings.vision_signs
        self._editors = TuningEditors(_DISTANCE_NAMES, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_status(settings))
        layout.addWidget(self._build_zones())

        self._result = make_label("", "message", word_wrap=True)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("บันทึก", "primary", self._save))
        buttons.addWidget(make_button("ยกเลิกการแก้ไข", "secondary", self.discard_changes))
        buttons.addWidget(make_button("คืนค่าเริ่มต้นจาก .env", "secondary", self._restore_defaults))
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(self._result)
        layout.addStretch(1)

        self._editors.edited.connect(self._on_edited)
        vision.changed.connect(self._refresh)
        vision.frame_changed.connect(self._show_people)
        self._editors.load(tuning.values)
        self._refresh()

    def _build_status(self, settings: Settings) -> QWidget:
        section, layout = make_section("สถานะกล้อง")
        self._status = ThaiLabel()
        self._people = QLabel()
        self._distance = QLabel()
        self._visitor = QLabel()
        self._sign = QLabel()
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.addRow("กล้อง", self._status)
        form.addRow("จำนวนคนด้านหน้า", self._people)
        form.addRow("ระยะของคนที่ใกล้ที่สุด", self._distance)
        form.addRow("สถานะผู้ใช้", self._visitor)
        form.addRow("การ์ด/ท่าทางที่เห็นล่าสุด", self._sign)
        layout.addLayout(form)
        layout.addWidget(
            make_label(
                (
                    "การตรวจจับคนและวัดระยะทำในตัวกล้อง ภาพสดถูกส่งมาแสดงบนหน้าจอผู้ใช้เท่านั้น "
                    "ไม่มีการบันทึกภาพและไม่จดจำใบหน้า (ปิดภาพสดได้ด้วย VISION_PREVIEW=false)"
                    if settings.vision_preview
                    else "การตรวจจับคนและวัดระยะทำในตัวกล้อง ภาพสดปิดอยู่ "
                    "โปรแกรมได้รับเฉพาะตำแหน่งของคนเป็นเมตร ไม่มีภาพออกจากกล้อง"
                ),
                "hint", word_wrap=True,
            )
        )
        return section

    def _build_zones(self) -> QWidget:
        section, layout = make_section(
            "ระยะทักทายและระยะสนทนา",
            "เลื่อนแถบแล้วดูขอบเขตบนแผนผังได้ทันที จุดบนแผนผังคือคนที่กล้องเห็นอยู่ตอนนี้ "
            "(สีเขียว = อยู่ในระยะสนทนา สีชมพู = อยู่ในระยะทักทาย สีเหลือง = อยู่นอกระยะ)",
        )
        self._zones = ZoneView()
        self._live = make_label("", "message", word_wrap=True)
        left = QVBoxLayout()
        left.addWidget(self._zones, 1)
        left.addWidget(zone_legend())
        left.addWidget(self._live)
        sliders = QVBoxLayout()
        sliders.setSpacing(2)
        for name in _DISTANCE_NAMES:
            sliders.addWidget(self._editors.widget(name))
        sliders.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addLayout(left, 5)
        row.addLayout(sliders, 6)
        layout.addLayout(row)
        return section

    # --- state ---

    def has_unsaved_changes(self) -> bool:
        return self._editors.differs_from(self._tuning.values)

    def discard_changes(self) -> None:
        self._editors.load(self._tuning.values)
        self._show_result("")

    def _shown(self) -> Tuning:
        return self._editors.apply_to(self._tuning.values)

    def save_changes(self) -> bool:
        return self._save()

    def _save(self) -> bool:
        try:
            self._tuning.save(self._shown())
        except TuningError as exc:
            self._show_result(str(exc), "error")
            return False
        self._show_result("บันทึกระยะและเวลาแล้ว มีผลทันที")
        return True

    def _restore_defaults(self) -> None:
        self._tuning.restore_defaults()
        self._editors.load(self._tuning.values)
        self._show_result("ใช้ระยะและเวลาจากไฟล์ .env แล้ว")

    def _on_edited(self) -> None:
        self._zones.set_tuning(self._shown())
        self._show_people()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._show_people()

    def _show_people(self) -> None:
        if not self.isVisible():
            return  # called for every camera frame; nothing to draw while the page is hidden
        people = [(person.x_m, person.z_m) for person in self._vision.people]
        self._zones.set_people(people)
        if self._vision.status is not SourceStatus.RUNNING:
            self._live.setText("กล้องไม่ได้ทำงาน: แผนผังแสดงเฉพาะขอบเขตที่ตั้งไว้")
            return
        tuning = self._shown()
        talk_half = tuning.talk_config().lateral_limit_m
        area_half = tuning.area_config().lateral_limit_m
        in_talk = [p for p in people if p[1] <= tuning.vision_engage_distance_m and abs(p[0]) <= talk_half]
        in_greet = [p for p in people if p[1] <= tuning.vision_greet_distance_m and abs(p[0]) <= area_half]
        if not people:
            text = "ตอนนี้ไม่เห็นใครอยู่ด้านหน้า"
        elif in_talk:
            text = f"มีคนอยู่ในระยะสนทนา {len(in_talk)} คน — หุ่นยนต์จะเริ่มฟัง"
        elif in_greet:
            text = f"มีคนอยู่ในระยะทักทาย {len(in_greet)} คน — หุ่นยนต์จะทักทาย"
        else:
            text = f"เห็น {len(people)} คน แต่อยู่นอกระยะทักทาย"
        self._live.setText(text)

    def _refresh(self) -> None:
        vision = self._vision
        text = _STATUS_TEXT[vision.status]
        self._status.setText(f"{text} — {vision.detail}" if vision.detail else text)
        running = vision.status is SourceStatus.RUNNING
        self._people.setText(str(vision.people_count) if running else "-")
        distance = vision.distance_m
        self._distance.setText(f"{distance:.1f} ม." if running and distance is not None else "-")
        sign = vision.last_sign
        if sign is None:
            self._sign.setText("-" if self._signs_enabled else "ปิดอยู่ (VISION_SIGNS=false)")
        elif sign.kind == KIND_CARD:
            self._sign.setText(f"การ์ดหมายเลข {sign.code}")
        else:
            self._sign.setText(f"ท่าทาง: {GESTURE_LABELS.get(sign.code, sign.code)}")
        if vision.engaged:
            self._visitor.setText("มีผู้ใช้อยู่ในระยะสนทนา")
        elif vision.present:
            self._visitor.setText("มีผู้ใช้อยู่ในบริเวณให้บริการ")
        else:
            self._visitor.setText("ไม่มีผู้ใช้")
        self._show_people()

    def _show_result(self, text: str, level: str = "info") -> None:
        self._result.setText(text)
        set_style_property(self._result, "level", level)
