"""Changing the Gemini API key from Admin: try it, store it in .env, use it without a restart."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from ai.gemini_client import GeminiClient
from app.logging_setup import register_secret
from app.workers import FunctionWorker
from config.settings import Settings
from services.conversation_service import ConversationService

logger = logging.getLogger(__name__)

ENV_KEY = "GEMINI_API_KEY"
MIN_KEY_CHARS, MAX_KEY_CHARS = 20, 200
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-.]+$")
_ERROR_TEXT = {
    "auth": "API key ไม่ถูกต้องหรือไม่มีสิทธิ์ใช้โมเดลนี้",
    "quota": "key ใช้ได้ แต่ใช้งานเกินโควตาหรือถี่เกินไป",
    "network": "เชื่อมต่ออินเทอร์เน็ตไม่ได้",
    "timeout": "AI ตอบช้าเกินเวลาที่กำหนด",
    "server": "ระบบของ Google ขัดข้องชั่วคราว",
    "request": "คำขอถูกปฏิเสธ (ตรวจชื่อโมเดลใน GEMINI_MODEL)",
    "empty": "AI ไม่ส่งข้อความกลับมา",
    "unavailable": "ยังไม่ได้ติดตั้ง google-genai",
}


class ApiKeyError(Exception):
    """The key or the .env file cannot be used; the message is safe to show."""


def write_env_value(path: Path, name: str, value: str) -> None:
    """Set ``name=value`` in a .env file, keeping every other line (and comment) as it was."""
    if "\n" in value or "\r" in value:
        raise ValueError("value must be one line")
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def masked(key: str) -> str:
    return f"••••••••{key[-4:]}" if key else ""


class AiKeyController(QObject):
    changed = Signal()  # the key in use changed
    test_finished = Signal(bool, str)  # works?, message for the Admin

    def __init__(
        self,
        settings: Settings,
        conversation_service: ConversationService,
        env_path: Path | None,
        pool: QThreadPool,
        client_factory: Callable[[str], GeminiClient] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._service = conversation_service
        self._env_path = env_path
        self._pool = pool
        self._key = settings.gemini_api_key
        self._factory = client_factory or (
            lambda key: GeminiClient(key, settings.gemini_model, settings.gemini_timeout_s)
        )
        self._testing = 0

    @property
    def can_edit(self) -> bool:
        """False when the program was not started with a .env file to write to."""
        return self._env_path is not None

    @property
    def ai_enabled(self) -> bool:
        return self._settings.ai_enabled

    @property
    def masked_key(self) -> str:
        return masked(self._key)

    def test(self, key: str) -> None:
        """Try ``key`` with one real request; ``test_finished`` follows. Raises ``ApiKeyError``."""
        key = self._checked(key)
        register_secret(key)
        self._testing += 1
        worker = FunctionWorker(self._testing, self._service.check_ai, self._factory(key))
        worker.signals.finished.connect(self._on_test_ok)
        worker.signals.failed.connect(self._on_test_failed)
        self._pool.start(worker)

    def save(self, key: str) -> None:
        """Store ``key`` in .env and use it from the next question. Raises ``ApiKeyError``."""
        key = self._checked(key)
        self._write(key)
        register_secret(key)
        self._key = key
        if self._settings.ai_enabled:
            self._service.set_gemini(self._factory(key))
        logger.info("Gemini API key replaced from Admin (%s)", masked(key))
        self.changed.emit()

    def remove(self) -> None:
        """Forget the key: answers come from the knowledge base only. Raises ``ApiKeyError``."""
        self._write("")
        self._key = ""
        self._service.set_gemini(None)
        logger.info("Gemini API key removed from Admin")
        self.changed.emit()

    def _checked(self, key: str) -> str:
        key = key.strip()
        if not MIN_KEY_CHARS <= len(key) <= MAX_KEY_CHARS or not _KEY_PATTERN.match(key):
            raise ApiKeyError("รูปแบบ API key ไม่ถูกต้อง (คัดลอกจาก Google AI Studio ทั้งข้อความ)")
        return key

    def _write(self, key: str) -> None:
        if self._env_path is None:
            raise ApiKeyError("โปรแกรมไม่ได้เปิดจากไฟล์ .env จึงบันทึก key จากหน้านี้ไม่ได้")
        try:
            write_env_value(self._env_path, ENV_KEY, key)
        except OSError:
            raise ApiKeyError(f"บันทึกไฟล์ {self._env_path} ไม่สำเร็จ") from None

    @Slot(object, object)
    def _on_test_ok(self, token: int, reply: object) -> None:
        if token == self._testing:
            self.test_finished.emit(True, f"ใช้งานได้ — AI ตอบว่า: {reply}")

    @Slot(object, str)
    def _on_test_failed(self, token: int, error: str) -> None:
        if token == self._testing:
            kind = error.split(":", 1)[0]
            self.test_finished.emit(False, f"ใช้งานไม่ได้ — {_ERROR_TEXT.get(kind, 'ดูรายละเอียดในหน้า Log')}")
