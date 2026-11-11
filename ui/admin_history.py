"""Admin page: find past conversations, read them in full, and turn mistakes into knowledge."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.content_controller import ContentController
from database.models import Interaction
from services.analytics_service import (
    KIND_AI_ERROR,
    KIND_ALL,
    KIND_DISLIKED,
    KIND_UNANSWERED,
    AnalyticsService,
)
from ui.admin_stats import add_to_knowledge
from ui.widgets import make_button, make_label, make_section, set_style_property

SHOWN_ROWS = 500
_PERIODS = (("วันนี้", 1), ("7 วันล่าสุด", 7), ("30 วันล่าสุด", 30), ("90 วันล่าสุด", 90))
_KINDS = (
    ("ทุกบทสนทนา", KIND_ALL),
    ("หุ่นยนต์ตอบไม่ได้", KIND_UNANSWERED),
    ("ถูกบอกว่ายังไม่ตรง", KIND_DISLIKED),
    ("AI ใช้งานไม่ได้", KIND_AI_ERROR),
)
_SOURCE = {"knowledge": "ฐานความรู้", "gemini": "Gemini", "knowledge_offline": "ฐานความรู้ (AI ใช้ไม่ได้)",
           "fallback": "ตอบไม่ได้", "gemini_no_info": "AI ไม่มีข้อมูล"}
_MODE = {"voice": "เสียง", "text": "พิมพ์", "shortcut": "ปุ่มเมนู"}
_FEEDBACK = {1: "ช่วยได้", -1: "ยังไม่ตรง", 0: ""}


class HistoryTab(QWidget):
    def __init__(self, analytics: AnalyticsService, content: ContentController) -> None:
        super().__init__()
        self._analytics = analytics
        self._content = content
        self._rows: list[Interaction] = []

        self._text = QLineEdit()
        self._text.setPlaceholderText("ค้นหาคำในคำถามหรือคำตอบ เช่น สมัครเรียน")
        self._text.returnPressed.connect(self.refresh)
        self._period = QComboBox()
        for label, days in _PERIODS:
            self._period.addItem(label, days)
        self._period.setCurrentIndex(1)
        self._kind = QComboBox()
        for label, kind in _KINDS:
            self._kind.addItem(label, kind)
        self._period.currentIndexChanged.connect(self.refresh)
        self._kind.currentIndexChanged.connect(self.refresh)
        controls = QHBoxLayout()
        controls.addWidget(self._text, 1)
        controls.addWidget(self._period)
        controls.addWidget(self._kind)
        controls.addWidget(make_button("ค้นหา", "primary", self.refresh))

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["เวลา", "คำถาม", "คำตอบ", "ที่มา", "ช่องทาง", "คำติชม"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setMinimumHeight(240)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._show_selected)
        self._count = make_label("", "hint")

        detail, detail_layout = make_section("บทสนทนาที่เลือก")
        self._question = make_label("เลือกแถวในตารางเพื่ออ่านเต็ม ๆ", "list-text", word_wrap=True)
        self._answer = make_label("", "list-text", word_wrap=True)
        self._meta = make_label("", "hint", word_wrap=True)
        detail_layout.addWidget(self._question)
        detail_layout.addWidget(self._answer)
        detail_layout.addWidget(self._meta)
        actions = QHBoxLayout()
        actions.addWidget(make_button("เพิ่มคำตอบในฐานความรู้", "primary", self._add_to_knowledge))
        actions.addWidget(make_button("ทำเครื่องหมายว่าตอบผิด", "secondary", self._mark_wrong))
        actions.addStretch(1)
        actions.addWidget(make_button("ส่งออกผลการค้นหาเป็น CSV", "secondary", self._export))
        detail_layout.addLayout(actions)
        self._status = make_label("", "message", word_wrap=True)
        detail_layout.addWidget(self._status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)
        layout.addLayout(controls)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._count)
        layout.addWidget(detail)
        if not analytics.logs_questions:
            layout.addWidget(
                make_label(
                    "ปิดการเก็บข้อความคำถามอยู่ (LOG_QUESTIONS=false) จึงเห็นเฉพาะเวลาและที่มาของคำตอบ",
                    "hint", word_wrap=True,
                )
            )

    def refresh(self) -> None:
        selected = self._selected()
        self._rows = self._analytics.search(
            self._period.currentData(), self._text.text(), self._kind.currentData(), SHOWN_ROWS
        )
        self._table.setRowCount(len(self._rows))
        for row, item in enumerate(self._rows):
            cells = (
                item.created_at.replace("T", " ")[:16], item.question, item.answer,
                _SOURCE.get(item.source, item.source), _MODE.get(item.input_mode, item.input_mode),
                _FEEDBACK.get(item.feedback, ""),
            )
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, item.id)
                self._table.setItem(row, column, cell)
            if selected is not None and item.id == selected.id:
                self._table.selectRow(row)
        more = f" (แสดง {SHOWN_ROWS} รายการล่าสุด)" if len(self._rows) >= SHOWN_ROWS else ""
        self._count.setText(f"พบ {len(self._rows)} บทสนทนา{more}")
        if self._selected() is None:
            self._show_selected()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    # --- selection ---

    def _selected(self) -> Interaction | None:
        row = self._table.currentRow()
        if not 0 <= row < len(self._rows) or not self._table.selectionModel().hasSelection():
            return None
        return self._rows[row]

    def _show_selected(self) -> None:
        item = self._selected()
        if item is None:
            self._question.setText("เลือกแถวในตารางเพื่ออ่านเต็ม ๆ")
            self._answer.setText("")
            self._meta.setText("")
            return
        self._question.setText(f"ผู้ใช้: {item.question or '(ไม่ได้เก็บข้อความ)'}")
        self._answer.setText(f"หุ่นยนต์: {item.answer or '-'}")
        notes = [
            item.created_at.replace("T", " "),
            f"ที่มา: {_SOURCE.get(item.source, item.source)}",
            f"ช่องทาง: {_MODE.get(item.input_mode, item.input_mode)}",
            f"ภาษา: {item.language}",
            f"ตอบใน {item.response_ms / 1000:.1f} วินาที",
        ]
        if item.ai_error:
            notes.append(f"AI ขัดข้อง: {item.ai_error}")
        if item.feedback:
            notes.append(f"คำติชม: {_FEEDBACK[item.feedback]}")
        self._meta.setText("  ·  ".join(notes))

    def _require_selected(self) -> Interaction | None:
        item = self._selected()
        if item is None:
            self._show_status("กรุณาเลือกบทสนทนาในตารางก่อน", "error")
        return item

    # --- actions ---

    def _add_to_knowledge(self) -> None:
        item = self._require_selected()
        if item is not None and add_to_knowledge(self, self._content, item.question):
            self._analytics.resolve(item.id)
            self._show_status("เพิ่มในฐานความรู้แล้ว หุ่นยนต์จะใช้ตอบตั้งแต่คำถามถัดไป")

    def _mark_wrong(self) -> None:
        item = self._require_selected()
        if item is None:
            return
        self._analytics.mark_wrong(item.id)
        self._content.log_activity("admin", "conversation.mark_wrong", f"id={item.id}")
        self.refresh()
        self._show_status("ทำเครื่องหมายแล้ว รายการนี้จะอยู่ในตาราง \"คำถามที่ยังตอบไม่ได้\" ของหน้าสถิติ")

    def _export(self) -> None:
        if not self._rows:
            self._show_status("ไม่มีผลการค้นหาให้ส่งออก", "error")
            return
        file_name, _filter = QFileDialog.getSaveFileName(
            self, "ส่งออกบทสนทนา", "conversations.csv", "CSV (*.csv)"
        )
        if not file_name:
            return
        try:
            rows = self._analytics.export_rows(file_name, list(reversed(self._rows)))
        except OSError:
            self._show_status("บันทึกไฟล์ไม่สำเร็จ (ไฟล์อาจเปิดค้างอยู่ในโปรแกรมอื่น)", "error")
            return
        self._show_status(f"ส่งออก {rows} บทสนทนาไปที่ {file_name}")

    def _show_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        set_style_property(self._status, "level", level)
