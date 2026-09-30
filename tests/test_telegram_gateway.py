import unittest
from typing import ClassVar
from unittest.mock import AsyncMock

from telethon.tl.types import MessageEntityUrl

from behancer_bot.telegram_gateway import TelegramGateway


class FakeMessage:
    raw_text = "👍 https://www.behance.net/gallery/987654/Test"
    entities: ClassVar = [MessageEntityUrl(offset=3, length=48)]

    def get_entities_text(self):
        return [(self.entities[0], "https://www.behance.net/gallery/987654/Test")]


class FakeButton:
    def __init__(self, text):
        self.text = text
        self.clicked = False

    async def click(self):
        self.clicked = True


class HistoryMessage:
    def __init__(self, message_id, text="", buttons=None, out=False):
        self.id = message_id
        self.raw_text = text
        self.buttons = buttons or []
        self.out = out
        self.entities = []

    def get_entities_text(self):
        return []


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    async def get_messages(self, _bot, limit):
        return self.messages[:limit]

    async def send_message(self, _bot, text):
        self.sent.append(text)


class VerdictClient:
    def __init__(self, message):
        self.message = message

    async def get_messages(self, _bot, ids=None, limit=None):
        return self.message if ids is not None else [self.message]


class TelegramGatewayTests(unittest.TestCase):
    def test_entity_url_after_emoji_uses_telethon_resolved_text(self):
        self.assertEqual(
            TelegramGateway.message_urls(FakeMessage()),
            ["https://www.behance.net/gallery/987654/Test"],
        )

    def test_detects_like_missing_and_shadow_ban_message(self):
        message = HistoryMessage(
            20,
            "Ошибка! Ваш лайк не найден. Возможно, проект в теневом бане.",
        )
        self.assertTrue(TelegramGateway._is_like_missing(message))

    def test_detects_missing_comment_message(self):
        message = HistoryMessage(21, "Ошибка! Ваш комментарий не найден.")
        self.assertTrue(TelegramGateway._is_like_missing(message))


class TelegramGatewayAsyncTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_gateway(messages):
        gateway = object.__new__(TelegramGateway)
        gateway.client = FakeClient(messages)
        gateway.bot = object()
        gateway.seen_ids = set()
        gateway.log = lambda _text: None
        return gateway

    async def test_only_latest_incoming_message_is_checked_for_existing_task(self):
        done = FakeButton("✅ Готово")
        skip = FakeButton("❌ Пропустить")
        old_task = HistoryMessage(
            10,
            "https://www.behance.net/gallery/123/Old",
            [[done], [skip]],
        )
        latest_menu = HistoryMessage(11, "Выберите действие")
        gateway = self.make_gateway([latest_menu, old_task])
        self.assertIsNone(await gateway.find_existing_task())

    async def test_available_tasks_button_is_clicked_automatically(self):
        button = FakeButton("▶️ Доступные задания")
        gateway = self.make_gateway([HistoryMessage(12, buttons=[[button]])])
        self.assertIsNone(await gateway.request_task())
        self.assertTrue(button.clicked)
        self.assertEqual(gateway.client.sent, [])

    async def test_task_arriving_during_request_check_prevents_another_click(self):
        available = FakeButton("▶️ Доступные задания")
        done = FakeButton("✅ Готово")
        skip = FakeButton("❌ Пропустить")
        task = HistoryMessage(
            13,
            "https://www.behance.net/gallery/456/New",
            [[done], [skip]],
        )
        menu = HistoryMessage(12, buttons=[[available]])
        gateway = self.make_gateway([task, menu])
        self.assertIs(await gateway.request_task(), task)
        self.assertFalse(available.clicked)

    async def test_like_missing_verdict_returns_message_for_skip(self):
        skip = FakeButton("❌ Пропустить")
        error = HistoryMessage(
            30,
            "Ошибка! Ваш лайк не найден. Проект в теневом бане.",
            [[skip]],
        )
        gateway = object.__new__(TelegramGateway)
        gateway.client = VerdictClient(error)
        gateway.bot = object()
        gateway._last_callback_text = ""
        gateway._sleep = AsyncMock()
        status, message = await gateway._wait_done_verdict(error)
        self.assertEqual(status, TelegramGateway.DONE_LIKE_MISSING)
        self.assertIs(message, error)


if __name__ == "__main__":
    unittest.main()
