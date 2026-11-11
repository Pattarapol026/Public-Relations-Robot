"""Admin page: the robot's name, personality, answer length and set phrases — each can be heard."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.content_controller import ContentController, ContentError
from app.speech_controller import SpeechController
from services.persona import (
    AFTERNOON_FROM,
    ANSWER_LENGTHS,
    EVENING_FROM,
    MAX_NAME_CHARS,
    MAX_PHRASE_CHARS,
    MORNING_FROM,
    Persona,
)
from ui.widgets import make_button, make_label, make_section, set_style_property

ROLE_PLACEHOLDER = (
    "ตัวอย่าง: พูดสุภาพแต่เป็นกันเอง สดใส ใช้คำง่าย ๆ ที่นักเรียนเข้าใจ "
    "ชวนให้มาสมัครเรียนเมื่อมีโอกาส"
)


class PersonaTab(QWidget):
    def __init__(self, content: ContentController, speech: SpeechController) -> None:
        super().__init__()
        self._content = content
        self._speech = speech

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        identity, identity_layout = make_section(
            "ตัวตนและบุคลิก",
            "ข้อความบุคลิกถูกส่งให้ AI ต่อท้ายกติกาหลักเสมอ จึงเปลี่ยนน้ำเสียงได้ "
            "แต่ไม่สามารถทำให้ AI ตอบนอกข้อมูลของสถานศึกษาหรือสั่งหุ่นยนต์ได้",
        )
        self._robot_name = QLineEdit()
        self._robot_name.setMaxLength(MAX_NAME_CHARS)
        self._role = QPlainTextEdit()
        self._role.setPlaceholderText(ROLE_PLACEHOLDER)
        self._role.setFixedHeight(100)
        self._role.setTabChangesFocus(True)
        self._length = QComboBox()
        for key, length in ANSWER_LENGTHS.items():
            self._length.addItem(length.label, key)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("ชื่อหุ่นยนต์", self._robot_name)
        form.addRow("บทบาทและบุคลิก", self._role)
        form.addRow("ความยาวคำตอบของ AI", self._length)
        identity_layout.addLayout(form)
        layout.addWidget(identity)

        greetings, greetings_layout = make_section(
            "คำทักทายเมื่อมีคนเข้ามา",
            f"เช้า {MORNING_FROM:02d}:00–{AFTERNOON_FROM - 1:02d}:59  ·  บ่าย {AFTERNOON_FROM:02d}:00–"
            f"{EVENING_FROM - 1:02d}:59  ·  เย็น {EVENING_FROM:02d}:00 เป็นต้นไป  —  "
            "ช่องที่เว้นว่างจะใช้คำทักทายทั่วไป",
        )
        self._greeting = self._phrase()
        self._morning = self._phrase("เว้นว่าง = ใช้คำทักทายทั่วไป")
        self._afternoon = self._phrase("เว้นว่าง = ใช้คำทักทายทั่วไป")
        self._evening = self._phrase("เว้นว่าง = ใช้คำทักทายทั่วไป")
        form = QFormLayout()
        form.setVerticalSpacing(10)
        for label, field in (
            ("ทั่วไป", self._greeting), ("ช่วงเช้า", self._morning),
            ("ช่วงบ่าย", self._afternoon), ("ช่วงเย็น", self._evening),
        ):
            form.addRow(label, self._with_listen(field))
        greetings_layout.addLayout(form)
        layout.addWidget(greetings)

        phrases, phrases_layout = make_section(
            "คำลาและเมื่อตอบไม่ได้",
            "คำลาพูดเมื่อผู้ที่เข้ามาคุยเดินออกไป (คนที่แค่เดินผ่านจะไม่ได้ยิน) เว้นว่างเพื่อไม่พูด",
        )
        self._farewell = self._phrase("เว้นว่าง = ไม่พูดคำลา")
        self._fallback = self._phrase()
        self._fallback_en = self._phrase()
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("คำลา", self._with_listen(self._farewell))
        form.addRow("ตอบไม่ได้ (ไทย)", self._with_listen(self._fallback))
        form.addRow("ตอบไม่ได้ (อังกฤษ)", self._with_listen(self._fallback_en))
        phrases_layout.addLayout(form)
        layout.addWidget(phrases)

        self._status = make_label("", "message", word_wrap=True)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("บันทึก", "primary", self._save))
        buttons.addWidget(make_button("ยกเลิกการแก้ไข", "secondary", self.discard_changes))
        buttons.addWidget(make_button("คืนค่าเริ่มต้น", "secondary", self._restore_defaults))
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(self._status)
        layout.addWidget(
            make_label(
                "มีผลกับคำถามถัดไปทันที ลองถามในหน้า \"ทดสอบ AI\" เพื่อดูข้อความทั้งหมดที่ส่งให้ AI และคำตอบที่ได้",
                "hint", word_wrap=True,
            )
        )
        layout.addStretch(1)
        self._load(content.persona())

    # --- building ---

    @staticmethod
    def _phrase(placeholder: str = "") -> QLineEdit:
        field = QLineEdit()
        field.setMaxLength(MAX_PHRASE_CHARS)
        field.setPlaceholderText(placeholder)
        return field

    def _with_listen(self, field: QLineEdit) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field, 1)
        row.addWidget(make_button("ฟัง", "secondary", lambda: self._listen(field)))
        return box

    # --- state ---

    def _shown(self) -> Persona:
        return Persona(
            robot_name=self._robot_name.text(),
            role=self._role.toPlainText(),
            answer_length=self._length.currentData(),
            fallback=self._fallback.text(),
            fallback_en=self._fallback_en.text(),
            greeting=self._greeting.text(),
            greeting_morning=self._morning.text(),
            greeting_afternoon=self._afternoon.text(),
            greeting_evening=self._evening.text(),
            farewell=self._farewell.text(),
        )

    def _load(self, persona: Persona) -> None:
        self._robot_name.setText(persona.robot_name)
        self._role.setPlainText(persona.role)
        self._length.setCurrentIndex(max(0, self._length.findData(persona.answer_length)))
        self._fallback.setText(persona.fallback)
        self._fallback_en.setText(persona.fallback_en)
        self._greeting.setText(persona.greeting)
        self._morning.setText(persona.greeting_morning)
        self._afternoon.setText(persona.greeting_afternoon)
        self._evening.setText(persona.greeting_evening)
        self._farewell.setText(persona.farewell)
        for field in self.findChildren(QLineEdit):
            field.setCursorPosition(0)  # long phrases show their beginning, not their end

    def has_unsaved_changes(self) -> bool:
        shown = self._shown()
        saved = self._content.persona()
        return any(
            getattr(shown, name).strip() != getattr(saved, name) for name in shown.__dataclass_fields__
        )

    def discard_changes(self) -> None:
        self._load(self._content.persona())
        self._show_status("")

    # --- actions ---

    def save_changes(self) -> bool:
        return self._save()

    def _save(self) -> bool:
        try:
            self._content.save_persona(self._shown())
        except ContentError as exc:
            self._show_status(str(exc), "error")
            return False
        self._load(self._content.persona())  # show it as stored (trimmed)
        self._show_status("บันทึกบุคลิกและบทสนทนาแล้ว มีผลกับคำถามถัดไป")
        return True

    def _restore_defaults(self) -> None:
        self._load(Persona())
        self._show_status("แสดงค่าเริ่มต้นแล้ว กดบันทึกเพื่อใช้งาน")

    def _listen(self, field: QLineEdit) -> None:
        text = field.text().strip()
        if not text:
            self._show_status("ช่องนี้ว่างอยู่ จึงไม่มีอะไรให้ฟัง", "error")
        elif self._speech.speak(text):
            self._show_status(f"กำลังพูด: {text}")
        else:
            self._show_status(f"พูดไม่ได้: เสียงพูด{self._speech.status_text}", "error")

    def _show_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        set_style_property(self._status, "level", level)
