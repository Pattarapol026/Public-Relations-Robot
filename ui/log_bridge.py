"""Forwards log records to the GUI through a Qt signal (safe from any thread).

Warnings and errors are also kept in a short list, so the Admin dashboard can
show what went wrong recently without opening the log file. Every record is
also sorted into a category (safety, connection, command, ...) so the Admin
can find a kind of problem quickly; the log file itself is unchanged.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal

MAX_PROBLEMS = 50

CATEGORY_SAFETY = "safety"
CATEGORY_CONNECTION = "connection"
CATEGORY_COMMAND = "command"
CATEGORY_ERROR = "error"
CATEGORY_WARNING = "warning"
CATEGORY_INFO = "info"
# Shown in this order in the Admin's filters, with these names.
CATEGORY_LABELS = {
    CATEGORY_ERROR: "ข้อผิดพลาด",
    CATEGORY_WARNING: "คำเตือน",
    CATEGORY_SAFETY: "ความปลอดภัย",
    CATEGORY_CONNECTION: "การเชื่อมต่อ",
    CATEGORY_COMMAND: "คำสั่งหุ่นยนต์",
    CATEGORY_INFO: "ข้อมูล",
}

# Safety: the proximity rule refusing or stopping a movement, or any stop request.
_SAFETY = re.compile(
    r"source=safety|person_too_close|distance_unknown|sensor_unavailable|STOP requested|"
    r"Safety interlock|reason=person"
)
# Connection: a device or service that could not be reached or went away.
_CONNECTION = re.compile(
    r"Camera unavailable|Camera process|camera stopped|Restarting the camera|"
    r"robot_disconnected|connection_lost|not connected|Could not read robot status|"
    r"Could not open the microphone|network|timed? ?out|unreachable",
    re.IGNORECASE,
)
_COMMAND_LOGGERS = ("robot.", "app.robot_controller", "app.command_controller")


def classify(level: int, source: str, message: str) -> str:
    """The category of one log record (most specific first)."""
    if _SAFETY.search(message):
        return CATEGORY_SAFETY
    if _CONNECTION.search(message) and level >= logging.WARNING:
        return CATEGORY_CONNECTION
    if source.startswith(_COMMAND_LOGGERS) and "Command" in message:
        return CATEGORY_COMMAND
    if level >= logging.ERROR:
        return CATEGORY_ERROR
    if level >= logging.WARNING:
        return CATEGORY_WARNING
    return CATEGORY_INFO


@dataclass(frozen=True)
class LogProblem:
    time: datetime
    level: str  # "WARNING" | "ERROR" | "CRITICAL"
    source: str  # logger name
    message: str  # first line only; the traceback stays in the log file
    category: str = CATEGORY_WARNING


@dataclass(frozen=True)
class LogEntry:
    """One formatted record, as shown on the Admin's Log page."""

    level: str
    category: str
    text: str  # the full formatted line(s), as written to the log file


class _LogEmitter(QObject):
    message = Signal(str)
    entry = Signal(object)  # LogEntry
    problem = Signal(object)  # LogProblem


class QtLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.emitter = _LogEmitter()
        self._problems: deque[LogProblem] = deque(maxlen=MAX_PROBLEMS)

    def recent_problems(self) -> list[LogProblem]:
        """Newest first."""
        return list(reversed(self._problems))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            message = record.getMessage()
            category = classify(record.levelno, record.name, message)
            self.emitter.message.emit(text)
            self.emitter.entry.emit(LogEntry(record.levelname, category, text))
            if record.levelno >= logging.WARNING:
                problem = LogProblem(
                    datetime.fromtimestamp(record.created),
                    record.levelname,
                    record.name,
                    message.splitlines()[0] if message else "",
                    category,
                )
                self._problems.append(problem)
                self.emitter.problem.emit(problem)
        except RuntimeError:
            # The emitter is gone because the app is shutting down.
            pass
