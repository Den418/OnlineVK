"""Тесты бота на заглушке VK API: привязка, выбор страницы, уведомления, настройки, клавиатуры."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ["DATA_DIR"] = tempfile.mkdtemp()

from tracker.bot import LOCKED, Bot  # noqa: E402
from tracker.db import Database  # noqa: E402
from tracker.engine import Engine  # noqa: E402
from tracker.errors import VkError  # noqa: E402
from tracker.fmt import parse_tz  # noqa: E402
from tracker.settings import Settings  # noqa: E402
from tracker.watcher import Watcher  # noqa: E402

T0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
TS0 = int(T0.timestamp())


class FakeApi:
    """Вместо настоящего VK API: запоминает отправленное и отдаёт заготовленные users.get."""

    def __init__(self):
        self.sent = []   # (peer_id, message, keyboard)
        self.users = {}  # str(user_ids) -> ответ users.get

    async def call(self, method, **params):
        if method == "messages.send":
            self.sent.append((params["peer_id"], params["message"], params.get("keyboard")))
            return 1
        if method == "users.get":
            key = str(params["user_ids"])
            if key not in self.users:
                raise VkError(113, "Invalid user id")
            return self.users[key]
        raise AssertionError(f"неожиданный метод {method}")


def person(**extra):
    return [{"id": 1, "first_name": "Павел", "last_name": "Дуров", **extra}]


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = FakeApi()
        self.db = Database(":memory:")
        self.s = Settings(self.db)
        self.now = T0
        self.bot = None

        async def notify(text):
            await self.bot.notify(text)

        self.eng = Engine(self.db, self.s, notify, clock=lambda: self.now)
        self.w = Watcher(self.s, self.eng, self.api)
        self.bot = Bot(self.api, self.s, self.db, self.eng, self.w)
        self.api.users["durov"] = person(online=0, last_seen={"time": TS0 - 600, "platform": 7})
        self.api.users["1"] = self.api.users["durov"]

    async def say(self, uid, text="", payload=None, peer=None):
        msg = {"from_id": uid, "peer_id": peer or uid, "text": text}
        if payload is not None:
            msg["payload"] = json.dumps(payload)
        await self.bot.handle({"type": "message_new", "object": {"message": msg, "client_info": {}}})

    def last(self):
        return self.api.sent[-1]

    def check_keyboard(self, raw):
        kb = json.loads(raw)
        rows = kb["buttons"]
        self.assertLessEqual(len(rows), 10)
        self.assertLessEqual(sum(len(r) for r in rows), 40)
        for row in rows:
            self.assertLessEqual(len(row), 5)
            for button in row:
                action = button["action"]
                self.assertEqual(action["type"], "text")
                self.assertLessEqual(len(action["label"]), 40)
                self.assertLessEqual(len(action["payload"]), 255)
                json.loads(action["payload"])
                self.assertIn(button["color"], ("primary", "secondary", "positive", "negative"))

    async def test_claim_owner(self):
        self.s.set("claim_code", "abc12345")
        await self.say(7, "привет")
        self.assertIn("не привязан", self.last()[1])
        self.assertEqual(self.s.owner_id, 0)
        await self.say(7, "Начать abc12345")
        self.assertEqual(self.s.owner_id, 7)
        self.assertIn("бот привязан", self.last()[1])
        self.assertEqual(self.s.get("claim_code"), "")
        await self.say(8, "привет")
        self.assertEqual(self.last(), (8, LOCKED, None))

    async def test_group_chats_ignored(self):
        self.s.set("owner_id", 7)
        await self.say(7, "Начать", peer=2000000001)
        self.assertEqual(self.api.sent, [])

    async def test_select_page_and_notifications(self):
        self.s.set("owner_id", 7)
        await self.say(7, "Начать")
        self.assertIn("Выбрать страницу", self.last()[2])

        await self.say(7, "Выбрать страницу", {"a": "target"})
        self.assertEqual(self.bot.state, "target")
        await self.say(7, "https://vk.com/durov")
        self.assertIn("Теперь слежу за Павел Дуров", self.last()[1])
        self.assertTrue(self.w.ready)
        self.assertIsNone(self.bot.state)

        # заходит с телефона
        self.api.users["1"] = person(online=1, online_mobile=1)
        await self.w.sync()
        peer, text, _ = self.last()
        self.assertEqual(peer, 7)
        self.assertIn("В сети", text)
        self.assertIn("с телефона", text)
        self.assertIn("10м 00с", text)

        # выходит через 125 секунд
        self.api.users["1"] = person(online=0, last_seen={"time": TS0 + 125, "platform": 4})
        await self.w.sync()
        self.assertIn("2м 05с", self.last()[1])

    async def test_bad_target(self):
        self.s.set("owner_id", 7)
        await self.say(7, "", {"a": "target"})
        await self.say(7, "vk.com/club1")           # ВК: нет такого пользователя
        self.assertIn("Не нашёл", self.last()[1])
        self.assertEqual(self.bot.state, "target")
        await self.say(7, "привет")                 # не ссылка
        self.assertIn("Не понял", self.last()[1])
        self.assertEqual(self.bot.state, "target")
        await self.say(7, "durov")
        self.assertIn("Теперь слежу", self.last()[1])

    async def test_all_screens_have_valid_keyboards(self):
        self.s.set("owner_id", 7)
        self.api.users["durov"] = person(online=0, last_seen={"time": TS0, "platform": 7})
        await self.say(7, "", {"a": "target"})
        await self.say(7, "durov")
        actions = [
            ("home", ""), ("stats", ""), ("history", ""), ("settings", ""), ("help", ""),
            ("poll", ""), ("setpoll", "30"), ("alert", ""), ("setalert", "10"),
            ("notify", ""), ("tog", "notify_online"), ("tz", ""), ("settz", "UTC"),
            ("tzmanual", ""), ("target", ""), ("pause", ""), ("pause", ""),
        ]
        for action, value in actions:
            payload = {"a": action, "v": value} if value else {"a": action}
            await self.say(7, "кнопка", payload)
            self.assertIsNotNone(self.last()[2], action)
            self.check_keyboard(self.last()[2])
        self.assertEqual(self.s.poll_seconds, 30)
        self.assertEqual(self.s.alert_per_hour, 10)
        self.assertFalse(self.s.flag("notify_online"))
        self.assertFalse(self.s.flag("paused"))

    async def test_timezone_input(self):
        self.s.set("owner_id", 7)
        await self.say(7, "", {"a": "tzmanual"})
        self.assertEqual(self.bot.state, "tz")
        await self.say(7, "Nope/Nope")
        self.assertIn("Не знаю такой пояс", self.last()[1])
        self.assertEqual(self.bot.state, "tz")
        await self.say(7, "+3")
        self.assertEqual(self.s.get("tz"), "+3")
        self.assertIsNone(self.bot.state)
        if parse_tz("Europe/Warsaw") is not None:
            await self.say(7, "", {"a": "settz", "v": "Europe/Warsaw"})
            self.assertEqual(self.s.get("tz"), "Europe/Warsaw")

    async def test_pause_blocks_notifications(self):
        self.s.set("owner_id", 7)
        await self.say(7, "", {"a": "target"})
        await self.say(7, "durov")
        await self.say(7, "", {"a": "pause"})
        self.assertTrue(self.s.flag("paused"))
        n = len(self.api.sent)
        self.api.users["1"] = person(online=1)
        await self.w.sync()
        self.assertEqual(len(self.api.sent), n)
        await self.say(7, "", {"a": "pause"})
        self.assertFalse(self.s.flag("paused"))

    async def test_commands_and_start_button(self):
        self.s.set("owner_id", 7)
        await self.say(7, "статистика")
        self.assertIn("Статистика заходов", self.last()[1])
        await self.say(7, "Настройки")
        self.assertIn("Настройки", self.last()[1])
        await self.say(7, "/menu")
        self.assertIn("VK Online Tracker", self.last()[1])
        await self.say(7, "что-то непонятное")      # без ожидаемого ввода -> главный экран
        self.assertIn("VK Online Tracker", self.last()[1])
        await self.say(7, "Начать", {"command": "start"})
        self.assertIn("VK Online Tracker", self.last()[1])

    async def test_old_flat_update_format(self):
        """Старые версии API отдают сообщение без вложенного message."""
        self.s.set("owner_id", 7)
        await self.bot.handle({"type": "message_new", "object": {"from_id": 7, "peer_id": 7, "text": "меню"}})
        self.assertIn("VK Online Tracker", self.last()[1])

    async def test_banned_page_alert(self):
        self.s.set("owner_id", 7)
        await self.say(7, "", {"a": "target"})
        await self.say(7, "durov")
        self.api.users["1"] = [{"id": 1, "first_name": "Павел", "last_name": "Дуров", "deactivated": "banned"}]
        await self.w.sync()
        self.assertIn("Страница недоступна", self.last()[1])
        self.assertIn("🚫", self.eng.status_text("Павел"))


if __name__ == "__main__":
    unittest.main()
