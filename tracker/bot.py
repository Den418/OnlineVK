"""Бот ВКонтакте: меню, настройки и выбор страницы. Всё делается кнопками в диалоге с сообществом."""
from __future__ import annotations

import hmac
import json
import logging
import random

from . import stats
from .errors import TargetError
from .fmt import fmt_time, parse_tz
from .settings import TOGGLES

log = logging.getLogger(__name__)

# ───────────────────────────── тексты (ВК: только обычный текст) ─────────────────────────────
LOCKED = "🔒 Это приватный бот."
CLAIM_HINT = (
    "🔒 Бот ещё не привязан к владельцу.\n\n"
    "Отправь сюда код привязки одним сообщением. Он выведен в консоли при запуске бота "
    "(в Docker: docker compose logs)."
)
TARGET_PROMPT = (
    "👤 Страница для слежения\n\n"
    "Пришли ссылку на страницу ВК (например vk.com/durov), короткое имя или числовой id.\n\n"
    "На этой странице должно стоять: Настройки → Приватность → «Кто видит меня в сети» → "
    "«Все пользователи». Иначе ВК не покажет статус."
)
TZ_PROMPT = (
    "🕒 Часовой пояс\n\n"
    "Пришли название пояса, например Europe/Warsaw, или смещение: +3, -5:30."
)
HELP = (
    "❓ Как это работает\n\n"
    "Бот раз в несколько секунд спрашивает у ВК, в сети ли выбранная страница, и пишет сюда: "
    "зашла, вышла, сколько была в сети. Входить в аккаунт не нужно.\n\n"
    "Чтобы ВК показал статус, на отслеживаемой странице: Настройки → Приватность → "
    "«Кто видит меня в сети» → «Все пользователи».\n\n"
    "Если бот пишет «Точный статус скрыт», дело в этой настройке. Если она верная, а статус "
    "всё равно скрыт, задай VK_STATUS_TOKEN (см. README).\n\n"
    "Точность примерно равна интервалу опроса. ВК может гасить «онлайн» не сразу, поэтому "
    "длительность это время по статусу, который видят и другие, а не лог подключений скрипта."
)

TZ_CHOICES = (
    "UTC", "Europe/Kyiv", "Europe/Moscow", "Europe/Minsk",
    "Europe/Warsaw", "Europe/Berlin", "Asia/Almaty", "Asia/Dubai",
)
POLL_OPTIONS = (5, 15, 30, 60, 120)
ALERT_OPTIONS = (3, 6, 10, 20, 0)
START_WORDS = {"/start", "start", "начать", "/menu", "menu", "меню"}


# ───────────────────────────── клавиатуры ─────────────────────────────
def kb(*rows) -> str:
    """kb([("Текст", "действие"), ("Текст", "действие", "значение", "цвет")], ...) -> JSON-клавиатура.

    Обычная клавиатура ВК: до 10 рядов по 5 кнопок, подпись до 40 символов.
    Цвета: primary (синий), secondary (белый), positive (зелёный), negative (красный).
    """
    buttons = []
    for row in rows:
        line = []
        for b in row:
            label, action = b[0], b[1]
            value = b[2] if len(b) > 2 else ""
            color = b[3] if len(b) > 3 else "secondary"
            payload = {"a": action, "v": value} if value else {"a": action}
            line.append(
                {
                    "action": {
                        "type": "text",
                        "label": label[:40],
                        "payload": json.dumps(payload, ensure_ascii=False),
                    },
                    "color": color,
                }
            )
        buttons.append(line)
    return json.dumps({"one_time": False, "inline": False, "buttons": buttons}, ensure_ascii=False)


def cancel_kb(back: str = "settings") -> str:
    return kb([("❌ Отмена", back, "", "negative")])


