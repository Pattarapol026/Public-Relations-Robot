"""Runs scheduled public relations messages when their time comes and the robot is free."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from database.models import Schedule
from database.repositories import ScheduleRepository
from services.schedule_service import due_schedules

logger = logging.getLogger(__name__)

CHECK_MS = 15_000


class ScheduleController(QObject):
    def __init__(
        self,
        repository: ScheduleRepository,
        clock: Callable[[], datetime] = datetime.now,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._clock = clock
        # Set by the main window: performs the announcement and returns True, or
        # returns False if the robot is busy (the schedule is then tried again).
        self.perform: Callable[[Schedule], bool] = lambda schedule: False

        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    def check(self) -> None:
        now = self._clock()
        for schedule in due_schedules(self._repository.list(), now):
            if self.perform(schedule):
                self._repository.mark_run(schedule.id, now.strftime("%Y-%m-%d"))
                logger.info("Scheduled announcement delivered: %s", schedule.title)
                break  # one at a time; another due one runs on the next check

    def shutdown(self) -> None:
        self._timer.stop()
