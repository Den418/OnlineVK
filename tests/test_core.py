"""Тесты ядра (без ВК). Запуск: python -m unittest discover -s tests"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ["DATA_DIR"] = tempfile.mkdtemp()  # до импорта tracker.config

from tracker import stats  # noqa: E402
from tracker.db import Database  # noqa: E402
from tracker.engine import Engine  # noqa: E402
from tracker.fmt import fmt_dur, parse_tz  # noqa: E402
from tracker.settings import Settings  # noqa: E402

T0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    @staticmethod
    def at(seconds):
        return T0 + timedelta(seconds=seconds)


class FormatTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(fmt_dur(0), "0с")
        self.assertEqual(fmt_dur(65), "1м 05с")
        self.assertEqual(fmt_dur(3725), "1ч 02м 05с")
        self.assertEqual(fmt_dur(-5), "0с")

    def test_timezones(self):
        self.assertEqual(parse_tz("UTC"), timezone.utc)
        self.assertEqual(parse_tz("+3").utcoffset(None), timedelta(hours=3))
        self.assertEqual(parse_tz("UTC-05:30").utcoffset(None), -timedelta(hours=5, minutes=30))
        self.assertIsNone(parse_tz("+25"))
        self.assertIsNone(parse_tz("Not/AZone"))
        self.assertIsNone(parse_tz(""))


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        self.s = Settings(self.db)
        self.clock = Clock()
        self.out = []

        async def notify(text):
            self.out.append(text)

        self.engine = Engine(self.db, self.s, notify, clock=self.clock)

    async def test_session_roundtrip(self):
        await self.engine.offline(self.clock.at(-600), startup=True)
        self.assertEqual(self.out, [])                          # на старте молчим

        await self.engine.online(where="с телефона")
        self.assertIn("В сети с 12:00:00 (с телефона)", self.out[-1])
        self.assertIn("10м 00с", self.out[-1])                  # сколько до этого был офлайн

        await self.engine.online(where="с телефона")            # продление - не новый заход
        self.assertEqual(len(self.out), 1)

        await self.engine.offline(self.clock.at(125))
        self.assertIn("2м 05с", self.out[-1])
        self.assertIn("12:02:05", self.out[-1])
        self.assertIn("с телефона", self.out[-1])
        self.assertEqual(len(self.db.last_sessions()), 1)

        await self.engine.offline(self.clock.at(125))           # дубль офлайна
        self.assertEqual(len(self.out), 2)

    async def test_stale_offline_ignored(self):
        await self.engine.online()
        await self.engine.offline(self.clock.at(-3600))         # статус из прошлого
        self.assertIsNotNone(self.engine.online_since)
        self.assertEqual(len(self.out), 1)

    async def test_first_offline_status_is_silent(self):
        await self.engine.offline(self.clock.at(30))            # прошлого статуса нет: сравнивать не с чем
        self.assertEqual(self.out, [])

    async def test_short_visit_between_polls(self):
        await self.engine.offline(self.clock.at(-600), startup=True)
        await self.engine.offline(self.clock.at(-100))          # «был в сети» стал новее, «онлайн» не поймали
        self.assertIn("Короткий заход", self.out[-1])
        self.assertIn("11:58:20", self.out[-1])
        self.assertEqual(len(self.db.last_sessions()), 1)       # заход учтён в истории
        await self.engine.offline(self.clock.at(-90))           # уточнение в пределах 30 с: не новый заход
        self.assertEqual(len(self.out), 1)

    async def test_short_visits_count_towards_burst_alert(self):
        self.s.set("alert_per_hour", 3)
        await self.engine.offline(self.clock.at(-1000), startup=True)
        for i in range(3):
            self.clock.now = self.clock.at(i * 100)
            await self.engine.offline(self.clock.now - timedelta(seconds=5))
        self.assertEqual(sum("Частые заходы" in m for m in self.out), 1)

    async def test_stats_split_short_visits(self):
        await self.engine.offline(self.clock.at(-1000), startup=True)
        await self.engine.offline(self.clock.at(-500))          # короткий заход
        await self.engine.online()
        await self.engine.offline(self.clock.at(90))            # обычный на 1м 30с
        text = stats.stats_text(self.db, timezone.utc, now=self.clock.at(120))
        self.assertIn("заходов: 2, коротких: 1, в сети: 1м 30с", text)

    async def test_hidden_status_reported_once(self):
        await self.engine.hidden_status()
        await self.engine.hidden_status()
        self.assertEqual(sum("скрыт" in m for m in self.out), 1)

    async def test_burst_alert(self):
        self.s.set("alert_per_hour", 3)
        for i in range(3):
            self.clock.now = self.clock.at(i * 100)
            await self.engine.online()
            await self.engine.offline(self.clock.now + timedelta(seconds=20))
        self.assertEqual(sum("Частые заходы" in m for m in self.out), 1)

    async def test_notification_toggles(self):
        self.s.set("notify_online", 0)
        await self.engine.online()
        self.assertEqual(self.out, [])
        await self.engine.offline(self.clock.at(60))
        self.assertIn("1м 00с", self.out[-1])
        self.assertEqual(len(self.db.last_sessions()), 1)       # история пишется всегда

    async def test_pause_ignores_events(self):
        self.s.set("paused", 1)
        await self.engine.online()
        self.assertEqual(self.out, [])
        self.assertIsNone(self.engine.online_since)

    async def test_started_while_online_is_approximate(self):
        await self.engine.online(startup=True)
        await self.engine.offline(self.clock.at(100))
        self.assertIn("не меньше 1м 40с", self.out[-1])
        self.assertEqual(self.db.last_sessions(), [])           # неточную сессию в историю не кладём

    async def test_unavailable_and_back(self):
        await self.engine.unavailable("banned")
        self.assertIn("заблокирована или заморожена", self.out[-1])
        await self.engine.unavailable("banned")                 # повтор не шумит
        self.assertEqual(len(self.out), 1)
        await self.engine.online()
        self.assertIn("снова доступна", self.out[1])
        self.assertIn("В сети", self.out[2])

    async def test_status_text(self):
        self.assertIn("не в сети", self.engine.status_text("Иван"))
        await self.engine.online(where="с сайта")
        self.assertIn("в сети уже", self.engine.status_text("Иван"))
        self.assertIn("с сайта", self.engine.status_text("Иван"))

    async def test_stats(self):
        await self.engine.online()
        await self.engine.offline(self.clock.at(90))
        text = stats.stats_text(self.db, timezone.utc, now=self.clock.at(120))
        self.assertIn("заходов: 1", text)
        self.assertIn("1м 30с", text)
        self.assertIn("1м 30с", stats.history_text(self.db, timezone.utc))


if __name__ == "__main__":
    unittest.main()
