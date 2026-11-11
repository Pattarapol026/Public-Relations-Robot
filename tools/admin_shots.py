r"""Renders every Admin page to a PNG (offscreen), to look at the layout without a display.

    .venv313\Scripts\python.exe tools\admin_shots.py OUTPUT_DIR [WIDTHxHEIGHT]

Runs the real application on a COPY of data/app.db in a temporary folder (your data is not
touched), with the microphone, speech, camera and AI switched off.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from app.application import Application  # noqa: E402
from config.settings import load_settings  # noqa: E402
from ui.fonts import apply_app_font  # noqa: E402
from ui.log_bridge import QtLogHandler  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    width, height = (1280, 720)
    if len(sys.argv) > 2:
        width, height = (int(part) for part in sys.argv[2].lower().split("x"))
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    with tempfile.TemporaryDirectory() as temp:
        data = Path(temp) / "data"
        data.mkdir()
        if (ROOT / "data" / "app.db").exists():
            shutil.copy(ROOT / "data" / "app.db", data / "app.db")
        settings = load_settings(environ={
            "DATA_DIR": str(data), "LOG_DIR": str(Path(temp) / "logs"), "MEDIA_DIR": str(Path(temp) / "media"),
            "STT_ENABLED": "false", "TTS_ENABLED": "false", "VISION_ENABLED": "false", "AI_ENABLED": "false",
            "MQTT_HW_HOST": "127.0.0.1", "MQTT_HW_ENABLED": "false",
        })
        app = Application(settings, QtLogHandler())
        window = app.window
        window.resize(width, height)
        window.show()
        window.enter_admin()
        admin = window.admin_screen
        for _ in range(5):
            qt_app.processEvents()
        manifest = []
        for index, (_group, title, _description) in enumerate(admin._page_info):
            admin.select_page(index)
            sub = admin._segmented.get(index)
            count = len(sub._buttons) if sub is not None else 1
            for sub_index in range(count):
                if sub is not None:
                    sub.show_sub_page(sub_index)
                for _ in range(8):
                    qt_app.processEvents()
                name = f"{index:02d}{'abcdef'[sub_index] if sub is not None else ''}.png"
                window.grab().save(str(out / name))
                # What lies below the fold: the whole content of the page's scroll area.
                page = admin._pages.currentWidget()
                if sub is not None:
                    page = sub._stack.currentWidget()
                area = page if isinstance(page, QScrollArea) else page.findChild(QScrollArea)
                if area is not None and area.widget() is not None:
                    inner = area.widget()
                    if inner.height() > area.viewport().height() + 4:
                        inner.grab().save(str(out / name.replace(".png", "_full.png")))
                label = title + (f" / {sub._buttons[sub_index].text()}" if sub is not None else "")
                manifest.append(f"{name}  {label}")
        # Dialogs inherit the Admin sheet only when parented inside it: look at two of them.
        from PySide6.QtWidgets import QMessageBox
        from ui.admin_content import KnowledgeTab
        from ui.form_dialog import FormDialog

        def show_and_grab(self, *_args):
            self.show()
            self.adjustSize()
            for _ in range(8):
                qt_app.processEvents()
            self.grab().save(str(out / "dialog_form.png"))
            self.hide()
            return 0

        FormDialog.exec = show_and_grab
        admin.select_page(3)
        admin._segmented[3].show_sub_page(0)
        admin.findChildren(KnowledgeTab)[0]._open_form(None)
        box = QMessageBox(QMessageBox.Icon.Question, "ยืนยันการลบ", "ลบ \"ตัวอย่างคำถาม\" ใช่หรือไม่",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, admin)
        box.show()
        for _ in range(8):
            qt_app.processEvents()
        box.grab().save(str(out / "dialog_message.png"))
        box.hide()
        (out / "manifest.txt").write_text(chr(10).join(manifest), encoding="utf-8")
        print(len(manifest), "screens ->", out)
        app.shutdown()


if __name__ == "__main__":
    main()
