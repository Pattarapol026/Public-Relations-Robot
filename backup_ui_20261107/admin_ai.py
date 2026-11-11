"""Admin tab: see exactly how the institution's own data reaches the AI for a question."""
from __future__ import annotations

import time

from PySide6.QtCore import QThreadPool, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.workers import FunctionWorker
from services.conversation_service import ConversationService
from ui.widgets import make_button, make_label, set_style_property

_SOURCE_TEXT = {
    "knowledge": "ตอบจากฐานความรู้โดยตรง ไม่ได้ถาม AI",
    "gemini": "Gemini เรียบเรียงจากข้อมูลด้านล่าง",
    "knowledge_offline": "AI ใช้ไม่ได้ จึงตอบจากรายการที่ตรงที่สุด",
    "fallback": "ไม่มีข้อมูลให้ตอบ",
    "gemini_no_info": "AI ตอบว่าไม่มีข้อมูลเรื่องนี้ (จะอยู่ในรายการคำถามที่ต้องเพิ่ม)",
}


class AiTab(QWidget):
    def __init__(self, service: ConversationService) -> None:
        super().__init__()
        self._service = service

        self._question = QLineEdit()
        self._question.setPlaceholderText("พิมพ์คำถามตัวอย่าง เช่น สมัครเรียนต้องไปที่ไหน")
        self._question.setMaxLength(500)
        self._question.returnPressed.connect(self._preview)
        top = QHBoxLayout()
        top.addWidget(self._question, 1)
        top.addWidget(make_button("ดูข้อมูลที่จะส่งให้ AI", "primary", self._preview))
        top.addWidget(make_button("ถามจริง", "secondary", self._ask))

        self._steps = make_label("", "message", word_wrap=True)

        self._ranked = QTableWidget(0, 3)
        self._ranked.setHorizontalHeaderLabels(["คะแนน", "รายการในฐานความรู้", "ส่งให้ AI"])
        self._ranked.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._ranked.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._ranked.verticalHeader().setVisible(False)
        header = self._ranked.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._ranked.setMinimumHeight(150)

        self._instruction = QPlainTextEdit()
        self._instruction.setReadOnly(True)
        self._instruction.setMinimumHeight(170)
        self._answer = make_label("", "message", word_wrap=True)

        layout = QVBoxLayout(self)
        layout.addWidget(
            make_label(
                "หุ่นยนต์ไม่ได้ให้ AI ตอบจากความรู้ทั่วไป แต่คัดข้อมูลของสถานศึกษาที่เกี่ยวกับคำถาม "
                "แล้วส่งไปพร้อมกติกาให้ตอบจากข้อมูลนั้นเท่านั้น หน้านี้แสดงแต่ละขั้นสำหรับคำถามที่พิมพ์",
                "hint", word_wrap=True,
            )
        )
        layout.addLayout(top)
        layout.addWidget(self._steps)
        layout.addWidget(make_label("ขั้นที่ 1: รายการที่ระบบคัดว่าเกี่ยวข้อง (เรียงตามคะแนน)", "heading"))
        layout.addWidget(self._ranked)
        layout.addWidget(make_label("ขั้นที่ 2: ข้อความทั้งหมดที่ส่งให้ AI พร้อมคำถาม", "heading"))
        layout.addWidget(self._instruction)
        layout.addWidget(make_label("ขั้นที่ 3: คำตอบ", "heading"))
        layout.addWidget(self._answer)

    def _preview(self) -> bool:
        question = self._question.text().strip()
        if not question:
            self._show(self._steps, "กรุณาพิมพ์คำถามตัวอย่างก่อน", "error")
            return False
        preview = self._service.preview(question)
        sent = {item.id for item in preview.sent_items}

        rows = list(preview.ranked)
        self._ranked.setRowCount(len(rows))
        for row, (item, score) in enumerate(rows):
            cells = (f"{score:.1f}", f"{item.question} — {item.answer[:60]}", "ใช่" if item.id in sent else "")
            for column, text in enumerate(cells):
                self._ranked.setItem(row, column, QTableWidgetItem(text))
        self._instruction.setPlainText(preview.instruction)

        if preview.exact_match is not None:
            summary = "คำถามนี้ตรงกับรายการในฐานความรู้พอดี จึงตอบจากฐานความรู้โดยตรง ไม่ต้องถาม AI"
        elif not self._service.uses_gemini:
            summary = "ตอนนี้ไม่ได้ใช้ AI จึงตอบจากรายการที่คะแนนสูงสุด ถ้าคะแนนถึงเกณฑ์"
        else:
            summary = (
                f"ฐานความรู้มี {preview.knowledge_size} รายการ เกี่ยวข้องกับคำถามนี้ {len(rows)} รายการ "
                f"ส่งให้ AI {len(preview.sent_items)} รายการ พร้อมประกาศปัจจุบันและกติกา"
            )
        self._show(self._steps, summary, "info")
        self._answer.setText("")
        return True

    def _ask(self) -> None:
        if not self._preview():
            return
        self._show(self._answer, "กำลังถาม...", "info")
        worker = FunctionWorker(time.monotonic(), self._service.respond, self._question.text().strip())
        worker.signals.finished.connect(self._on_answer)
        worker.signals.failed.connect(self._on_failed)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _on_answer(self, started: float, result: object) -> None:
        seconds = time.monotonic() - started
        note = _SOURCE_TEXT.get(result.source, result.source)
        if result.ai_error:
            note += f" (AI ขัดข้อง: {result.ai_error})"
        self._show(self._answer, f"{result.answer_text}\n[{note} · {seconds:.1f} วินาที]", "info")

    @Slot(object, str)
    def _on_failed(self, _started: float, _error: str) -> None:
        self._show(self._answer, "ถามไม่สำเร็จ ดูรายละเอียดใน Log", "error")

    @staticmethod
    def _show(label, text: str, level: str) -> None:
        label.setText(text)
        set_style_property(label, "level", level)
