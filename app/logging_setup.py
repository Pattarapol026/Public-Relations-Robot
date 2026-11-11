"""Logging configuration: rotating file + console, with secret redaction."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5


# Secrets that became known after start-up (an API key entered in Admin).
_LATE_SECRETS: list[str] = []


def register_secret(secret: str) -> None:
    """Keep ``secret`` out of every log written from now on."""
    if secret and secret not in _LATE_SECRETS:
        _LATE_SECRETS.append(secret)


class SecretRedactionFilter(logging.Filter):
    """Replace known secret values with a placeholder before a record is written."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = self._secrets + _LATE_SECRETS
        if secrets:
            message = record.getMessage()
            for secret in secrets:
                message = message.replace(secret, "***")
            record.msg = message
            record.args = None
        return True


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    secrets: Iterable[str] = (),
    extra_handlers: Iterable[logging.Handler] = (),
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)
    redaction = SecretRedactionFilter(secrets)

    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_dir / "app.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        ),
        logging.StreamHandler(),
        *extra_handlers,
    ]
    root = logging.getLogger()
    root.setLevel(level)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redaction)
        root.addHandler(handler)

    def log_uncaught(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("app").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = log_uncaught
