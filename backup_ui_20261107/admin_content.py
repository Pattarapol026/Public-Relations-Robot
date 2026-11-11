"""Admin tabs for stored content: media, announcements, knowledge base, settings."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.content_controller import (
    HOME_CAMERA,
    HOME_MEDIA,
    MAX_SECONDS,
    MIN_SECONDS,
    ContentController,
    ContentError,
)
from database.models import MEDIA_VIDEO, MediaItem
from database.repositories import MOVE_DOWN, MOVE_UP
from services.media_service import (
    IMAGE_EXTENSIONS,
    MAX_CATEGORY_CHARS,
    MAX_TITLE_CHARS,
    VIDEO_EXTENSIONS,
    MediaError,
)
from services import venues
from services.knowledge_transfer import thai_date
from ui.form_dialog import CHECK, CHOICE, TEXT, Field, FormDialog
from ui.media_view import MediaView
from ui.widgets import make_button, make_label, set_style_property

_ENABLED_TEXT = {True: "เปิดใช้งาน", False: "ปิด"}


class _TableTab(QWidget):
    """A table of records with a row of action buttons underneath."""

    def __init__(self, headers: list[str], stretch_column: int) -> None:
        super().__init__()
        self._table = QTableWidget(0, len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(46)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(8)
        self._buttons.addStretch(1)  # buttons keep their natural width, packed to the left
        self._status = make_label("", "message", word_wrap=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._table, 1)
        layout.addLayout(self._buttons)
        layout.addWidget(self._status)

    def _add_button(self, text: str, role: str, on_click: Callable[[], None]) -> None:
        self._buttons.insertWidget(self._buttons.count() - 1, make_button(text, role, on_click))

    def _fill(self, rows: list[tuple[int, list[str]]]) -> None:
        selected = self._selected_id()
        self._table.setRowCount(len(rows))
        for row_index, (record_id, cells) in enumerate(rows):
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, record_id)
                self._table.setItem(row_index, column, item)
            if record_id == selected:
                self._table.selectRow(row_index)

    def _selected_id(self) -> int | None:
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _require_selection(self) -> int | None:
        record_id = self._selected_id()
        if record_id is None:
            self._show_status("กรุณาเลือกรายการในตารางก่อน", "error")
        return record_id

    def _show_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        set_style_property(self._status, "level", level)

    def _confirm_delete(self, name: str) -> bool:
        answer = QMessageBox.question(self, "ยืนยันการลบ", f"ลบ \"{name}\" ใช่หรือไม่")
        return answer == QMessageBox.StandardButton.Yes


class MediaTab(_TableTab):
    show_on_showcase = Signal(int)

    def __init__(self, content: ContentController) -> None:
        super().__init__(["ลำดับ", "ชื่อ", "ชนิด", "หมวดหมู่", "ขนาด", "สถานะ"], stretch_column=1)
        self._content = content
        self._add_button("เพิ่มสื่อ", "primary", self._add)
        self._add_button("แก้ไข", "secondary", self._edit)
        self._add_button("ลบ", "secondary", self._delete)
        self._add_button("เลื่อนขึ้น", "secondary", lambda: self._move(MOVE_UP))
        self._add_button("เลื่อนลง", "secondary", lambda: self._move(MOVE_DOWN))
        self._add_button("ดูตัวอย่าง", "secondary", self._preview)
        self._add_button("เล่นบนหน้าแรกตอนนี้", "secondary", self._show)
        content.content_changed.connect(self.refresh)
        content.media_import_finished.connect(self._on_import_finished)
        self.refresh()

    def refresh(self) -> None:
        self._items = {item.id: item for item in self._content.media()}
        self._fill(
            [
                (
                    item.id,
                    [
                        str(index + 1),
                        item.title,
                        "วิดีโอ" if item.media_type == MEDIA_VIDEO else "รูปภาพ",
                        item.category,
                        f"{item.size_bytes / 1_048_576:.1f} MB",
                        _ENABLED_TEXT[item.enabled],
                    ],
                )
                for index, item in enumerate(self._items.values())
            ]
        )

    def _selected_item(self) -> MediaItem | None:
        record_id = self._require_selection()
        return self._items.get(record_id) if record_id is not None else None

    def _add(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in (*VIDEO_EXTENSIONS, *IMAGE_EXTENSIONS))
        file_name, _filter = QFileDialog.getOpenFileName(
            self, "เลือกไฟล์วิดีโอหรือรูปภาพ", "", f"สื่อที่รองรับ ({patterns})"
        )
        if not file_name:
            return
        source = Path(file_name)

        def submit(values: dict) -> None:
            if not values["title"].strip():
                raise ContentError("กรุณากรอกชื่อสื่อ")
            self._show_status("กำลังคัดลอกไฟล์เข้าระบบ...")
            self._content.import_media(
                source, values["title"], values["description"], values["category"]
            )

        FormDialog(
            "เพิ่มสื่อ",
            [
                Field("title", "ชื่อสื่อ", value=source.stem[:MAX_TITLE_CHARS],
                      max_length=MAX_TITLE_CHARS),
                Field("description", "คำอธิบาย", TEXT),
                Field("category", "หมวดหมู่", max_length=MAX_CATEGORY_CHARS),
            ],
            submit, ContentError, self,
        ).exec()

    def _on_import_finished(self, item: MediaItem | None, error: str) -> None:
        if item is None:
            self._show_status(error, "error")
        else:
            self._show_status(f"เพิ่มสื่อ \"{item.title}\" แล้ว")

    def _edit(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        FormDialog(
            "แก้ไขข้อมูลสื่อ",
            [
                Field("title", "ชื่อสื่อ", value=item.title, max_length=MAX_TITLE_CHARS),
                Field("description", "คำอธิบาย", TEXT, item.description),
                Field("category", "หมวดหมู่", value=item.category, max_length=MAX_CATEGORY_CHARS),
                Field("enabled", "เปิดใช้งาน (เล่นบนหน้าแรก)", CHECK, item.enabled),
            ],
            lambda v: self._content.update_media(
                item.id, v["title"], v["description"], v["category"], v["enabled"]
            ),
            ContentError, self,
        ).exec()

    def _delete(self) -> None:
        item = self._selected_item()
        if item is not None and self._confirm_delete(item.title):
            self._content.delete_media(item.id)
            self._show_status(f"ลบสื่อ \"{item.title}\" แล้ว")

    def _move(self, direction: int) -> None:
        record_id = self._require_selection()
        if record_id is not None:
            self._content.move_media(record_id, direction)

    def _preview(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        try:
            path = self._content.media_path(item)
        except MediaError as exc:
            self._show_status(str(exc), "error")
            return
        if not path.is_file():
            self._show_status("ไม่พบไฟล์ของสื่อนี้ในโฟลเดอร์สื่อ", "error")
            return
        _PreviewDialog(item, path, self._content.image_seconds(), self).exec()

    def _show(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        if not item.enabled:
            self._show_status("สื่อนี้ปิดใช้งานอยู่ กรุณาเปิดใช้งานก่อน", "error")
            return
        self.show_on_showcase.emit(item.id)


class _PreviewDialog(QDialog):
    def __init__(self, item: MediaItem, path: Path, image_seconds: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"ดูตัวอย่าง — {item.title}")
        self.setModal(True)
        self.resize(900, 560)
        self._item = item
        self._path = path
        self._view = MediaView(image_seconds)
        self._view.finished.connect(lambda: self._view.show_placeholder("เล่นจบแล้ว"))
        self._view.failed.connect(self._view.show_placeholder)

        buttons = QHBoxLayout()
        buttons.addWidget(make_button("เล่นใหม่", "secondary", self._play))
        buttons.addWidget(make_button("หยุด", "secondary",
                                      lambda: self._view.show_placeholder("หยุดแล้ว")))
        buttons.addWidget(make_button("ปิด", "primary", self.accept))
        layout = QVBoxLayout(self)
        layout.addWidget(self._view, 1)
        layout.addLayout(buttons)
        self._play()

    def _play(self) -> None:
        self._view.show_media(self._path, self._item.media_type)

    def done(self, result: int) -> None:
        self._view.stop()
        super().done(result)


def _venue_field(value: str) -> Field:
    """Which kind of place a knowledge entry or announcement is for."""
    return Field(
        "venue", "ใช้กับหมวดประชาสัมพันธ์", CHOICE, value,
        options=tuple((venue.label, venue.id) for venue in venues.VENUES)
        + (("ใช้ร่วมทุกหมวด", venues.SHARED),),
    )


class AnnouncementTab(_TableTab):
    def __init__(self, content: ContentController) -> None:
        super().__init__(
            ["ลำดับ", "หัวข้อ", "รายละเอียด", "สถานะ", "ประเภทสถานที่"], stretch_column=1
        )
        self._content = content
        self._add_button("เพิ่มประกาศ", "primary", lambda: self._open_form(None))
        self._add_button("แก้ไข", "secondary", self._edit)
        self._add_button("ลบ", "secondary", self._delete)
        self._add_button("เลื่อนขึ้น", "secondary", lambda: self._move(MOVE_UP))
        self._add_button("เลื่อนลง", "secondary", lambda: self._move(MOVE_DOWN))
        content.content_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self._items = {item.id: item for item in self._content.announcements()}
        self._fill(
            [
                (item.id, [str(index + 1), item.title, item.body[:60], _ENABLED_TEXT[item.enabled],
                           venues.label(item.venue)])
                for index, item in enumerate(self._items.values())
            ]
        )

    def _open_form(self, item_id: int | None) -> None:
        item = self._items.get(item_id) if item_id is not None else None
        FormDialog(
            "แก้ไขประกาศ" if item else "เพิ่มประกาศ",
            [
                Field("title", "หัวข้อ", value=item.title if item else "", max_length=200),
                Field("body", "รายละเอียด", TEXT, item.body if item else ""),
                _venue_field(item.venue if item else self._content.venue_id()),
                Field("enabled", "เปิดใช้งาน", CHECK, item.enabled if item else True),
            ],
            lambda v: self._content.save_announcement(
                item_id, v["title"], v["body"], v["enabled"], v["venue"]
            ),
            ContentError, self,
        ).exec()

    def _edit(self) -> None:
        record_id = self._require_selection()
        if record_id is not None:
            self._open_form(record_id)

    def _delete(self) -> None:
        record_id = self._require_selection()
        if record_id is not None and self._confirm_delete(self._items[record_id].title):
            self._content.delete_announcement(record_id)

    def _move(self, direction: int) -> None:
        record_id = self._require_selection()
        if record_id is not None:
            self._content.move_announcement(record_id, direction)




class KnowledgeTab(_TableTab):
    def __init__(self, content: ContentController) -> None:
        super().__init__(
            ["หมวด", "คำถาม / หัวข้อ", "คำตอบ", "ปุ่มลัด", "หมดอายุ", "สถานะ", "ประเภทสถานที่"],
            stretch_column=2,
        )
        self._content = content
        self.layout().insertWidget(0, self._build_venue_bar())
        self._add_button("เพิ่มข้อมูล", "primary", lambda: self._open_form(None))
        self._add_button("แก้ไข", "secondary", self._edit)
        self._add_button("ลบ", "secondary", self._delete)
        self._add_button("นำเข้าจากไฟล์ CSV", "secondary", self._import)
        self._add_button("ส่งออกเป็น CSV", "secondary", self._export)
        content.content_changed.connect(self.refresh)
        self.refresh()

    def _build_venue_bar(self) -> QWidget:
        """Which kind of place the robot serves now, and which entries the table shows."""
        bar = QFrame()
        bar.setProperty("role", "section")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)
        self._venue_now = make_label("", "venue-now", word_wrap=True)
        layout.addWidget(self._venue_now)

        # The place chosen here is also what the table shows (its entries and the shared ones).
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(make_label("หมวดประชาสัมพันธ์", "field-label"))
        self._venue_choice = QComboBox()
        for venue in venues.VENUES:
            self._venue_choice.addItem(venue.label, venue.id)
        self._venue_choice.setCurrentIndex(self._venue_choice.findData(self._content.venue_id()))
        self._venue_choice.currentIndexChanged.connect(self.refresh)
        row.addWidget(self._venue_choice, 2)
        row.addWidget(make_button("ใช้หมวดนี้", "primary", self._apply_venue))
        row.addSpacing(16)
        row.addWidget(make_label("ภาษา", "field-label"))
        # Each entry has a Thai and an English side; the table shows one at a time.
        self._language_choice = QComboBox()
        self._language_choice.addItem("ภาษาไทย", "th")
        self._language_choice.addItem("English", "en")
        self._language_choice.currentIndexChanged.connect(self.refresh)
        row.addWidget(self._language_choice)
        layout.addLayout(row)
        # A question that sounds like another kind of place ("ผลงานสิ่งประดิษฐ์ทีมนี้...")
        # is answered from that place's entries (services/venue_router.py).
        self._venue_auto = QCheckBox(
            "ถ้าคำถามดูเป็นเรื่องของสถานที่อื่น ให้ตอบจากข้อมูลของหมวดนั้นอัตโนมัติ "
            "(เฉพาะหมวดที่มีข้อมูลแล้ว)"
        )
        self._venue_auto.setChecked(self._content.venue_auto())
        self._venue_auto.toggled.connect(self._content.set_venue_auto)
        layout.addWidget(self._venue_auto)
        return bar

    def refresh(self) -> None:
        active = self._content.venue()
        self._venue_now.setText(
            f"หุ่นยนต์ใช้หมวด: {active.label}"
            + (
                "  —  ตอบจากหมวดนี้ หรือหมวดที่คำถามพูดถึงชัดเจน (และข้อมูลใช้ร่วม)"
                if self._content.venue_auto()
                else "  —  ตอบจากข้อมูลของหมวดนี้และข้อมูลใช้ร่วมเท่านั้น"
            )
        )
        self._venue_auto.blockSignals(True)
        self._venue_auto.setChecked(self._content.venue_auto())
        self._venue_auto.blockSignals(False)

        # The place picked above (whether in use or not) and the shared entries. Entries of
        # places no longer offered stay in the database but are not shown.
        shown = self._venue_choice.currentData() or active.id
        everything = self._content.knowledge()
        visible = [item for item in everything if item.venue in (shown, venues.SHARED)]
        self._items = {item.id: item for item in everything}
        english = self._language_choice.currentData() == "en"
        self._table.setHorizontalHeaderItem(
            1, QTableWidgetItem("Question" if english else "คำถาม / หัวข้อ")
        )
        self._table.setHorizontalHeaderItem(2, QTableWidgetItem("Answer" if english else "คำตอบ"))
        today = date.today().isoformat()
        self._fill(
            [
                (
                    item.id,
                    [item.category,
                     (item.question_en or "(ยังไม่มีภาษาอังกฤษ)") if english else item.question,
                     (item.answer_en if english else item.answer)[:80],
                     "ใช่" if item.show_as_shortcut else "",
                     thai_date(item.expires_on) + (" (หมดแล้ว)" if item.expired(today) else ""),
                     _ENABLED_TEXT[item.enabled], venues.label(item.venue)],
                )
                for item in visible
            ]
        )

    def _apply_venue(self) -> None:
        venue_id = self._venue_choice.currentData()
        try:
            self._content.set_venue(venue_id)
        except ContentError as exc:
            self._show_status(str(exc), "error")
            return
        count = len(self._content.active_knowledge())
        self._show_status(
            f"เปลี่ยนเป็นหมวด \"{venues.get(venue_id).label}\" แล้ว — มีข้อมูลที่เปิดใช้งาน {count} รายการ"
        )

    def _import(self) -> None:
        file_name, _filter = QFileDialog.getOpenFileName(
            self, "นำเข้าฐานความรู้", "", "CSV (*.csv)"
        )
        if not file_name:
            return
        try:
            report = self._content.import_knowledge(Path(file_name))
        except ContentError as exc:
            self._show_status(str(exc), "error")
            return
        text = f"นำเข้าแล้ว: เพิ่มใหม่ {report.added} รายการ ปรับปรุง {report.updated} รายการ"
        if report.errors:
            shown = "\n".join(report.errors[:5])
            more = f"\n... และอีก {len(report.errors) - 5} แถว" if len(report.errors) > 5 else ""
            self._show_status(f"{text} ข้าม {len(report.errors)} แถว:\n{shown}{more}", "error")
        else:
            self._show_status(text)

    def _export(self) -> None:
        file_name, _filter = QFileDialog.getSaveFileName(
            self, "ส่งออกฐานความรู้", "knowledge.csv", "CSV (*.csv)"
        )
        if not file_name:
            return
        try:
            count = self._content.export_knowledge(Path(file_name))
        except OSError:
            self._show_status("บันทึกไฟล์ไม่สำเร็จ (ไฟล์อาจเปิดค้างอยู่ในโปรแกรมอื่น)", "error")
            return
        self._show_status(f"ส่งออก {count} รายการไปที่ {file_name} (เปิดด้วย Excel ได้)")

    def _open_form(self, item_id: int | None) -> None:
        item = self._items.get(item_id) if item_id is not None else None
        FormDialog(
            "แก้ไขข้อมูลฐานความรู้" if item else "เพิ่มข้อมูลฐานความรู้",
            [
                Field("category", "หมวด (เช่น FAQ, แผนกวิชา, ติดต่อ)",
                      value=item.category if item else "", max_length=60),
                Field("question", "คำถาม / หัวข้อ", value=item.question if item else "",
                      max_length=300),
                Field("answer", "คำตอบ", TEXT, item.answer if item else ""),
                Field("question_en", "Question (English, เว้นว่างได้)",
                      value=item.question_en if item else "", max_length=300),
                Field("answer_en", "Answer (English, เว้นว่างได้)", TEXT,
                      item.answer_en if item else ""),
                Field("keywords", "คำค้น (คั่นด้วย ,)",
                      value=", ".join(item.keywords) if item else "", max_length=300),
                Field("expires", "ใช้ถึงวันที่ (เช่น 31/12/2569 เว้นว่าง = ไม่หมดอายุ)",
                      value=thai_date(item.expires_on) if item else "", max_length=20),
                # A new entry belongs to the place the table shows.
                _venue_field(item.venue if item else (self._venue_choice.currentData() or self._content.venue_id())),
                Field("shortcut", "ปุ่มลัด (ตอนนี้หน้าสนทนาไม่แสดงปุ่มลัด)", CHECK,
                      item.show_as_shortcut if item else False),
                Field("enabled", "เปิดใช้งาน", CHECK, item.enabled if item else True),
            ],
            lambda v: self._content.save_knowledge(
                item_id, v["category"], v["question"], v["answer"], v["keywords"],
                v["enabled"], v["shortcut"], v["expires"], v["venue"],
                v["question_en"], v["answer_en"],
            ),
            ContentError, self,
        ).exec()

    def _edit(self) -> None:
        record_id = self._require_selection()
        if record_id is not None:
            self._open_form(record_id)

    def _delete(self) -> None:
        record_id = self._require_selection()
        if record_id is not None and self._confirm_delete(self._items[record_id].question):
            self._content.delete_knowledge(record_id)


class DisplaySettingsTab(QWidget):
    def __init__(self, content: ContentController) -> None:
        super().__init__()
        self._content = content
        self._welcome = QPlainTextEdit()
        self._welcome.setFixedHeight(90)
        self._welcome.setTabChangesFocus(True)
        self._image_seconds = self._seconds_box()
        self._announcement_seconds = self._seconds_box()

        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._home_display = QComboBox()
        self._home_display.addItem("สื่อประชาสัมพันธ์และประกาศ (ภาพกล้องขนาดเล็กด้านข้าง)", HOME_MEDIA)
        self._home_display.addItem("ภาพสดจากกล้องขนาดใหญ่", HOME_CAMERA)
        form.addRow("หน้าแรกแสดง", self._home_display)
        form.addRow("ข้อความต้อนรับ", self._welcome)
        form.addRow("เวลาแสดงรูปภาพแต่ละรูป", self._image_seconds)
        form.addRow("เวลาแสดงประกาศแต่ละข้อ", self._announcement_seconds)
        self._privacy_enabled = QCheckBox("แสดงข้อความแจ้งความเป็นส่วนตัวที่ด้านล่างของหน้าจอผู้ใช้")
        self._privacy_text = QPlainTextEdit()
        self._privacy_text.setFixedHeight(80)
        self._privacy_text.setTabChangesFocus(True)
        form.addRow("ความเป็นส่วนตัว (PDPA)", self._privacy_enabled)
        form.addRow("", self._privacy_text)
        # The visitor screen opens on the conversation and never shows the home page now:
        # its settings are kept (and still saved as they are) but not offered.
        for row in range(4):
            form.setRowVisible(row, False)

        self._status = make_label("", "message", word_wrap=True)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("บันทึกการตั้งค่า", "primary", self._save))
        buttons.addWidget(make_button("ยกเลิกการแก้ไข", "secondary", self.discard_changes))
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self._status)
        layout.addStretch(1)
        self.reload()

    def reload(self) -> None:
        self._welcome.setPlainText(self._content.welcome_message())
        self._home_display.setCurrentIndex(
            max(0, self._home_display.findData(self._content.home_display()))
        )
        self._image_seconds.setValue(self._content.image_seconds())
        self._announcement_seconds.setValue(self._content.announcement_seconds())
        self._privacy_enabled.setChecked(self._content.privacy_notice_enabled())
        self._privacy_text.setPlainText(self._content.privacy_notice_text())

    def has_unsaved_changes(self) -> bool:
        content = self._content
        return (
            self._welcome.toPlainText().strip() != content.welcome_message()
            or self._home_display.currentData() != content.home_display()
            or self._image_seconds.value() != content.image_seconds()
            or self._announcement_seconds.value() != content.announcement_seconds()
            or self._privacy_enabled.isChecked() != content.privacy_notice_enabled()
            or " ".join(self._privacy_text.toPlainText().split()) != content.privacy_notice_text()
        )

    def discard_changes(self) -> None:
        self.reload()
        self._status.setText("")

    @staticmethod
    def _seconds_box() -> QSpinBox:
        box = QSpinBox()
        box.setRange(MIN_SECONDS, MAX_SECONDS)
        box.setSuffix(" วินาที")
        return box

    def _save(self) -> None:
        try:
            self._content.save_display_settings(
                self._welcome.toPlainText(),
                self._image_seconds.value(),
                self._announcement_seconds.value(),
            )
            self._content.set_home_display(self._home_display.currentData())
            self._content.save_privacy_notice(
                self._privacy_enabled.isChecked(), self._privacy_text.toPlainText()
            )
        except ContentError as exc:
            self._show_status(str(exc), "error")
            return
        self._show_status("บันทึกการตั้งค่าแล้ว")

    def _show_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        set_style_property(self._status, "level", level)
