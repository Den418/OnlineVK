"""Наблюдатель: раз в N секунд спрашивает у ВК статус страницы и передаёт его движку.

В отличие от Telegram, входить в аккаунт не нужно: статус «онлайн» открыт любому
ключу доступа, если на странице разрешено «Кто видит меня в сети: Все пользователи».
"""
from __future__ import annotations

import asyncio
import logging

from .engine import Engine
from .errors import TargetError, VkError
from .settings import Settings
from .vkstatus import VkStatus, parse_ref, parse_status

log = logging.getLogger(__name__)


class Watcher:
    def __init__(self, settings: Settings, engine: Engine, api) -> None:
        self.s = settings
        self.engine = engine
        self.api = api  # VkApi, которым читаем статус

    @property
    def ready(self) -> bool:
        return bool(self.s.get("target_id"))

    async def _fetch(self, user_ids) -> list:
        return await self.api.call("users.get", user_ids=user_ids, fields="online,last_seen") or []

    async def set_target(self, text: str):
        """Выбрать страницу по ссылке, короткому имени или id."""
        ref = parse_ref(text)
        if ref is None:
            raise TargetError("Не понял. Пришли ссылку на страницу (vk.com/durov), короткое имя или числовой id.")
        try:
            users = await self._fetch(ref)
        except VkError as e:
            if e.code in (100, 113):
                raise TargetError("Не нашёл такую страницу. Нужна страница человека, не сообщество.")
            raise TargetError(f"ВК вернул ошибку: {e.message}")
        if not users:
            raise TargetError("Не нашёл такую страницу. Нужна страница человека, не сообщество.")
        user = users[0]
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or f"id{user['id']}"
        self.s.set("target_ref", ref)
        self.s.set("target_id", user["id"])
        self.s.set("target_name", name)
        self.engine.reset()
        await self._dispatch(parse_status(user), startup=True)
        return user

    async def restore(self) -> None:
        """При старте: спросить текущий статус ранее выбранной страницы."""
        if self.ready:
            await self._safe_sync(startup=True)

    async def sync(self, startup: bool = False) -> None:
        if not self.ready:
            return
        users = await self._fetch(self.s.get("target_id"))
        if users:
            await self._dispatch(parse_status(users[0]), startup)

    async def _safe_sync(self, startup: bool = False) -> None:
        try:
            await self.sync(startup=startup)
        except Exception as e:
            log.warning("Не удалось получить статус: %s", e)

    async def _dispatch(self, st: VkStatus, startup: bool = False) -> None:
        if st.kind == "online":
            await self.engine.online(where=st.where, startup=startup)
        elif st.kind == "offline":
            await self.engine.offline(st.last_seen, startup=startup)
        elif st.kind == "gone":
            await self.engine.unavailable(st.detail, startup=startup)
        else:
            await self.engine.hidden_status(startup=startup)

    async def poll_loop(self) -> None:
        """Главный цикл слежения: раз в poll_seconds сверяем статус."""
        waited = 0
        while True:
            await asyncio.sleep(1)
            waited += 1
            interval = self.s.poll_seconds
            if not interval or waited < interval:
                continue
            waited = 0
            if not self.ready or self.s.flag("paused"):
                continue
            try:
                await self.sync()
            except VkError as e:
                log.warning("Опрос статуса не удался: %s", e)
                if e.code == 5:
                    log.error("ВК не принял ключ (код 5). Проверь VK_TOKEN / VK_STATUS_TOKEN.")
                    await asyncio.sleep(30)
            except Exception as e:
                log.warning("Опрос статуса не удался: %s", e)
