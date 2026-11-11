"""Admin tab: usage statistics, visitor feedback, and questions the robot could not answer."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.content_controller import ContentController, ContentError
from services.analytics_service import AnalyticsService, UsageSummary
from ui.form_dialog import CHECK, TEXT, Field, FormDialog
from ui.hour_chart import HourChart
from ui.widgets import make_button, make_label, set_style_property

_PERIODS = (("วันนี้", 1), ("7 วันล่าสุด", 7), ("30 วันล่าสุด", 30))
_SOURCE_LABELS = {
    "knowledge": "ฐานความรู้ (ตรงตัว)",
    "gemini": "Gemini",
    "knowledge_offline": "ฐานความรู้ (AI ใช้ไม่ได้)",
    "fallback": "ตอบไม่ได้",
    "gemini_no_info": "AI ตอบว่าไม่มีข้อมูล",
}
_REASONS = {"fallback": "ตอบไม่ได้", "gemini_no_info": "AI ไม่มีข้อมูล"}
_MODE_LABELS = {"voice": "เสียง", "text": "พิมพ์", "shortcut": "ปุ่มเมนู"}
_LANGUAGE_LABELS = {"th": "ไทย", "en": "อังกฤษ"}


def add_to_knowledge(parent: QWidget, content: ContentController, question: str) -> bool:
    """Ask the Admin for an answer to a visitor's question and store it. True if saved."""

    def submit(values: dict) -> None:
        content.save_knowledge(
            None, values["category"], values["question"], values["answer"],
            values["keywords"], True, values["shortcut"],
        )

    dialog = FormDialog(
        "เพิ่มคำตอบในฐานความรู้",
        [
            Field("category", "หมวด", value="FAQ", max_length=60),
            Field("question", "คำถาม / หัวข้อ", value=question, max_length=300),
            Field("answer", "คำตอบ", TEXT),
            Field("keywords", "คำค้น (คั่นด้วย ,)", max_length=300),
            Field("shortcut", "ปุ่มลัด (ตอนนี้หน้าสนทนาไม่แสดงปุ่มลัด)", CHECK, False),
        ],
        submit, ContentError, parent,
    )
    return bool(dialog.exec())


