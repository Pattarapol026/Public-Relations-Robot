"""Security page section: replace, test or remove the Gemini API key."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QMessageBox

from app.ai_key import AiKeyController, ApiKeyError
from ui.widgets import make_button, make_label, make_section, set_style_property


def build_api_key_section(ai_key: AiKeyController) -> QFrame:
    section, layout = make_section(
        "API key ของ Gemini",
        "key ถูกเก็บในไฟล์ .env ของเครื่องนี้เท่านั้น ไม่แสดงเต็มบนหน้าจอ ไม่ถูกบันทึกลง log "
        "และไม่อยู่ในไฟล์การตั้งค่าที่ส่งออก ขอ key ได้ที่ Google AI Studio",
    )
    current = make_label("", "list-text")
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setPlaceholderText("วาง API key ใหม่ที่นี่")
    status = make_label("", "message", word_wrap=True)

    def show(text: str, level: str = "info") -> None:
        status.setText(text)
        set_style_property(status, "level", level)

    def show_current() -> None:
        if ai_key.masked_key:
            text = f"key ที่ใช้อยู่: {ai_key.masked_key}"
        else:
            text = "ยังไม่ได้ตั้งค่า key — ตอบจากฐานความรู้อย่างเดียว"
        if not ai_key.ai_enabled:
            text += "  (ปิด AI อยู่ด้วย AI_ENABLED=false ใน .env)"
        current.setText(text)

    def test() -> None:
        try:
            ai_key.test(field.text())
        except ApiKeyError as exc:
            show(str(exc), "error")
            return
        show("กำลังทดสอบ key ...")

    def save() -> None:
        try:
            ai_key.save(field.text())
        except ApiKeyError as exc:
            show(str(exc), "error")
            return
        field.clear()
        show("บันทึก key ใหม่แล้ว ใช้ตั้งแต่คำถามถัดไป")

    def remove() -> None:
        answer = QMessageBox.question(
            section, "ยืนยัน", "ลบ API key ออกจากเครื่องนี้ใช่หรือไม่ หุ่นยนต์จะตอบจากฐานความรู้อย่างเดียว"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            ai_key.remove()
        except ApiKeyError as exc:
            show(str(exc), "error")
            return
        show("ลบ key แล้ว")

    layout.addWidget(current)
    if ai_key.can_edit:
        layout.addWidget(field)
        row = QHBoxLayout()
        row.addWidget(make_button("ทดสอบ key นี้", "secondary", test))
        row.addWidget(make_button("บันทึก key", "primary", save))
        row.addStretch(1)
        row.addWidget(make_button("ลบ key", "secondary", remove))
        layout.addLayout(row)
    else:
        layout.addWidget(make_label("แก้ไข GEMINI_API_KEY ได้ในไฟล์ .env", "hint"))
    layout.addWidget(status)

    ai_key.changed.connect(show_current)
    ai_key.test_finished.connect(lambda ok, message: show(message, "info" if ok else "error"))
    show_current()
    # Kept reachable for tests and for clearing when the Admin leaves.
    section.key_field = field
    section.key_status = status
    return section
