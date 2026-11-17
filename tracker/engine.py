"""Ядро: превращает смену статуса «в сети / не в сети» в уведомления и историю заходов.

Про ВКонтакте здесь ничего не известно: наблюдатель (watcher.py) просто сообщает,
что страница стала онлайн / офлайн / скрыла статус / стала недоступна.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .db import Database
from .fmt import as_utc, fmt_dt, fmt_dur, fmt_time
from .settings import Settings

log = logging.getLogger(__name__)

# Офлайн-статус старше текущей сессии считаем запоздавшим ответом и игнорируем.
STALE = timedelta(seconds=15)

# Новое «был в сети» в пределах этого срока после прошлого считаем уточнением, а не новым заходом.
REVISIT_GAP = timedelta(seconds=30)

GONE_TEXT = {"banned": "заблокирована или заморожена", "deleted": "удалена"}

Notify = Callable[[str], Awaitable[None]]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Engine:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        notify: Notify,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.db = db
        self.s = settings
        self._notify = notify
        self.clock = clock
        self.lock = asyncio.Lock()
        self.reset()

    def reset(self) -> None:
        """Забыть текущее состояние (смена страницы, пауза)."""
        self.online_since = None   # datetime, если сейчас в сети
        self.online_where = ""     # откуда зашёл: «с телефона», «через приложение ...»
        self.approx = False        # сессия началась до запуска трекера: старт неточный
        self.last_seen = None      # когда страница последний раз вышла из сети
        self.hidden = False        # ВК скрывает точный статус (приватность)
        self.gone = None           # «banned» / «deleted», если страница недоступна
        self.recent_starts = deque()
        self.last_alert = None

    # ---------- события от наблюдателя ----------
    async def online(self, *, where: str = "", startup: bool = False) -> None:
        async with self.lock:
            if self.s.flag("paused"):
                return
            await self._back(startup)
            self.hidden = False
            if self.online_since is not None:
                return  # «онлайн» просто продлили, это не новый заход
            now = self.clock()
            self.online_since, self.approx, self.online_where = now, startup, where
            if startup:
                return
            if self.s.flag("notify_online"):
                head = f"🟢 В сети с {fmt_time(now, self.s.tz)}" + (f" ({where})" if where else "")
                lines = [head]
                if self.last_seen is not None:
                    pause = fmt_dur((now - self.last_seen).total_seconds())
                    lines.append(f"до этого не был в сети: {pause}")
                await self._send("\n".join(lines))
            await self._check_burst(now)

    async def offline(self, was_online: datetime, *, startup: bool = False) -> None:
        was_online = as_utc(was_online)
        async with self.lock:
            if self.s.flag("paused"):
                return
            await self._back(startup)
            self.hidden = False
            if self.online_since is not None and was_online < self.online_since - STALE:
                return  # запоздавший ответ со статусом из прошлого
            prev = self.last_seen
            if prev is None or was_online > prev:
                self.last_seen = was_online
            tz = self.s.tz

            if self.online_since is None:
                # «Онлайн» на наших проверках не попался, но «был в сети» стал заметно новее:
                # это заход короче интервала опроса (ВК не шлёт событий, мы только опрашиваем).
                fresh = prev is not None and was_online - prev > REVISIT_GAP
                if fresh and not startup:
                    self.db.add_session(was_online.timestamp(), was_online.timestamp())
                    if self.s.flag("notify_online"):
                        await self._send(
                            f"⚡ Короткий заход: была активность в {fmt_time(was_online, tz)}\n"
                            "(короче интервала опроса, «онлайн» между проверками не попался)"
                        )
                    await self._check_burst(self.clock())
                return

            start, approx, where = self.online_since, self.approx, self.online_where
            self.online_since, self.approx, self.online_where = None, False, ""
            end = max(was_online, start)
            seconds = (end - start).total_seconds()
            if not approx:
                self.db.add_session(start.timestamp(), end.timestamp())
            if not self.s.flag("notify_offline"):
                return
            if approx:
                text = (
                    f"🔴 Вышел из сети в {fmt_time(end, tz)}\n"
                    f"⏱ в сети: не меньше {fmt_dur(seconds)} (заход был до запуска трекера)"
                )
            else:
                how = f", {where}" if where else ""
                text = (
                    f"🔴 Вышел из сети в {fmt_time(end, tz)}\n"
                    f"⏱ в сети: {fmt_dur(seconds)} (с {fmt_time(start, tz)}{how})"
                )
            await self._send(text)

    async def hidden_status(self, *, startup: bool = False) -> None:
        """ВК не отдаёт онлайн страницы: он скрыт приватностью (или ключ его не видит)."""
        async with self.lock:
            if self.s.flag("paused"):
                return
            await self._back(startup)
            if self.hidden:
                return
            self.hidden, self.online_since, self.approx = True, None, False
            if not startup:
                await self._send(
                    "🙈 Точный статус скрыт\n"
                    "ВК не отдаёт онлайн этой страницы. На ней должно стоять: Настройки → Приватность → "
                    "«Кто видит меня в сети» → «Все пользователи». Если так и есть, см. VK_STATUS_TOKEN в README."
                )

    async def unavailable(self, kind: str, *, startup: bool = False) -> None:
        """Страница заблокирована, заморожена или удалена. Об этом пишем всегда."""
        async with self.lock:
            if self.s.flag("paused") or self.gone == kind:
                return
            self.gone, self.online_since, self.approx, self.hidden = kind, None, False, False
            if not startup:
                what = GONE_TEXT.get(kind, kind)
                await self._send(f"🚫 Страница недоступна: {what}")

    # ---------- тексты ----------
    def status_text(self, name: str) -> str:
        now, tz, name = self.clock(), self.s.tz, name or "Страница"
        if self.gone is not None:
            return f"🚫 {name}: страница недоступна ({GONE_TEXT.get(self.gone, self.gone)})"
        if self.hidden:
            return f"🙈 Статус {name} скрыт настройками приватности"
        if self.online_since is not None:
            since = fmt_dur((now - self.online_since).total_seconds())
            prefix = "не меньше " if self.approx else ""
            where = f" ({self.online_where})" if self.online_where else ""
            return f"🟢 {name} в сети уже {prefix}{since}{where}"
        if self.last_seen is not None:
            ago = fmt_dur((now - self.last_seen).total_seconds())
            return f"🔴 {name} не в сети. Был: {fmt_dt(self.last_seen, tz)} ({ago} назад)"
        return f"🔴 {name} не в сети"

    # ---------- внутреннее ----------
    async def _back(self, startup: bool) -> None:
        """Страница снова доступна после блокировки или удаления."""
        if self.gone is not None:
            self.gone = None
            if not startup:
                await self._send("✅ Страница снова доступна")

    async def _check_burst(self, now: datetime) -> None:
        self.recent_starts.append(now)
        hour_ago = now - timedelta(hours=1)
        while self.recent_starts and self.recent_starts[0] < hour_ago:
            self.recent_starts.popleft()
        count, limit = len(self.recent_starts), self.s.alert_per_hour
        quiet = self.last_alert is None or now - self.last_alert >= timedelta(hours=1)
        if limit and count >= limit and quiet:
            self.last_alert = now
            if self.s.flag("notify_alerts"):
                await self._send(f"⚠️ Частые заходы: {count} за последний час")

    async def _send(self, text: str) -> None:
        log.info(text.replace("\n", " | "))
        try:
            await self._notify(text)
        except Exception:
            log.exception("Не удалось отправить уведомление (владелец уже писал сообществу?)")
