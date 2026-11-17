"""Тесты разбора ответа ВК и ссылок на страницы."""
import os
import tempfile
import unittest
from datetime import datetime, timezone

os.environ["DATA_DIR"] = tempfile.mkdtemp()

from tracker.vkstatus import parse_ref, parse_status  # noqa: E402


class StatusTests(unittest.TestCase):
    def test_online_variants(self):
        self.assertEqual(parse_status({"online": 1}).where, "с сайта")
        self.assertEqual(parse_status({"online": 1, "online_mobile": 1}).where, "с телефона")
        self.assertEqual(parse_status({"online": 1, "online_app": 777}).where, "приложение 777")
        st = parse_status({"online": 1, "online_mobile": 1, "online_app": 2274003})
        self.assertEqual((st.kind, st.where), ("online", "с телефона, приложение 2274003"))

    def test_offline(self):
        st = parse_status({"online": 0, "last_seen": {"time": 1700000000, "platform": 4}})
        self.assertEqual(st.kind, "offline")
        self.assertEqual(st.last_seen, datetime.fromtimestamp(1700000000, timezone.utc))

    def test_hidden(self):
        self.assertEqual(parse_status({"id": 1}).kind, "hidden")
        self.assertEqual(parse_status({"online": 0}).kind, "hidden")
        self.assertEqual(parse_status({"online_info": {"visible": False}}).kind, "hidden")

    def test_gone(self):
        st = parse_status({"deactivated": "banned"})
        self.assertEqual((st.kind, st.detail), ("gone", "banned"))

    def test_online_info_format(self):
        st = parse_status({"online_info": {"visible": True, "is_online": True, "is_mobile": True, "app_id": 5}})
        self.assertEqual((st.kind, st.where), ("online", "с телефона, приложение 5"))
        st = parse_status({"online_info": {"visible": True, "is_online": False, "last_seen": 1700000000}})
        self.assertEqual(st.kind, "offline")


class RefTests(unittest.TestCase):
    def test_good_refs(self):
        cases = {
            "https://vk.com/durov": "durov",
            "vk.com/id1": "1",
            "m.vk.com/durov?z=photo1": "durov",
            "https://vk.ru/durov/": "durov",
            "@durov": "durov",
            "id123": "123",
            "42": "42",
            "[id5|Вася]": "5",
        }
        for text, expected in cases.items():
            self.assertEqual(parse_ref(text), expected, text)

    def test_bad_refs(self):
        for text in ("", "https://vk.com/", "привет", "a", "два слова"):
            self.assertIsNone(parse_ref(text), text)


if __name__ == "__main__":
    unittest.main()