def _percent(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


class StatsTab(QWidget):
    def __init__(self, analytics: AnalyticsService, content: ContentController) -> None:
        super().__init__()
        self._analytics = analytics
        self._content = content
        self._attention: dict[int, str] = {}

        self._period = QComboBox()
        for label, days in _PERIODS:
            self._period.addItem(label, days)
        self._period.setCurrentIndex(1)
        self._period.currentIndexChanged.connect(self.refresh)
        controls = QHBoxLayout()
        controls.addWidget(make_label("ช่วงเวลา", "hint"))
        controls.addWidget(self._period)
        controls.addStretch(1)
        controls.addWidget(make_button("โหลดใหม่", "secondary", self.refresh))
        controls.addWidget(make_button("ส่งออกเป็นไฟล์ CSV", "secondary", self._export))

        # Headline numbers are tiles, not charts: one number each needs no axis.
        self._tiles: dict[str, tuple[QLabel, QLabel]] = {}
        tiles = QGridLayout()
        tiles.setSpacing(10)
        for index, (key, title) in enumerate(
            (
                ("visits", "ผู้เข้ามาในบริเวณ"),
                ("engaged", "เข้ามาสนทนา"),
                ("questions", "คำถามทั้งหมด"),
                ("answered", "ตอบได้"),
                ("satisfaction", "พอใจคำตอบ"),
                ("speed", "เวลาตอบเฉลี่ย"),
            )
        ):
            tile = QFrame()
            tile.setProperty("role", "tile")
            layout = QVBoxLayout(tile)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(0)
            value = make_label("–", "tile-value")
            note = make_label("", "hint")
            layout.addWidget(make_label(title, "hint"))
            layout.addWidget(value)
            layout.addWidget(note)
            self._tiles[key] = (value, note)
            tiles.addWidget(tile, index // 3, index % 3)  # two rows: fits a small screen

        self._chart = HourChart()
        self._breakdown = make_label("", "hint", word_wrap=True)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["เวลา", "คำถาม", "เหตุผล"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(42)
        self._table.setMinimumHeight(180)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        actions = QHBoxLayout()
        actions.addWidget(make_button("เพิ่มคำตอบในฐานความรู้", "primary", self._add_to_knowledge))
        actions.addWidget(make_button("ไม่ต้องเพิ่ม (ซ่อนรายการนี้)", "secondary", self._dismiss))
        actions.addStretch(1)
        housekeeping = QHBoxLayout()
        housekeeping.addStretch(1)
        housekeeping.addWidget(make_button("ลบข้อความคำถามที่เก็บไว้ทั้งหมด", "secondary", self._clear_text))
        self._status = make_label("", "message", word_wrap=True)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addLayout(tiles)
        layout.addWidget(make_label("จำนวนคำถามตามช่วงเวลาของวัน (นาฬิกา)", "heading"))
        layout.addWidget(self._chart)
        layout.addWidget(self._breakdown)
        layout.addWidget(make_label("คำถามที่หุ่นยนต์ยังตอบไม่ได้ หรือผู้ใช้บอกว่ายังไม่ตรง", "heading"))
        layout.addWidget(self._table)
        layout.addLayout(actions)
        layout.addWidget(self._status)
        if analytics.logs_questions:
            privacy = (
                f"เก็บเฉพาะข้อความคำถามและคำตอบ ไม่เก็บเสียง ภาพ หรือตัวตนของผู้ถาม "
                f"และลบอัตโนมัติเมื่อเก่ากว่า {analytics.retention_days} วัน"
            )
        else:
            privacy = "ปิดการเก็บข้อความคำถามอยู่ (LOG_QUESTIONS=false) จึงมีแต่ตัวเลข ไม่มีรายการคำถาม"
        layout.addWidget(make_label(privacy, "hint", word_wrap=True))
        layout.addLayout(housekeeping)
        self.refresh()

    def refresh(self) -> None:
        days = self._period.currentData()
        summary = self._analytics.summary(days)
        self._show_summary(summary)
        self._chart.set_values(summary.per_hour)

        items = self._analytics.needing_attention(days)
        self._attention = {item.id: item.question for item in items}
        self._table.setRowCount(len(items))
        for row, item in enumerate(items):
            reason = _REASONS.get(item.source, "ผู้ใช้บอกว่ายังไม่ตรง")
            for column, text in enumerate((item.created_at.replace("T", " ")[:16], item.question, reason)):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, item.id)
                self._table.setItem(row, column, cell)

    def _show_summary(self, summary: UsageSummary) -> None:
        def tile(key: str, value: str, note: str = "") -> None:
            self._tiles[key][0].setText(value)
            self._tiles[key][1].setText(note)

        rated = summary.feedback_up + summary.feedback_down
        tile("visits", str(summary.visits), f"อยู่เฉลี่ย {summary.average_visit_s:.0f} วินาที" if summary.visits else "")
        tile("engaged", str(summary.engaged_visits), f"{_percent(summary.engagement_rate)} ของผู้เข้ามา")
        tile("questions", str(summary.questions))
        tile("answered", _percent(summary.answer_rate), f"{summary.answered} จาก {summary.questions} ข้อ")
        tile("satisfaction", _percent(summary.satisfaction), f"จากผู้ให้คะแนน {rated} ครั้ง")
        tile("speed", f"{summary.average_response_ms / 1000:.1f} วินาที" if summary.questions else "–",
             f"AI ขัดข้อง {summary.ai_failures} ครั้ง" if summary.ai_failures else "")

        def parts(counts: dict[str, int], labels: dict[str, str]) -> str:
            return ", ".join(
                f"{labels.get(key, key)} {count}"
                for key, count in sorted(counts.items(), key=lambda pair: -pair[1])
            ) or "–"

        self._breakdown.setText(
            f"ที่มาของคำตอบ: {parts(summary.by_source, _SOURCE_LABELS)}   |   "
            f"ช่องทางถาม: {parts(summary.by_mode, _MODE_LABELS)}   |   "
            f"ภาษา: {parts(summary.by_language, _LANGUAGE_LABELS)}"
        )

    def _selected_id(self) -> int | None:
        row = self._table.currentRow()
        cell = self._table.item(row, 0) if row >= 0 else None
        if cell is None:
            self._show_status("กรุณาเลือกคำถามในตารางก่อน", "error")
            return None
        return cell.data(Qt.ItemDataRole.UserRole)

    def _add_to_knowledge(self) -> None:
        interaction_id = self._selected_id()
        if interaction_id is None:
            return
        if add_to_knowledge(self, self._content, self._attention[interaction_id]):
            self._analytics.resolve(interaction_id)
            self.refresh()
            self._show_status("เพิ่มในฐานความรู้แล้ว หุ่นยนต์จะใช้ตอบตั้งแต่คำถามถัดไป")

    def _dismiss(self) -> None:
        interaction_id = self._selected_id()
        if interaction_id is not None:
            self._analytics.resolve(interaction_id)
            self.refresh()

    def _export(self) -> None:
        days = self._period.currentData()
        file_name, _filter = QFileDialog.getSaveFileName(
            self, "ส่งออกสถิติ", f"usage_{days}days.csv", "CSV (*.csv)"
        )
        if not file_name:
            return
        try:
            rows = self._analytics.export_csv(file_name, days)
        except OSError:
            self._show_status("บันทึกไฟล์ไม่สำเร็จ (ไฟล์อาจเปิดค้างอยู่ในโปรแกรมอื่น)", "error")
            return
        self._show_status(f"ส่งออก {rows} รายการไปที่ {file_name}")

    def _clear_text(self) -> None:
        answer = QMessageBox.question(
            self, "ยืนยัน",
            "ลบข้อความคำถามและคำตอบที่เก็บไว้ทั้งหมดใช่หรือไม่ (ตัวเลขสถิติยังอยู่ แต่รายการคำถามจะหายและกู้คืนไม่ได้)",
        )
        if answer == QMessageBox.StandardButton.Yes:
            removed = self._analytics.clear_question_text()
            self._content.log_activity("admin", "usage.clear_text", f"rows={removed}")
            self.refresh()
            self._show_status(f"ลบข้อความแล้ว {removed} รายการ")

    def _show_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        set_style_property(self._status, "level", level)
