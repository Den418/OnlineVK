"""Переменные окружения.

Обязателен только ключ доступа сообщества (бот живёт в сообществе ВК).
Всё остальное настраивается прямо в боте и хранится в базе данных.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else 0


# Ключ доступа СООБЩЕСТВА: на нём работает бот.
VK_TOKEN = os.getenv("VK_TOKEN", "").strip()

# Необязательно: отдельный ключ (пользователя или сервисный ключ приложения)
# для чтения статуса, если ключ сообщества «онлайн» не видит.
STATUS_TOKEN = os.getenv("VK_STATUS_TOKEN", "").strip()

API_VERSION = os.getenv("VK_API_VERSION", "").strip() or "5.199"

# Необязательно: твой id ВК. Если не задан, владельцем станет тот, кто пришлёт код привязки.
ENV_OWNER_ID = _int("OWNER_ID")

# Здесь лежит база (tracker.db): настройки и история заходов.
DATA_DIR = Path(os.getenv("DATA_DIR", "").strip() or "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
