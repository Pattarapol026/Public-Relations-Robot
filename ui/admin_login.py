"""Admin login dialog; doubles as first-run password setup."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from admin.auth import MIN_PASSWORD_LENGTH, AdminAuth
from ui.widgets import make_button, make_label


class AdminLoginDialog(QDialog):
    def __init__(self, auth: AdminAuth, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._setup_mode = not auth.is_configured()
        self.setWindowTitle("ผู้ดูแลระบบ")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        if self._setup_mode:
            title = "ตั้งรหัสผ่านผู้ดูแลระบบ (ครั้งแรก)"
            hint = f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD_LENGTH} ตัวอักษร"
        else:
            title = "เข้าสู่โหมดผู้ดูแลระบบ"
            hint = "กรุณากรอกรหัสผ่าน"
        layout.addWidget(make_label(title, "heading"))
        layout.addWidget(make_label(hint, "hint"))

        self._password = self._password_field("รหัสผ่าน")
        layout.addWidget(self._password)
        self._confirm = self._password_field("ยืนยันรหัสผ่าน")
        self._confirm.setVisible(self._setup_mode)
        layout.addWidget(self._confirm)

        self._error = make_label("", "error", word_wrap=True)
        layout.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addWidget(make_button("ยกเลิก", "secondary", self.reject))
        buttons.addWidget(make_button("ตกลง", "primary", self._submit))
        layout.addLayout(buttons)

        self._password.returnPressed.connect(self._submit)
        self._confirm.returnPressed.connect(self._submit)

    def _password_field(self, placeholder: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(placeholder)
        return field

    def _submit(self) -> None:
        password = self._password.text()
        if self._setup_mode:
            if password != self._confirm.text():
                self._error.setText("รหัสผ่านทั้งสองช่องไม่ตรงกัน")
                return
            try:
                self._auth.set_password(password)
            except ValueError as exc:
                self._error.setText(str(exc))
                return
            except OSError:
                self._error.setText("บันทึกรหัสผ่านไม่สำเร็จ กรุณาตรวจสอบสิทธิ์ของโฟลเดอร์ข้อมูล")
                return
            self.accept()
            return

        remaining = self._auth.locked_remaining()
        if remaining > 0:
            self._error.setText(f"ใส่รหัสผิดหลายครั้ง กรุณารออีก {int(remaining) + 1} วินาที")
        elif self._auth.verify(password):
            self.accept()
        else:
            self._error.setText("รหัสผ่านไม่ถูกต้อง")
        self._password.clear()
