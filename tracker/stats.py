"""Статистика заходов и история (обычный текст: ВК не поддерживает разметку в сообщениях)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

from .db import Database
from .fmt import fmt_dt, fmt_dur, fmt_time, local


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


def _summary(rows: list) -> str:
    if not rows:
        return "заходов не было"
    durations = [end - start for start, end in rows]
    short = sum(1 for d in durations if d < 1)        # заходы короче интервала опроса: длительность неизвестна
    measured = [d for d in durations if d >= 1]
    parts = [f"заходов: {len(rows)}"]
    if short:
        parts.append(f"коротких: {short}")
    if measured:
        parts += [
            f"в сети: {fmt_dur(sum(measured))}",
            f"самый долгий: {fmt_dur(max(measured))}",
            f"средний: {fmt_dur(sum(measured) / len(measured))}",
        ]
    return ", ".join(parts)


def stats_text(db: Database, tz: tzinfo, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    midnight = local(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    periods = (
        ("Сегодня", midnight.timestamp()),
        ("7 дней", (now - timedelta(days=7)).timestamp()),
        ("Всё время", 0.0),
    )
    lines = ["📊 Статистика заходов", ""]
    for title, since in periods:
        lines.append(f"{title} - {_summary(db.sessions_since(since))}")
    return "\n".join(lines)


def history_text(db: Database, tz: tzinfo, limit: int = 10) -> str:
    rows = db.last_sessions(limit)
    if not rows:
        return "📜 История\n\nПока пусто: ни одного завершённого захода."
    lines = [f"📜 Последние заходы (до {limit})", ""]
    for start, end in rows:
        lines.append(f"{fmt_dt(_dt(start), tz)} → {fmt_time(_dt(end), tz)}  ({fmt_dur(end - start)})")
    return "\n".join(lines)
