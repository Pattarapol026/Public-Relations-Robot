"""Admin password hashing, verification and lockout.

The hash record is kept by a ``CredentialStore`` — in the app that is
``AdminUserRepository`` (table ``admin_users``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_KEY_LENGTH = 32


class CredentialStore(Protocol):
    def load(self) -> dict | None: ...
    def save(self, record: dict) -> None: ...


class AdminAuth:
    def __init__(
        self,
        store: CredentialStore,
        max_attempts: int = 5,
        lockout_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._max_attempts = max_attempts
        self._lockout_s = lockout_s
        self._clock = clock
        self._failed_attempts = 0
        self._locked_until = 0.0

    def is_configured(self) -> bool:
        return self._load() is not None

    def locked_remaining(self) -> float:
        return max(0.0, self._locked_until - self._clock())

    def set_password(self, password: str) -> None:
        """Store a new password hash. Raises ``ValueError`` if it is too short."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD_LENGTH} ตัวอักษร")
        salt = os.urandom(16)
        self._store.save(
            {
                "algorithm": "scrypt",
                **_SCRYPT,
                "salt": salt.hex(),
                "hash": _derive(password, salt, **_SCRYPT).hex(),
            }
        )
        logger.info("Admin password updated")

    def verify(self, password: str) -> bool:
        if self.locked_remaining() > 0:
            return False
        record = self._load()
        if record is None:
            return False
        try:
            expected = bytes.fromhex(record["hash"])
            actual = _derive(
                password, bytes.fromhex(record["salt"]),
                n=record["n"], r=record["r"], p=record["p"],
            )
        except (KeyError, TypeError, ValueError):
            logger.error("Admin credential record is malformed")
            return False
        if hmac.compare_digest(actual, expected):
            self._failed_attempts = 0
            logger.info("Admin login succeeded")
            return True

        self._failed_attempts += 1
        logger.warning("Admin login failed (%d/%d)", self._failed_attempts, self._max_attempts)
        if self._failed_attempts >= self._max_attempts:
            self._failed_attempts = 0
            self._locked_until = self._clock() + self._lockout_s
            logger.warning("Admin login locked for %.0f s", self._lockout_s)
        return False

    def _load(self) -> dict | None:
        record = self._store.load()
        if not isinstance(record, dict) or record.get("algorithm") != "scrypt":
            return None
        return record


def migrate_legacy_credentials(legacy_path: Path, store: CredentialStore) -> bool:
    """Move the V0.1 ``admin_credentials.json`` hash into ``store``, once."""
    if not legacy_path.is_file():
        return False
    try:
        record = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.error("Legacy admin credential file is unreadable: %s", legacy_path)
        return False
    if not isinstance(record, dict) or record.get("algorithm") != "scrypt":
        logger.error("Legacy admin credential file has an unknown format: %s", legacy_path)
        return False
    if store.load() is None:
        store.save(record)
        logger.info("Admin credential migrated from %s into the database", legacy_path.name)
    legacy_path.unlink(missing_ok=True)
    return True


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_KEY_LENGTH)
