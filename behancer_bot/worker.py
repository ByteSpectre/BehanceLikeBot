from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from .chrome import ChromeController
from .config import AppConfig
from .control import RunControl, SkipRequested, StopRequested
from .links import is_facebook_url
from .telegram_gateway import TelegramGateway

LogFn = Callable[[str], None]
PromptFn = Callable[[str, str, bool], str | None]
EventFn = Callable[[str, object], None]


class BotWorker:
    def __init__(
        self,
        config: AppConfig,
        log: LogFn,
        prompt: PromptFn,
        event: EventFn,
    ) -> None:
        self.config = config
        self.log = log
        self.prompt = prompt
        self.event = event
        self.control = RunControl()
        self.thread: threading.Thread | None = None
        self.done_count = 0
        self.skipped_count = 0

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.done_count = 0
        self.skipped_count = 0
        self.control = RunControl()
        self.thread = threading.Thread(
            target=self._thread_main, name="behancer-worker", daemon=True
        )
        self.thread.start()

    def pause(self) -> None:
        self.control.pause()
        self.event("state", "Пауза")
        self.log("Работа приостановлена")

    def resume(self) -> None:
        self.control.resume()
        self.event("state", "Работает")
        self.log("Работа продолжена")

    def skip(self) -> None:
        self.control.skip()
        self.log("Запрошен пропуск текущего задания")

    def stop(self) -> None:
        self.control.stop()
        self.event("state", "Остановка…")
        self.log("Запрошена остановка")

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001 - report any worker failure to the GUI
            self.log(f"Критическая ошибка: {exc}")
            self.event("error", str(exc))
        finally:
            self.event("state", "Остановлен")
            self.event("finished", None)

    async def _run(self) -> None:
        self.event("state", "Подключение")
        telegram = TelegramGateway(self.config, self.log, self.prompt, self.control)
        chrome = ChromeController(
            self.config,
            self.control,
            self.log,
            lambda status: self.event("chrome", status),
        )
        current_message = None
        chrome_started = False
        try:
            await telegram.connect()
            self.event("state", "Ожидание задания")

            while True:
                await self._wait_until_active(allow_skip=False)
                current_message = await self._await_task_interruptibly(telegram)
                if current_message is None:
                    self.log("Задания не пришли после 5 попыток. Работа остановлена")
                    return

                urls = telegram.message_urls(current_message)
                facebook_urls = [url for url in urls if is_facebook_url(url)]
                await telegram.mark_seen(current_message)
                self.log(
                    f"Обработка задания #{current_message.id}: найдено ссылок — "
                    f"{len(urls)}"
                )
                if facebook_urls:
                    self.log(
                        "Задание ведёт на Facebook — автоматически пропускаю "
                        "его без открытия Chrome"
                    )
                try:
                    if facebook_urls:
                        completed = False
                    elif urls:
                        if not chrome_started:
                            await asyncio.to_thread(chrome.start)
                            chrome_started = True
                        self.event("state", "Работает")
                        completed = await asyncio.to_thread(chrome.perform_task, urls)
                    else:
                        completed = False
                        self.log("В задании нет ссылок для обработки — пропускаю его")
                    if completed:
                        await self._wait_until_active()
                except SkipRequested:
                    completed = False
                    self.log("Текущее задание прервано пользователем")

                if completed:
                    done_status, verdict_message = await telegram.click_done_and_verify(
                        current_message
                    )
                    if done_status == telegram.DONE_SUCCESS:
                        self.done_count += 1
                        self.event("counters", (self.done_count, self.skipped_count))
                        self.log("Задание успешно подтверждено")
                        current_message = None
                        continue
                    if done_status == telegram.DONE_LIKE_MISSING:
                        self.log(
                            "Бот не нашёл лайк или проект находится в теневом бане — "
                            "пропускаю задание"
                        )
                        await telegram.mark_seen(verdict_message)
                        if await telegram.click_skip(verdict_message):
                            self.skipped_count += 1
                            self.event(
                                "counters", (self.done_count, self.skipped_count)
                            )
                            self.log("Проблемное задание пропущено")
                            current_message = None
                            continue
                        self.log("Не удалось нажать «Пропустить» после ошибки проверки")
                        return
                    if done_status == telegram.DONE_CLICK_FAILED:
                        self.log("Не удалось нажать «Готово»")
                    else:
                        self.log("Бот не подтвердил выполнение задания за 10 секунд")
                    self.log(
                        "Подтверждение «Готово» не получено — новое задание "
                        "не будет запрошено"
                    )
                    return

                # Failed and manually skipped tasks both use the bot's Skip button.
                self.control.consume_skip()
                if await telegram.click_skip(current_message):
                    self.skipped_count += 1
                    self.event("counters", (self.done_count, self.skipped_count))
                    self.log("Задание пропущено")
                    current_message = None
                    continue
                self.log("Не удалось нажать «Пропустить». Работа остановлена")
                return
        except StopRequested:
            self.log("Работа остановлена пользователем")
        finally:
            await telegram.disconnect()
            await asyncio.to_thread(chrome.release)

    async def _await_task_interruptibly(self, telegram: TelegramGateway):
        task = asyncio.create_task(telegram.get_task())
        try:
            while not task.done():
                if self.control.stopped:
                    raise StopRequested
                # A skip has no target while no task is active; do not carry it
                # over and accidentally skip the next task that arrives.
                self.control.consume_skip()
                await asyncio.sleep(0.2)
            return await task
        except StopRequested:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise

    async def _wait_until_active(self, allow_skip: bool = True) -> None:
        # Never block the asyncio thread while paused: Telethon must keep its
        # connection and NewMessage listener alive.
        while self.control.paused and not self.control.stopped:
            await asyncio.sleep(0.2)
        self.control.checkpoint(allow_skip=allow_skip)
