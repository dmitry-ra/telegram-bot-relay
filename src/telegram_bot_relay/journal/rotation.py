"""Cron rotation in a configured timezone, without empty periods."""

from datetime import datetime
from zoneinfo import ZoneInfo

from cronsim import CronSim


class Schedule:
    def __init__(self, expression: str, timezone: str, now: datetime) -> None:
        self.expression = expression
        self.zone = ZoneInfo(timezone)
        self.next = next(CronSim(expression, now.astimezone(self.zone)))

    def due(self, now: datetime) -> bool:
        if now < self.next:
            return False
        self.next = next(CronSim(self.expression, now.astimezone(self.zone)))
        return True
