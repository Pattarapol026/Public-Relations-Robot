"""Admin tab: public relations messages the robot delivers by itself at set times."""
from __future__ import annotations

from PySide6.QtCore import Signal

from app.content_controller import ContentController, ContentError
from database.models import Schedule
from services.schedule_service import ALL_WEEKDAYS, GRACE_MINUTES, describe_weekdays
from ui.admin_content import _ENABLED_TEXT, _TableTab
from ui.form_dialog import CHECK, CHOICE, TEXT, Field, FormDialog

_WEEKDAY_CHOICES = (
    ("ทุกวัน", ALL_WEEKDAYS),
    ("จันทร์–ศุกร์", "01234"),
    ("เสาร์–อาทิตย์", "56"),
    ("จันทร์", "0"), ("อังคาร", "1"), ("พุธ", "2"), ("พฤหัสบดี", "3"),
    ("ศุกร์", "4"), ("เสาร์", "5"), ("อาทิตย์", "6"),
)


class ScheduleTab(_TableTab):
    run_requested = Signal(object)  # Schedule: the Admin wants to see it now

    def __init__(self, content: ContentController) -> None:
        super().__init__(["เวลา", "วัน", "ชื่อ", "ข้อความที่ประกาศ", "สื่อ", "ล่าสุด", "สถานะ"], stretch_column=3)
        self._content = content
        self._add_button("เพิ่มกำหนดการ", "primary", lambda: self._open_form(None))
        self._add_button("แก้ไข", "secondary", self._edit)
        self._add_button("ลบ", "secondary", self._delete)
        self._add_button("ประกาศเดี๋ยวนี้", "secondary", self._run_now)
        content.content_changed.connect(self.refresh)
        self.refresh()
        self._show_status(
            f"ถ้าถึงเวลาแล้วมีคนกำลังคุยกับหุ่นยนต์ จะรอจนว่างไม่เกิน {GRACE_MINUTES} นาที "
            "เกินจากนั้นจะข้ามของวันนั้นไป"
        )

    def refresh(self) -> None:
        self._items = {schedule.id: schedule for schedule in self._content.schedules()}
        media_titles = {item.id: item.title for item in self._content.media()}
        self._fill(
            [
                (
                    schedule.id,
                    [
                        schedule.time_of_day,
                        describe_weekdays(schedule.weekdays),
                        schedule.title,
                        schedule.message[:70],
                        media_titles.get(schedule.media_id, ""),
                        schedule.last_run_date or "ยังไม่เคย",
                        _ENABLED_TEXT[schedule.enabled],
                    ],
                )
                for schedule in self._items.values()
            ]
        )

    def _selected(self) -> Schedule | None:
        record_id = self._require_selection()
        return self._items.get(record_id) if record_id is not None else None

    def _open_form(self, schedule: Schedule | None) -> None:
        media = (("(ไม่แสดงสื่อ)", None),) + tuple(
            (item.title, item.id) for item in self._content.media() if item.enabled
        )
        FormDialog(
            "แก้ไขกำหนดการ" if schedule else "เพิ่มกำหนดการ",
            [
                Field("title", "ชื่อ (เช่น เชิญเข้าแถวตอนเช้า)", value=schedule.title if schedule else "",
                      max_length=120),
                Field("time", "เวลา (เช่น 08:30)", value=schedule.time_of_day if schedule else "08:00",
                      max_length=5),
                Field("weekdays", "วัน", CHOICE, schedule.weekdays if schedule else ALL_WEEKDAYS,
                      options=_WEEKDAY_CHOICES),
                Field("message", "ข้อความที่หุ่นยนต์ประกาศ", TEXT, schedule.message if schedule else ""),
                Field("media", "สื่อที่แสดงบนหน้าแรก", CHOICE, schedule.media_id if schedule else None,
                      options=media),
                Field("enabled", "เปิดใช้งาน", CHECK, schedule.enabled if schedule else True),
            ],
            lambda v: self._content.save_schedule(
                schedule.id if schedule else None, v["title"], v["time"], v["weekdays"],
                v["message"], v["media"], v["enabled"],
            ),
            ContentError, self,
        ).exec()

    def _edit(self) -> None:
        schedule = self._selected()
        if schedule is not None:
            self._open_form(schedule)

    def _delete(self) -> None:
        schedule = self._selected()
        if schedule is not None and self._confirm_delete(schedule.title):
            self._content.delete_schedule(schedule.id)

    def _run_now(self) -> None:
        schedule = self._selected()
        if schedule is not None:
            self.run_requested.emit(schedule)
