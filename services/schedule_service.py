"""Decides which scheduled public relations messages are due.

A schedule runs once a day, on its chosen weekdays, at its time. If the robot
is busy at that moment (someone is talking to it) the schedule stays due for a
grace period and runs as soon as the robot is free; after that it is skipped
for the day rather than interrupting much later.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Sequence

from database.models import Schedule

GRACE_MINUTES = 10
ALL_WEEKDAYS = "0123456"  # 0 = Monday
WEEKDAY_LABELS = ("จ", "อ", "พ", "พฤ", "ศ", "ส", "อา")

_TIME = re.compile(r"([01]?\d|2[0-3])[:.]([0-5]\d)")


def normalize_time(text: str) -> str | None:
    """'8.30' or '08:30' -> '08:30'; None if it is not a time of day."""
    match = _TIME.fullmatch(text.strip())
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else None


def normalize_weekdays(digits: str) -> str:
    return "".join(day for day in ALL_WEEKDAYS if day in digits)


def describe_weekdays(digits: str) -> str:
    if normalize_weekdays(digits) == ALL_WEEKDAYS:
        return "ทุกวัน"
    if normalize_weekdays(digits) == "01234":
        return "จันทร์–ศุกร์"
    return " ".join(WEEKDAY_LABELS[int(day)] for day in normalize_weekdays(digits))


def due_schedules(schedules: Sequence[Schedule], now: datetime) -> list[Schedule]:
    today = now.strftime("%Y-%m-%d")
    due = []
    for schedule in schedules:
        if not schedule.enabled or schedule.last_run_date == today:
            continue
        if str(now.weekday()) not in schedule.weekdays:
            continue
        time_of_day = normalize_time(schedule.time_of_day)
        if time_of_day is None:
            continue
        hour, minute = (int(part) for part in time_of_day.split(":"))
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if start <= now < start + timedelta(minutes=GRACE_MINUTES):
            due.append(schedule)
    return due
