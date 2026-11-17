"""Минимальный асинхронный клиент VK API и Bots Long Poll (aiohttp)."""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from .errors import VkError

log = logging.getLogger(__name__)

API_URL = "https://api.vk.com/method/"
LONG_POLL_WAIT = 25  # сек: сколько сервер держит запрос, пока нет событий


class VkApi:
    def __init__(self, token: str, version: str, session: aiohttp.ClientSession) -> None:
        self.token = token
        self.version = version
        self.session = session

    async def call(self, method: str, **params):
        """Вызов метода VK API. None-параметры отбрасываются. Ошибка -> VkError."""
        data = {k: str(v) for k, v in params.items() if v is not None}
        data["access_token"] = self.token
        data["v"] = self.version
        for attempt in range(5):
            async with self.session.post(API_URL + method, data=data) as resp:
                payload = await resp.json(content_type=None)
            error = payload.get("error")
            if not error:
                return payload.get("response")
            code = int(error.get("error_code", 0))
            if code == 6:  # слишком много запросов в секунду: чуть ждём и повторяем
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            raise VkError(code, str(error.get("error_msg", "")))
        raise VkError(6, "Too many requests per second")

    async def group_id(self) -> int:
        """id сообщества, которому принадлежит ключ."""
        res = await self.call("groups.getById")
        groups = res.get("groups") if isinstance(res, dict) else res
        return int(groups[0]["id"])

    async def long_poll(self, group_id: int):
        """Бесконечный поток событий Bots Long Poll: async for update in api.long_poll(...)."""
        server = await self.call("groups.getLongPollServer", group_id=group_id)
        key, url, ts = server["key"], server["server"], server["ts"]
        while True:
            try:
                async with self.session.get(
                    url,
                    params={"act": "a_check", "key": key, "ts": ts, "wait": LONG_POLL_WAIT},
                    timeout=aiohttp.ClientTimeout(total=LONG_POLL_WAIT + 15),
                ) as resp:
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await asyncio.sleep(2)
                continue
            failed = data.get("failed")
            if failed == 1:      # история устарела: берём новый ts
                ts = data["ts"]
                continue
            if failed in (2, 3):  # 2: ключ истёк, 3: данные потеряны (нужны новые key и ts)
                server = await self.call("groups.getLongPollServer", group_id=group_id)
                key = server["key"]
                if failed == 3:
                    ts = server["ts"]
                continue
            ts = data.get("ts", ts)
            for update in data.get("updates", []):
                yield update
