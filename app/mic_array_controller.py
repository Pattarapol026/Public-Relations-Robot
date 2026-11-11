"""Keeps track of whether the microphone array is plugged in, so the screen can say so.

Checked every few seconds in the thread pool (reading Windows' device list takes tens of
milliseconds), so plugging the board in or out shows within seconds, with no restart.
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from app.workers import FunctionWorker

logger = logging.getLogger(__name__)

POLL_MS = 3000


class MicArrayController(QObject):
    changed = Signal()  # plugged in, or unplugged

    def __init__(
        self,
        detector: Callable[[], str | None],
        pool: QThreadPool,
        parent: QObject | None = None,
    ) -> None:
        """``detector`` returns the device's name while it is present, else None."""
        super().__init__(parent)
        self._detector = detector
        self._pool = pool
        self._device: str | None = None
        self._busy = False
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    @property
    def connected(self) -> bool:
        return self._device is not None

    @property
    def device(self) -> str | None:
        return self._device

    def start(self) -> None:
        self._stopped = False
        self._poll()
        self._timer.start()

    def shutdown(self) -> None:
        self._stopped = True
        self._timer.stop()

    def _poll(self) -> None:
        if self._busy or self._stopped:
            return
        self._busy = True
        worker = FunctionWorker(None, self._detector)
        worker.signals.finished.connect(self._on_result)
        worker.signals.failed.connect(self._on_failed)
        self._pool.start(worker)

    @Slot(object, object)
    def _on_result(self, _token: object, device: object) -> None:
        self._busy = False
        if self._stopped:
            return
        device = device or None
        if device != self._device:
            logger.info("Microphone array: %s", f"connected ({device})" if device else "not connected")
            self._device = device
            self.changed.emit()

    @Slot(object, str)
    def _on_failed(self, _token: object, _error: str) -> None:
        self._busy = False
        if self._device is not None:
            self._device = None
            self.changed.emit()
