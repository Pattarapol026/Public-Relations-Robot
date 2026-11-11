"""Admin tab: what the robot does when a visitor shows it a card or a hand gesture."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QFileDialog

from app.content_controller import CARD_COUNT, ContentController, ContentError
from database.models import (
    ACTION_FEEDBACK_DOWN,
    ACTION_FEEDBACK_UP,
    ACTION_SAY,
    ACTION_STOP,
    TRIGGER_CARD,
    TRIGGER_GESTURE,
    VisualTrigger,
)
from ui.admin_content import _ENABLED_TEXT, _TableTab
from ui.card_image import render_card
from ui.form_dialog import CHECK, CHOICE, TEXT, Field, FormDialog
from vision.signs import GESTURE_LABELS

_ACTION_LABELS = {
    ACTION_SAY: "พูดข้อความ / แสดงสื่อ",
    ACTION_FEEDBACK_UP: "บันทึกว่าพอใจคำตอบ",
    ACTION_FEEDBACK_DOWN: "บันทึกว่าไม่พอใจคำตอบ",
    ACTION_STOP: "หยุดพูดและหยุดฟัง",
}


class TriggerTab(_TableTab):
    def __init__(self, content: ContentController) -> None:
        super().__init__(["ชนิด", "สิ่งที่แสดง", "ชื่อ", "หุ่นยนต์จะ", "ข้อความ", "สถานะ"], stretch_column=4)
        self._content = content
        self._add_button("เพิ่มการ์ด", "primary", lambda: self._open_form(TRIGGER_CARD, None))
        self._add_button("เพิ่มท่าทาง", "secondary", lambda: self._open_form(TRIGGER_GESTURE, None))
        self._add_button("แก้ไข", "secondary", self._edit)
        self._add_button("ลบ", "secondary", self._delete)
        self._add_button("บันทึกภาพการ์ดสำหรับพิมพ์", "secondary", self._export_card)
        content.content_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self._items = {trigger.id: trigger for trigger in self._content.triggers()}
        self._fill(
            [
                (
                    trigger.id,
                    [
                        "การ์ด" if trigger.kind == TRIGGER_CARD else "ท่าทาง",
                        f"หมายเลข {trigger.code}" if trigger.kind == TRIGGER_CARD
                        else GESTURE_LABELS.get(trigger.code, trigger.code),
                        trigger.title,
                        _ACTION_LABELS[trigger.action],
                        trigger.response_text[:60],
                        _ENABLED_TEXT[trigger.enabled],
                    ],
                )
                for trigger in self._items.values()
            ]
        )

    def _selected(self) -> VisualTrigger | None:
        record_id = self._require_selection()
        return self._items.get(record_id) if record_id is not None else None

    def _next_free_card(self) -> str:
        used = {t.code for t in self._items.values() if t.kind == TRIGGER_CARD}
        return next((str(n) for n in range(CARD_COUNT) if str(n) not in used), "0")

    def _open_form(self, kind: str, trigger: VisualTrigger | None) -> None:
        media = (("(ไม่แสดงสื่อ)", None),) + tuple(
            (item.title, item.id) for item in self._content.media() if item.enabled
        )
        text_field = Field("text", "ข้อความที่หุ่นยนต์พูด", TEXT, trigger.response_text if trigger else "")
        enabled_field = Field("enabled", "เปิดใช้งาน", CHECK, trigger.enabled if trigger else True)
        title_field = Field("title", "ชื่อ (เช่น แผนกเทคโนโลยีสารสนเทศ)",
                            value=trigger.title if trigger else "", max_length=120)
        if kind == TRIGGER_CARD:
            fields = [
                Field("code", f"หมายเลขการ์ด (0–{CARD_COUNT - 1})",
                      value=trigger.code if trigger else self._next_free_card(), max_length=2),
                title_field,
                text_field,
                Field("media", "สื่อที่แสดงบนหน้าแรก", CHOICE,
                      trigger.media_id if trigger else None, options=media),
                enabled_field,
            ]
        else:
            fields = [
                Field("code", "ท่าทาง", CHOICE, trigger.code if trigger else "Victory",
                      options=tuple((label, name) for name, label in GESTURE_LABELS.items())),
                title_field,
                Field("action", "หุ่นยนต์จะ", CHOICE, trigger.action if trigger else ACTION_SAY,
                      options=tuple((label, action) for action, label in _ACTION_LABELS.items())),
                text_field,
                enabled_field,
            ]

        def submit(values: dict) -> None:
            self._content.save_trigger(
                trigger.id if trigger else None,
                kind,
                str(values["code"]),
                values["title"],
                values.get("action", ACTION_SAY),
                values["text"],
                values.get("media"),
                values["enabled"],
            )

        what = "การ์ด" if kind == TRIGGER_CARD else "ท่าทาง"
        FormDialog(f"แก้ไข{what}" if trigger else f"เพิ่ม{what}", fields, submit, ContentError, self).exec()

    def _edit(self) -> None:
        trigger = self._selected()
        if trigger is not None:
            self._open_form(trigger.kind, trigger)

    def _delete(self) -> None:
        trigger = self._selected()
        if trigger is not None and self._confirm_delete(trigger.title):
            self._content.delete_trigger(trigger.id)

    def _export_card(self) -> None:
        trigger = self._selected()
        if trigger is None:
            return
        if trigger.kind != TRIGGER_CARD:
            self._show_status("เลือกรายการที่เป็นการ์ดก่อน (ท่าทางไม่มีภาพให้พิมพ์)", "error")
            return
        file_name, _filter = QFileDialog.getSaveFileName(
            self, "บันทึกภาพการ์ด", f"card_{trigger.code}.png", "PNG (*.png)"
        )
        if not file_name:
            return
        image = render_card(int(trigger.code), trigger.title, QApplication.font().family())
        if image.save(file_name, "PNG"):
            self._show_status(f"บันทึกภาพการ์ดไว้ที่ {file_name} — พิมพ์ขนาด A6 ขึ้นไป บนกระดาษด้าน")
        else:
            self._show_status("บันทึกภาพการ์ดไม่สำเร็จ", "error")
