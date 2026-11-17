"""Разбор ответа users.get: онлайн / офлайн / скрыт / недоступен. Ссылки на страницы."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

_URL = re.compile(r"^(?:https?://)?(?:m\.|www\.)?(?:vk\.com|vk\.ru|vkontakte\.ru)/", re.IGNORECASE)
_MENTION = re.compile(r"^\[([A-Za-z0-9_.]+)\|.*\]$")


@dataclass
class VkStatus:
    kind: str                           # online | offline | hidden | gone
    last_seen: datetime | None = None   # для offline: время последней активности
    where: str = ""                     # для online: откуда зашёл
    detail: str = ""                    # для gone: banned / deleted


def _where(app, mobile: bool) -> str:
    if app and mobile:
        return f"с телефона, приложение {app}"
    if app:
        return f"приложение {app}"
    return "с телефона" if mobile else "с сайта"


def parse_status(user: dict) -> VkStatus:
    """Ответ users.get (fields=online,last_seen) -> VkStatus."""
    if user.get("deactivated"):
        return VkStatus("gone", detail=str(user["deactivated"]))

    info = user.get("online_info")
    info = info if isinstance(info, dict) else None
    seen = user.get("last_seen")
    seen = seen if isinstance(seen, dict) else {}
    last = seen.get("time") or (info or {}).get("last_seen")

    if info is not None and info.get("visible") is False:
        return VkStatus("hidden")
    if info is not None and "is_online" in info:
        online, app, mobile = bool(info["is_online"]), info.get("app_id"), bool(info.get("is_mobile"))
    else:
        online = bool(user.get("online"))
        app, mobile = user.get("online_app"), bool(user.get("online_mobile"))

    if online:
        return VkStatus("online", where=_where(app, mobile))
    if not last:
        return VkStatus("hidden")  # не в сети, но времени визита нет: ВК его скрыл
    return VkStatus("offline", last_seen=datetime.fromtimestamp(int(last), timezone.utc))


def parse_ref(text: str):
    """Ссылка, короткое имя или id -> строка для users.get(user_ids=...). Не поняли -> None."""
    t = (text or "").strip()
    m = _MENTION.match(t)
    if m:
        t = m.group(1)
    t = _URL.sub("", t)
    t = t.split("?")[0].split("#")[0].strip("/ @")
    if not t:
        return None
    m = re.fullmatch(r"id(\d+)", t, re.IGNORECASE)
    if m:
        return m.group(1)
    if t.isdigit():
        return t
    if re.fullmatch(r"[A-Za-z0-9_.]{2,32}", t):
        return t
    return None
