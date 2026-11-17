"""Тесты клиента VK API и Long Poll на подставной сессии (без сети)."""
import os
import tempfile
import unittest

os.environ["DATA_DIR"] = tempfile.mkdtemp()

from tracker.errors import VkError  # noqa: E402
from tracker.vkapi import VkApi  # noqa: E402


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data=None):
        self.calls.append(("POST", url, data))
        return FakeResp(self.responses.pop(0))

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        return FakeResp(self.responses.pop(0))


class VkApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_params_and_errors(self):
        session = FakeSession([
            {"response": [1]},
            {"error": {"error_code": 113, "error_msg": "Invalid user id"}},
        ])
        api = VkApi("TOKEN", "5.199", session)
        self.assertEqual(await api.call("users.get", user_ids=1, fields=None), [1])
        _, url, data = session.calls[0]
        self.assertTrue(url.endswith("/users.get"))
        self.assertEqual(data["user_ids"], "1")
        self.assertNotIn("fields", data)            # None-параметры не отправляем
        self.assertEqual(data["access_token"], "TOKEN")
        self.assertEqual(data["v"], "5.199")
        with self.assertRaises(VkError) as ctx:
            await api.call("users.get", user_ids="x")
        self.assertEqual(ctx.exception.code, 113)

    async def test_rate_limit_is_retried(self):
        session = FakeSession([
            {"error": {"error_code": 6, "error_msg": "Too many requests per second"}},
            {"response": 42},
        ])
        self.assertEqual(await VkApi("T", "5.199", session).call("users.get"), 42)
        self.assertEqual(len(session.calls), 2)

    async def test_group_id_both_response_formats(self):
        for response in ({"groups": [{"id": 5}], "profiles": []}, [{"id": 5}]):
            api = VkApi("T", "5.199", FakeSession([{"response": response}]))
            self.assertEqual(await api.group_id(), 5)

    async def test_long_poll_handles_failed_codes(self):
        lp = "https://lp.example/x"
        session = FakeSession([
            {"response": {"key": "K1", "server": lp, "ts": "1"}},
            {"ts": "2", "updates": [{"type": "message_new", "n": 1}]},
            {"failed": 1, "ts": "7"},                                    # история устарела
            {"failed": 2},                                               # ключ истёк
            {"response": {"key": "K2", "server": lp, "ts": "99"}},
            {"failed": 3},                                               # данные потеряны
            {"response": {"key": "K3", "server": lp, "ts": "100"}},
            {"ts": "101", "updates": [{"type": "message_new", "n": 2}]},
        ])
        gen = VkApi("T", "5.199", session).long_poll(5)
        got = []
        async for update in gen:
            got.append(update["n"])
            if len(got) == 2:
                break
        await gen.aclose()
        self.assertEqual(got, [1, 2])
        gets = [c[2] for c in session.calls if c[0] == "GET"]
        self.assertEqual((gets[2]["key"], gets[2]["ts"]), ("K1", "7"))     # после failed=1: новый ts
        self.assertEqual((gets[3]["key"], gets[3]["ts"]), ("K2", "7"))     # после failed=2: новый ключ, ts прежний
        self.assertEqual((gets[4]["key"], gets[4]["ts"]), ("K3", "100"))   # после failed=3: и ключ, и ts


if __name__ == "__main__":
    unittest.main()
