from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import Callable
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import AuthKeyError, FloodWaitError, SessionPasswordNeededError
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from .config import AppConfig
from .control import RunControl, StopRequested
from .links import extract_urls

LogFn = Callable[[str], None]
PromptFn = Callable[[str, str, bool], str | None]


class TelegramGateway:
    DONE_SUCCESS = "success"
    DONE_LIKE_MISSING = "like_missing"
    DONE_UNKNOWN = "unknown"
    DONE_CLICK_FAILED = "click_failed"

    def __init__(
        self,
        config: AppConfig,
        log: LogFn,
        prompt: PromptFn,
        control: RunControl | None = None,
    ) -> None:
        self.config = config
        self.log = log
        self.prompt = prompt
        self.control = control
        self.client = TelegramClient(
            str(config.session_path), config.api_id, config.api_hash
        )
        self.bot: Any = None
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.seen_ids: set[int] = set()
        self._last_callback_text = ""
        self._handler_registered = False

    async def connect(self) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 6):
            try:
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    await self._authorize()
                self.bot = await self.client.get_entity(self.config.bot_username)
                self._register_handler()
                self.log("Telegram подключён")
                return
            except (
                OSError,
                AuthKeyError,
                ConnectionError,
                sqlite3.OperationalError,
            ) as exc:
                last_error = exc
                self.log(f"Telegram занят или недоступен, попытка {attempt}/5")
                try:
                    await self.client.disconnect()
                except Exception as disconnect_error:  # noqa: BLE001
                    self.log(
                        f"Ошибка отключения Telegram перед повтором: {disconnect_error}"
                    )
                if attempt < 5:
                    await self._sleep(attempt * 2)
        raise ConnectionError(
            "Не удалось подключиться к Telegram после 5 попыток"
        ) from last_error

    async def _authorize(self) -> None:
        phone = self.config.phone.strip()
        await self.client.send_code_request(phone)
        code = await asyncio.to_thread(
            self.prompt, "Вход в Telegram", "Код из Telegram:", False
        )
        if not code:
            raise RuntimeError("Вход отменён: код не введён")
        try:
            await self.client.sign_in(phone=phone, code=code.strip())
        except SessionPasswordNeededError:
            password = await asyncio.to_thread(
                self.prompt, "Двухфакторная авторизация", "Пароль 2FA:", True
            )
            if not password:
                raise RuntimeError("Вход отменён: пароль 2FA не введён")
            await self.client.sign_in(password=password)

    def _register_handler(self) -> None:
        if self._handler_registered:
            return

        @self.client.on(events.NewMessage(from_users=self.bot))
        async def on_message(event: Any) -> None:
            message = event.message
            if (
                message.id not in self.seen_ids
                and self.message_urls(message)
                and self._has_task_buttons(message)
                and not self._is_like_missing(message)
            ):
                await self.queue.put(message)
                self.log(f"Получено новое задание #{message.id}")

        self._handler_registered = True

    @staticmethod
    def message_urls(message: Any) -> list[str]:
        text = message.raw_text or ""
        entity_urls: list[str] = []
        entities_with_text = (
            message.get_entities_text() if hasattr(message, "get_entities_text") else []
        )
        for entity, entity_text in entities_with_text:
            if isinstance(entity, MessageEntityTextUrl):
                entity_urls.append(entity.url)
            elif isinstance(entity, MessageEntityUrl):
                # Telethon resolves Telegram's UTF-16 offsets here, including
                # messages containing emoji before the URL.
                entity_urls.append(entity_text)
        return extract_urls(text, entity_urls)

    async def find_existing_task(self) -> Any | None:
        messages = await self.client.get_messages(self.bot, limit=20)
        for message in messages:
            if getattr(message, "out", False):
                continue
            if (
                message.id not in self.seen_ids
                and self.message_urls(message)
                and self._has_task_buttons(message)
                and not self._is_like_missing(message)
            ):
                self.log(f"Найдено ожидающее задание #{message.id}")
                return message
            # The most recent incoming bot message is not a task. Older tasks
            # must not be processed again.
            return None
        return None

    @staticmethod
    def _has_task_buttons(message: Any) -> bool:
        labels = {
            (button.text or "").strip().lower()
            for row in (message.buttons or [])
            for button in row
        }
        return any("готово" in label for label in labels) and any(
            "пропустить" in label for label in labels
        )

    async def request_task(self) -> Any | None:
        messages = await self.client.get_messages(self.bot, limit=20)
        latest_incoming = next(
            (message for message in messages if not getattr(message, "out", False)),
            None,
        )
        if (
            latest_incoming is not None
            and latest_incoming.id not in self.seen_ids
            and self.message_urls(latest_incoming)
            and self._has_task_buttons(latest_incoming)
            and not self._is_like_missing(latest_incoming)
        ):
            self.log(f"Задание #{latest_incoming.id} пришло во время проверки")
            return latest_incoming

        for message in messages:
            for row in message.buttons or []:
                for button in row:
                    normalized = re.sub(
                        r"\s+", " ", (button.text or "").strip().lower()
                    )
                    if "доступные задания" not in normalized:
                        continue
                    try:
                        await button.click()
                        self.log("Нажата кнопка «▶️ Доступные задания»")
                        return None
                    except Exception as exc:  # noqa: BLE001 - Telegram button errors vary
                        self.log(f"Не удалось нажать кнопку заданий: {exc}")
                        break
        await self.client.send_message(self.bot, "▶️ Доступные задания")
        self.log("Команда «▶️ Доступные задания» отправлена сообщением")
        return None

    async def get_task(self) -> Any | None:
        existing = await self.find_existing_task()
        if existing is not None:
            return existing

        waits = (10, 20, 30, 40, 50)
        for attempt, timeout in enumerate(waits, start=1):
            task_during_check = await self.request_task()
            if task_during_check is not None:
                return task_during_check
            self.log(f"Ожидание задания: до {timeout} сек. ({attempt}/5)")
            message = await self._wait_for_message(timeout)
            if message is not None and message.id not in self.seen_ids:
                return message
        return None

    async def _wait_for_message(self, timeout: float) -> Any | None:
        """Wait for active (unpaused) seconds while keeping Telegram online."""
        remaining = timeout
        loop = asyncio.get_running_loop()
        while remaining > 0:
            await self._wait_until_active()
            started = loop.time()
            try:
                return await asyncio.wait_for(
                    self.queue.get(), timeout=min(0.5, remaining)
                )
            except asyncio.TimeoutError:
                remaining -= loop.time() - started
        return None

    async def mark_seen(self, message: Any) -> None:
        self.seen_ids.add(message.id)

    async def click_done(self, message: Any) -> bool:
        return await self._click_button(message, ("✅ готово", "готово"), attempts=3)

    async def click_done_and_verify(self, message: Any) -> tuple[str, Any]:
        self._last_callback_text = ""
        if not await self.click_done(message):
            return self.DONE_CLICK_FAILED, message
        return await self._wait_done_verdict(message)

    async def click_skip(self, message: Any) -> bool:
        return await self._click_button(
            message, ("❌ пропустить", "пропустить"), attempts=3
        )

    async def _click_button(
        self, message: Any, labels: tuple[str, ...], attempts: int
    ) -> bool:
        for attempt in range(1, attempts + 1):
            fresh = await self.client.get_messages(self.bot, ids=message.id)
            if not fresh:
                return False
            for row in fresh.buttons or []:
                for button in row:
                    normalized = re.sub(
                        r"\s+", " ", (button.text or "").strip().lower()
                    )
                    if any(label in normalized for label in labels):
                        try:
                            response = await button.click()
                            self._last_callback_text = str(
                                getattr(response, "message", "") or ""
                            )
                            self.log(f"Нажата кнопка «{button.text}»")
                            return True
                        except FloodWaitError as exc:
                            await self._sleep(exc.seconds)
                        except Exception as exc:  # noqa: BLE001 - Telegram callback errors vary
                            self.log(
                                f"Кнопка не сработала ({attempt}/{attempts}): {exc}"
                            )
            await self._sleep(attempt)
        return False

    async def _wait_done_verdict(self, message: Any) -> tuple[str, Any]:
        if self._text_is_like_missing(self._last_callback_text):
            return self.DONE_LIKE_MISSING, message

        remaining = 10.0
        done_button_removed = False
        while remaining > 0:
            fresh = await self.client.get_messages(self.bot, ids=message.id)
            recent = await self.client.get_messages(self.bot, limit=10)
            recent_candidates = [
                candidate
                for candidate in recent
                if not getattr(candidate, "out", False)
                and getattr(candidate, "id", 0) >= message.id
            ]
            candidates = [
                candidate for candidate in [fresh, *recent_candidates] if candidate
            ]
            unique_candidates = list(
                {candidate.id: candidate for candidate in candidates}.values()
            )
            skip_source = next(
                (
                    candidate
                    for candidate in unique_candidates
                    if self._has_button(candidate, "пропустить")
                ),
                message,
            )
            for candidate in unique_candidates:
                if self._is_like_missing(candidate):
                    return self.DONE_LIKE_MISSING, skip_source
                if self._is_done_success(candidate):
                    return self.DONE_SUCCESS, candidate

            if fresh and not self._has_button(fresh, "готово"):
                done_button_removed = True
            await self._sleep(0.5)
            remaining -= 0.5

        if done_button_removed:
            return self.DONE_SUCCESS, message
        return self.DONE_UNKNOWN, message

    @classmethod
    def _is_like_missing(cls, message: Any) -> bool:
        return cls._text_is_like_missing(getattr(message, "raw_text", "") or "")

    @staticmethod
    def _text_is_like_missing(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.lower())
        return any(
            phrase in normalized
            for phrase in (
                "лайк не найден",
                "ваш лайк не найден",
                "комментарий не найден",
                "ваш комментарий не найден",
                "комментарий не обнаружен",
                "проект в теневом бане",
                "проект в теневом бан",
            )
        )

    @staticmethod
    def _is_done_success(message: Any) -> bool:
        text = re.sub(r"\s+", " ", (getattr(message, "raw_text", "") or "").lower())
        return any(
            phrase in text
            for phrase in (
                "задание выполнено",
                "задание засчитано",
                "лайк найден",
                "комментарий найден",
                "комментарий засчитан",
                "награда начислена",
                "успешно выполнено",
            )
        )

    @staticmethod
    def _has_button(message: Any, label: str) -> bool:
        return any(
            label in (button.text or "").strip().lower()
            for row in (getattr(message, "buttons", None) or [])
            for button in row
        )

    async def _wait_until_active(self) -> None:
        while (
            self.control is not None
            and self.control.paused
            and not self.control.stopped
        ):
            await asyncio.sleep(0.2)
        if self.control is not None and self.control.stopped:
            raise StopRequested

    async def _sleep(self, seconds: float) -> None:
        remaining = float(seconds)
        loop = asyncio.get_running_loop()
        while remaining > 0:
            await self._wait_until_active()
            started = loop.time()
            await asyncio.sleep(min(0.2, remaining))
            remaining -= loop.time() - started

    async def disconnect(self) -> None:
        if self.client.is_connected():
            await self.client.disconnect()
        self.log("Telegram отключён")
