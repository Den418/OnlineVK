"""Запуск: python -m tracker"""
import asyncio
import logging
import secrets

import aiohttp

from . import config
from .bot import Bot
from .db import Database
from .engine import Engine
from .errors import VkError
from .settings import Settings
from .vkapi import VkApi
from .watcher import Watcher

log = logging.getLogger("tracker")


async def main() -> None:
    if not config.VK_TOKEN:
        raise SystemExit("Не задан VK_TOKEN. Скопируйте .env в .env и вставьте ключ доступа сообщества.")

    db = Database(config.DATA_DIR / "tracker.db")
    settings = Settings(db)

    async with aiohttp.ClientSession() as session:
        api = VkApi(config.VK_TOKEN, config.API_VERSION, session)
        status_api = VkApi(config.STATUS_TOKEN, config.API_VERSION, session) if config.STATUS_TOKEN else api

        try:
            group_id = await api.group_id()
        except VkError as e:
            raise SystemExit(
                f"ВК не принял VK_TOKEN ({e.message}). Нужен ключ доступа СООБЩЕСТВА "
                f"(Управление → Работа с API → Ключи доступа) с правом на сообщения."
            )
        try:  # включаем Long Poll и событие «входящее сообщение», если у ключа хватает прав
            await api.call(
                "groups.setLongPollSettings",
                group_id=group_id, enabled=1, api_version=config.API_VERSION, message_new=1,
            )
        except VkError as e:
            log.warning("Не получилось включить Long Poll автоматически (%s). Включи вручную: "
                        "Управление → Работа с API → Long Poll API.", e.message)

        async def notify(text: str) -> None:
            await bot.notify(text)

        engine = Engine(db, settings, notify)
        watcher = Watcher(settings, engine, status_api)
        bot = Bot(api, settings, db, engine, watcher)

        if not settings.owner_id:
            code = settings.get("claim_code") or secrets.token_hex(4)
            settings.set("claim_code", code)
            print(
                f"\n{'=' * 54}\n"
                f"  Бот ещё не привязан к владельцу.\n"
                f"  Напиши сообществу в ВК и отправь ему этот код:\n\n"
                f"      {code}\n"
                f"{'=' * 54}\n",
                flush=True,
            )

        await watcher.restore()
        if settings.owner_id and watcher.ready:
            try:
                await bot.notify("♻️ Трекер запущен\n" + engine.status_text(settings.get("target_name")))
            except Exception as e:
                log.warning("Не удалось написать владельцу (он уже писал сообществу?): %s", e)

        log.info("Бот запущен")
        await asyncio.gather(bot.run(group_id), watcher.poll_loop())


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
