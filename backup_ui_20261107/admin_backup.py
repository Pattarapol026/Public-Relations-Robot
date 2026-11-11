"""Admin page: back up the database, and carry the settings to another robot as a file."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from app.content_controller import ContentController, ContentError
from app.tuning import TuningController
from config.settings import Settings
from ui.widgets import make_button, make_label, make_section, set_style_property


class BackupTab(QWidget):
    config_imported = Signal()

    def __init__(self, settings: Settings, content: ContentController, tuning: TuningController) -> None:
        super().__init__()
        self._content = content
        self._tuning = tuning

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        database, database_layout = make_section(
            "สำรองฐานข้อมูล",
            "เก็บสำเนาทั้งหมด (สื่อที่ลงทะเบียน ประกาศ ฐานความรู้ สถิติ และการตั้งค่า) ไว้ที่ "
            f"{settings.data_dir / 'backups'} ทำได้ขณะหุ่นยนต์ทำงานอยู่",
        )
        row = QHBoxLayout()
        row.addWidget(make_button("สำรองฐานข้อมูลตอนนี้", "primary", self._backup))
        row.addStretch(1)
        database_layout.addLayout(row)
        layout.addWidget(database)

        config, config_layout = make_section(
            "ย้ายการตั้งค่าไปเครื่องอื่น",
            "ไฟล์การตั้งค่า (.json) มีบุคลิกหุ่นยนต์ คำทักทาย ความเร็วพูด ความไวไมโครโฟน ระยะกล้อง "
            "และการตั้งค่าหน้าจอ ไม่มีรหัสผ่านและไม่มี API key นำไปเปิดในหุ่นยนต์อีกตัวเพื่อตั้งค่าให้เหมือนกันได้ทันที",
        )
        row = QHBoxLayout()
        row.addWidget(make_button("ส่งออกการตั้งค่า", "secondary", self._export))
        row.addWidget(make_button("นำเข้าการตั้งค่า", "secondary", self._import))
        row.addStretch(1)
        config_layout.addLayout(row)
        layout.addWidget(config)

        knowledge, _knowledge_layout = make_section(
            "ฐานความรู้",
            "นำเข้าและส่งออกคำถาม-คำตอบเป็นไฟล์ CSV (แก้ไขใน Excel ได้) ที่หน้า \"ฐานความรู้\"",
        )
        layout.addWidget(knowledge)

        self._status = make_label("", "message", word_wrap=True)
        layout.addWidget(self._status)
        layout.addStretch(1)

    def _backup(self) -> None:
        try:
            target = self._content.backup_database()
        except Exception:
            self._show_status("สำรองฐานข้อมูลไม่สำเร็จ ดูรายละเอียดในหน้า Log", "error")
            raise
        self._show_status(f"สำรองฐานข้อมูลไว้ที่ {target}")

    def _export(self) -> None:
        file_name, _filter = QFileDialog.getSaveFileName(
            self, "ส่งออกการตั้งค่า", "robot-settings.json", "JSON (*.json)"
        )
        if not file_name:
            return
        try:
            count = self._content.export_config(Path(file_name))
        except OSError:
            self._show_status("บันทึกไฟล์ไม่สำเร็จ", "error")
            return
        self._show_status(f"ส่งออกการตั้งค่า {count} ค่าไปที่ {file_name}")

    def _import(self) -> None:
        file_name, _filter = QFileDialog.getOpenFileName(
            self, "นำเข้าการตั้งค่า", "", "JSON (*.json)"
        )
        if not file_name:
            return
        answer = QMessageBox.question(
            self, "ยืนยัน", "การตั้งค่าในไฟล์จะแทนที่ค่าปัจจุบันของเครื่องนี้ ต้องการนำเข้าหรือไม่"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.import_from(Path(file_name))

    def import_from(self, path: Path) -> bool:
        try:
            count = self._content.import_config(path)
        except ContentError as exc:
            self._show_status(str(exc), "error")
            return False
        self._tuning.reload()
        self.config_imported.emit()
        self._show_status(f"นำเข้าการตั้งค่า {count} ค่าแล้ว มีผลทันที")
        return True

    def _show_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        set_style_property(self._status, "level", level)
