"""Форматирование времени и длительности, разбор часовых поясов."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)


def parse_tz(value: str) -> tzinfo | None:
    """'Europe/Warsaw', 'UTC', '+3', 'UTC+05:30' -> tzinfo. Не распознали -> None."""
    value = (value or "").strip()
    if not value:
        return None
    if value.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    m = _OFFSET.match(value)
    if m:
        hours, minutes = int(m.group(2)), int(m.group(3) or 0)
        if hours > 14 or minutes > 59:
            return None
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(delta if m.group(1) == "+" else -delta)
    try:
        return ZoneInfo(value)
    except Exception:  # нет такого пояса / нет базы tzdata / мусор на входе
        return None


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def local(dt: datetime, tz: tzinfo) -> datetime:
    return as_utc(dt).astimezone(tz)


def fmt_time(dt: datetime, tz: tzinfo) -> str:
    return local(dt, tz).strftime("%H:%M:%S")


def fmt_dt(dt: datetime, tz: tzinfo) -> str:
    return local(dt, tz).strftime("%d.%m %H:%M:%S")


def fmt_dur(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    hours, rest = divmod(s, 3600)
    minutes, sec = divmod(rest, 60)
    if hours:
        return f"{hours}ч {minutes:02d}м {sec:02d}с"
    if minutes:
        return f"{minutes}м {sec:02d}с"
    return f"{sec}с"