# ───────────────────────────── бот ─────────────────────────────
class Bot:
    def __init__(self, api, settings, db, engine, watcher) -> None:
        self.api, self.s, self.db, self.eng, self.w = api, settings, db, engine, watcher
        self.state = None  # None | "target" | "tz": ждём текстовый ввод от владельца

    # ---------- отправка ----------
    async def send(self, peer_id: int, text: str, keyboard: str | None = None) -> None:
        await self.api.call(
            "messages.send",
            peer_id=peer_id,
            random_id=random.getrandbits(31),
            message=text[:4000],
            keyboard=keyboard,
        )

    async def notify(self, text: str) -> None:
        if self.s.owner_id:
            await self.send(self.s.owner_id, text)

    # ---------- приём сообщений ----------
    async def run(self, group_id: int) -> None:
        async for update in self.api.long_poll(group_id):
            try:
                await self.handle(update)
            except Exception:
                log.exception("Не удалось обработать сообщение")

    async def handle(self, update: dict) -> None:
        if update.get("type") != "message_new":
            return
        obj = update.get("object") or {}
        msg = obj.get("message", obj)  # разные версии API отдают событие по-разному
        uid, peer = msg.get("from_id"), msg.get("peer_id")
        if not uid or uid != peer:  # только личные сообщения
            return
        text = (msg.get("text") or "").strip()
        owner = self.s.owner_id
        if not owner:
            await self._claim(uid, text)
        elif uid != owner:
            await self.send(uid, LOCKED)
        else:
            await self._owner(uid, text, self._payload(msg))

    @staticmethod
    def _payload(msg: dict) -> dict:
        raw = msg.get("payload")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return {}
        return raw if isinstance(raw, dict) else {}

    async def _claim(self, uid: int, text: str) -> None:
        """Привязка владельца: первый, кто пришлёт код из консоли."""
        code = self.s.get("claim_code")
        words = text.split()
        if code and words and hmac.compare_digest(words[-1].encode(), code.encode()):
            self.s.set("owner_id", uid)
            self.s.delete("claim_code")
            screen, keyboard = self.home_screen()
            await self.send(uid, f"✅ Готово, бот привязан к тебе.\n\n{screen}", keyboard)
        else:
            await self.send(uid, CLAIM_HINT)

    async def _owner(self, uid: int, text: str, payload: dict) -> None:
        action, value = payload.get("a"), str(payload.get("v", ""))
        if payload.get("command") == "start":  # кнопка ВК «Начать»
            action = "home"
        if not action:
            word = text.lower()
            if word in START_WORDS:
                action = "home"
            elif word in ("/status", "статус"):
                action = "status"
            elif word in ("/stats", "статистика"):
                action = "stats"
            elif word in ("/settings", "настройки"):
                action = "settings"
        if not action and self.state:
            await self._input(uid, text)
            return
        self.state = None  # любая кнопка или команда обрывает незаконченный ввод
        screen, keyboard = await self._screen(action or "home", value)
        await self.send(uid, screen, keyboard)

    async def _input(self, uid: int, text: str) -> None:
        if self.state == "target":
            try:
                await self.w.set_target(text)
            except TargetError as e:
                await self.send(uid, f"❌ {e}\n\nПопробуй ещё раз или нажми «Отмена».", cancel_kb())
                return
            self.state = None
            screen, keyboard = self.home_screen()
            await self.send(uid, f"✅ Теперь слежу за {self.s.get('target_name')}.\n\n{screen}", keyboard)
        elif self.state == "tz":
            if parse_tz(text) is None:
                await self.send(
                    uid, "Не знаю такой пояс. Пример: Europe/Warsaw или +3.", cancel_kb("tz")
                )
                return
            self.s.set("tz", text.strip())
            self.state = None
            screen, keyboard = self.tz_screen()
            await self.send(uid, screen, keyboard)

    async def _safe_sync(self, startup: bool = False) -> None:
        try:
            await self.w.sync(startup=startup)
        except Exception as e:
            log.warning("Не удалось обновить статус: %s", e)

    async def _screen(self, a: str, v: str):
        """Действие кнопки -> (текст, клавиатура)."""
        s = self.s
        if a in ("home", "status"):
            await self._safe_sync()
            return self.home_screen()
        if a == "stats":
            return self.stats_screen()
        if a == "history":
            return stats.history_text(self.db, s.tz), kb([("⬅️ К статистике", "stats")])
        if a == "pause":
            if s.toggle("paused"):
                self.eng.reset()
            else:
                await self._safe_sync(startup=True)
            return self.home_screen()
        if a == "settings":
            return self.settings_screen()
        if a == "help":
            return HELP, kb([("⬅️ Назад", "settings")])
        if a == "poll":
            return self.poll_screen()
        if a == "setpoll":
            if v.isdigit():
                s.set("poll_seconds", int(v))
            return self.poll_screen()
        if a == "alert":
            return self.alert_screen()
        if a == "setalert":
            if v.isdigit():
                s.set("alert_per_hour", int(v))
            return self.alert_screen()
        if a == "notify":
            return self.notify_screen()
        if a == "tog":
            if v in TOGGLES:
                s.toggle(v)
            return self.notify_screen()
        if a == "tz":
            return self.tz_screen()
        if a == "settz":
            if parse_tz(v) is None:
                return "Этот пояс недоступен на сервере.", kb([("⬅️ Назад", "tz")])
            s.set("tz", v)
            return self.tz_screen()
        if a == "tzmanual":
            self.state = "tz"
            return TZ_PROMPT, cancel_kb("tz")
        if a == "target":
            self.state = "target"
            return TARGET_PROMPT, cancel_kb()
        return self.home_screen()

    # ---------- экраны: (текст, клавиатура) ----------
    def home_screen(self):
        paused = self.s.flag("paused")
        if not self.w.ready:
            state = "👤 Осталось выбрать страницу, за которой следить."
        elif paused:
            state = "⏸ Слежение на паузе."
        else:
            state = self.eng.status_text(self.s.get("target_name"))
        rows = []
        if not self.w.ready:
            rows.append([("👤 Выбрать страницу", "target", "", "positive")])
        else:
            rows.append([("🔄 Обновить", "home", "", "primary"), ("📊 Статистика", "stats")])
            rows.append([("▶️ Продолжить" if paused else "⏸ Пауза", "pause")])
        rows.append([("⚙️ Настройки", "settings")])
        return f"📡 VK Online Tracker\n\n{state}", kb(*rows)

    def stats_screen(self):
        return stats.stats_text(self.db, self.s.tz), kb(
            [("📜 История", "history")],
            [("⬅️ Назад", "home")],
        )

    def settings_screen(self):
        s = self.s
        poll = f"{s.poll_seconds} с" if s.poll_seconds else "выкл"
        alert = f"{s.alert_per_hour} за час" if s.alert_per_hour else "выкл"
        text = (
            "⚙️ Настройки\n\n"
            f"👤 Следим за: {s.get('target_name') or 'не выбрана'}\n"
            f"⏱ Опрос: каждые {poll}\n"
            f"⚠️ Предупреждать о частых заходах: {alert}\n"
            f"🕒 Часовой пояс: {s.get('tz')}"
        )
        return text, kb(
            [("👤 Страница", "target"), ("⏱ Опрос", "poll")],
            [("⚠️ Порог", "alert"), ("🔔 Уведомления", "notify")],
            [("🕒 Часовой пояс", "tz"), ("❓ Помощь", "help")],
            [("⬅️ Назад", "home")],
        )

    def poll_screen(self):
        cur = self.s.poll_seconds
        btns = [(("✅ " if cur == n else "") + f"{n} с", "setpoll", str(n)) for n in POLL_OPTIONS]
        text = (
            "⏱ Как часто спрашивать ВК\n\n"
            "У ВК нет мгновенных уведомлений об онлайне, поэтому бот сам проверяет статус раз в N "
            "секунд. Чаще - точнее время захода, но больше запросов."
        )
        return text, kb(btns[:3], btns[3:], [("⬅️ Назад", "settings")])

    def alert_screen(self):
        cur = self.s.alert_per_hour
        btns = [(("✅ " if cur == n else "") + (str(n) if n else "выкл"), "setalert", str(n))
                for n in ALERT_OPTIONS]
        text = (
            "⚠️ Порог частых заходов\n\n"
            "Если страница заходит в сеть столько раз за час или чаще, пришлю предупреждение "
            "(не чаще раза в час). Полезно, чтобы заметить, что скрипт слишком часто подключается."
        )
        return text, kb(btns[:3], btns[3:], [("⬅️ Назад", "settings")])

    def notify_screen(self):
        def mark(key, label):
            return [(("✅ " if self.s.flag(key) else "▫️ ") + label, "tog", key)]

        return "🔔 Уведомления\n\nЧто присылать:", kb(
            mark("notify_online", "🟢 Заход в сеть"),
            mark("notify_offline", "🔴 Выход и время в сети"),
            mark("notify_alerts", "⚠️ Частые заходы"),
            [("⬅️ Назад", "settings")],
        )

    def tz_screen(self):
        cur = self.s.get("tz")
        btns = [(("✅ " if cur == z else "") + z.split("/")[-1], "settz", z) for z in TZ_CHOICES]
        text = f"🕒 Часовой пояс\n\nСейчас: {cur}, на часах {fmt_time(self.eng.clock(), self.s.tz)}"
        return text, kb(
            btns[0:2], btns[2:4], btns[4:6], btns[6:8],
            [("✏️ Ввести вручную", "tzmanual")],
            [("⬅️ Назад", "settings")],
        )
