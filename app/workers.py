"""Run blocking functions off the GUI thread and report back through Qt signals.

Connect the signals to bound methods of QObjects living in the GUI thread so
Qt queues the call onto that thread; a bare lambda would run in the worker.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = Signal(object, object)  # token, result
    failed = Signal(object, str)  # token, error text


class FunctionWorker(QRunnable):
    """Calls ``fn(*args)``; ``token`` lets the receiver match or discard the result."""

    def __init__(self, token: Any, fn: Callable[..., Any], *args: Any) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._token = token
        self._fn = fn
        self._args = args

    def run(self) -> None:
        try:
            result = self._fn(*self._args)
        except Exception as exc:
            logger.exception("Background task failed")
            self._emit(self.signals.failed, str(exc))
        else:
            self._emit(self.signals.finished, result)

    def _emit(self, signal: Any, payload: Any) -> None:
        try:
            signal.emit(self._token, payload)
        except RuntimeError:
            # The signal source was destroyed because the app is shutting down.
            pass
