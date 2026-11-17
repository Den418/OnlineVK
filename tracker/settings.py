"""Настройки хранятся в базе и меняются прямо из бота."""
from __future__ import annotations

from datetime import timezone, tzinfo

from . import config
from .db import Database
from .fmt import parse_tz

DEFAULTS = {
    "tz": "UTC",
    "poll_seconds": "15",       # как часто спрашивать статус у ВК, сек
    "alert_per_hour": "6",      # предупреждать о частых заходах (0 = выкл)
    "notify_online": "1",       # слать «зашёл в сеть»
    "notify_offline": "1",      # слать «вышел + сколько был в сети»
    "notify_alerts": "1",       # слать предупреждение о частых заходах
    "paused": "0",
}
TOGGLES = ("notify_online", "notify_offline", "notify_alerts")


class Settings:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> str:
        return self.db.get(key, DEFAULTS.get(key, ""))

    def set(self, key: str, value: object) -> None:
        self.db.set(key, value)

    def delete(self, key: str) -> None:
        self.db.delete(key)

    def flag(self, key: str) -> bool:
        return self.get(key) == "1"

    def toggle(self, key: str) -> bool:
        new = not self.flag(key)
        self.set(key, "1" if new else "0")
        return new

    def _int(self, key: str) -> int:
        try:
            return max(0, int(self.get(key)))
        except ValueError:
            return int(DEFAULTS.get(key, "0"))

    @property
    def poll_seconds(self) -> int:
        return self._int("poll_seconds")

    @property
    def alert_per_hour(self) -> int:
        return self._int("alert_per_hour")

    @property
    def tz(self) -> tzinfo:
        return parse_tz(self.get("tz")) or timezone.utc

    @property
    def owner_id(self) -> int:
        return config.ENV_OWNER_ID or self._int("owner_id")
