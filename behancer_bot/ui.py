from __future__ import annotations

import queue
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .chrome import ChromeController
from .chrome_profiles import discover_chrome_profiles
from .config import AppConfig
from .control import RunControl
from .worker import BotWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Behancer Bot v{__version__}")
        self.resize(920, 680)
        self.setMinimumSize(760, 560)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.config = AppConfig.load()
        self.worker: BotWorker | None = None
        self._close_when_finished = False
        self._force_close = False
        self._build_ui()
        self._load_config()
        self._append_log(f"Behancer Bot v{__version__}")
        self._append_log(f"Код запущен из: {Path(__file__).resolve().parent.parent}")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(100)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        self.setCentralWidget(central)

        settings = QGroupBox("Подключение")
        form = QFormLayout(settings)
        self.fields: dict[str, QLineEdit] = {
            "api_id": QLineEdit(),
            "api_hash": QLineEdit(),
            "phone": QLineEdit(),
            "chrome_user_data_dir": QLineEdit(),
        }
        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(True)
        self.profile_combo.setToolTip(
            "Имя папки профиля Chrome, например Default или Profile 1"
        )
        self.refresh_profiles_button = QPushButton("Обновить")
        self.refresh_profiles_button.clicked.connect(self._refresh_profiles)
        self.open_profile_button = QPushButton("Открыть для входа")
        self.open_profile_button.setToolTip(
            "Открыть постоянный управляемый профиль и войти в соцсети до запуска бота"
        )
        self.open_profile_button.clicked.connect(self._open_profile_for_login)
        self.fields["chrome_user_data_dir"].editingFinished.connect(
            self._refresh_profiles
        )
        self.fields["api_hash"].setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Telegram API ID", self.fields["api_id"])
        form.addRow("Telegram API Hash", self.fields["api_hash"])
        form.addRow("Телефон", self.fields["phone"])
        form.addRow("Папка данных Chrome", self.fields["chrome_user_data_dir"])
        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.addWidget(self.profile_combo, 1)
        profile_row.addWidget(self.refresh_profiles_button)
        profile_row.addWidget(self.open_profile_button)
        form.addRow("Профиль Chrome", profile_row)
        root.addWidget(settings)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Старт")
        self.pause_button = QPushButton("Пауза")
        self.resume_button = QPushButton("Возобновить")
        self.skip_button = QPushButton("Пропустить")
        self.stop_button = QPushButton("Стоп")
        self.start_button.clicked.connect(self._start)
        self.pause_button.clicked.connect(self._pause)
        self.resume_button.clicked.connect(self._resume)
        self.skip_button.clicked.connect(self._skip)
        self.stop_button.clicked.connect(self._stop)
        for button in (
            self.start_button,
            self.pause_button,
            self.resume_button,
            self.skip_button,
            self.stop_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        root.addLayout(controls)

        stats = QGridLayout()
        self.state_label = QLabel("Остановлен")
        self.chrome_label = QLabel("Не подключён")
        self.done_label = QLabel("0")
        self.skipped_label = QLabel("0")
        values = (
            ("Статус:", self.state_label),
            ("Chrome:", self.chrome_label),
            ("Выполнено:", self.done_label),
            ("Пропущено:", self.skipped_label),
        )
        for column, (title, value) in enumerate(values):
            stats.addWidget(QLabel(title), 0, column * 2)
            stats.addWidget(value, 0, column * 2 + 1)
        stats.setColumnStretch(8, 1)
        root.addLayout(stats)

        log_group = QGroupBox("Лог")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        root.addWidget(log_group, 1)
        self._set_running_controls(False)

    def _load_config(self) -> None:
        self.fields["api_id"].setText(str(self.config.api_id or ""))
        self.fields["api_hash"].setText(self.config.api_hash)
        self.fields["phone"].setText(self.config.phone)
        self.fields["chrome_user_data_dir"].setText(self.config.chrome_user_data_dir)
        self._refresh_profiles(self.config.chrome_profile)

    def _refresh_profiles(self, preferred: str | None = None) -> None:
        selected = preferred or self._selected_profile() or self.config.chrome_profile
        profiles = discover_chrome_profiles(
            self.fields["chrome_user_data_dir"].text().strip()
        )
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in profiles:
            self.profile_combo.addItem(profile.label, profile.directory)
        matching_index = next(
            (
                index
                for index in range(self.profile_combo.count())
                if self.profile_combo.itemData(index) == selected
            ),
            -1,
        )
        if matching_index >= 0:
            self.profile_combo.setCurrentIndex(matching_index)
        else:
            self.profile_combo.setEditText(selected or "Default")
        self.profile_combo.blockSignals(False)

    def _selected_profile(self) -> str:
        index = self.profile_combo.currentIndex()
        if (
            index >= 0
            and self.profile_combo.currentText() == self.profile_combo.itemText(index)
        ):
            data = self.profile_combo.itemData(index)
            if data:
                return str(data)
        return self.profile_combo.currentText().strip()

    def _read_config(self) -> AppConfig:
        api_id_text = self.fields["api_id"].text().strip()
        if not api_id_text.isdigit():
            raise ValueError("Telegram API ID должен быть числом")
        config = AppConfig(
            api_id=int(api_id_text),
            api_hash=self.fields["api_hash"].text().strip(),
            phone=self.fields["phone"].text().strip(),
            bot_username=self.config.bot_username,
            chrome_binary=self.config.chrome_binary,
            chrome_user_data_dir=self.fields["chrome_user_data_dir"].text().strip(),
            chrome_profile=self._selected_profile() or "Default",
            chrome_debug_port=self.config.chrome_debug_port,
        )
        config.validate()
        return config

    def _start(self) -> None:
        try:
            self.config = self._read_config()
            self.config.save()
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Настройки", str(exc))
            return
        self.done_label.setText("0")
        self.skipped_label.setText("0")
        self.chrome_label.setText("Не подключён")
        self.worker = BotWorker(
            self.config, self._enqueue_log, self._prompt, self._event
        )
        self.worker.start()
        self._set_running_controls(True)

    def _open_profile_for_login(self) -> None:
        try:
            self.config = self._read_config()
            self.config.save()
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Настройки", str(exc))
            return
        self._append_log(
            f"Открываю управляемый профиль {self.config.chrome_profile} для входа в соцсети"
        )
        self.start_button.setEnabled(False)
        self.open_profile_button.setEnabled(False)
        self.profile_combo.setEnabled(False)
        self.refresh_profiles_button.setEnabled(False)
        thread = threading.Thread(
            target=self._open_profile_thread,
            args=(self.config,),
            name="chrome-profile-setup",
            daemon=True,
        )
        thread.start()

    def _open_profile_thread(self, config: AppConfig) -> None:
        controller = ChromeController(
            config,
            RunControl(),
            self._enqueue_log,
            lambda status: self._event("chrome", status),
        )
        try:
            controller.start()
            controller.release()
            self._event("profile_ready", config.chrome_profile)
        except Exception as exc:  # noqa: BLE001 - surface setup errors in the GUI
            controller.release()
            self._event("profile_error", str(exc))

    def _pause(self) -> None:
        if self.worker:
            self.worker.pause()
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(True)

    def _resume(self) -> None:
        if self.worker:
            self.worker.resume()
            self.pause_button.setEnabled(True)
            self.resume_button.setEnabled(False)

    def _skip(self) -> None:
        if self.worker:
            self.worker.skip()

    def _stop(self) -> None:
        if self.worker:
            self.worker.stop()
        self.stop_button.setEnabled(False)

    def _set_running_controls(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(False)
        self.skip_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        for field in self.fields.values():
            field.setEnabled(not running)
        self.profile_combo.setEnabled(not running)
        self.refresh_profiles_button.setEnabled(not running)
        self.open_profile_button.setEnabled(not running)

    def _enqueue_log(self, text: str) -> None:
        self.events.put(("log", text))

    def _event(self, kind: str, payload: object) -> None:
        self.events.put((kind, payload))

    def _prompt(self, title: str, prompt: str, secret: bool) -> str | None:
        response: queue.Queue[str | None] = queue.Queue(maxsize=1)
        self.events.put(("prompt", (title, prompt, secret, response)))
        return response.get()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "state":
                    self.state_label.setText(str(payload))
                elif kind == "chrome":
                    self.chrome_label.setText(str(payload))
                elif kind == "counters":
                    done, skipped = payload
                    self.done_label.setText(str(done))
                    self.skipped_label.setText(str(skipped))
                elif kind == "prompt":
                    title, prompt, secret, response = payload
                    mode = (
                        QLineEdit.EchoMode.Password
                        if secret
                        else QLineEdit.EchoMode.Normal
                    )
                    value, accepted = QInputDialog.getText(self, title, prompt, mode)
                    response.put(value if accepted else None)
                elif kind == "error":
                    QMessageBox.critical(self, "Behancer Bot", str(payload))
                elif kind == "finished":
                    self._set_running_controls(False)
                    if self._close_when_finished:
                        self._close_when_finished = False
                        self._force_close = True
                        self.close()
                elif kind == "profile_ready":
                    self._set_running_controls(False)
                    self._append_log(
                        f"Профиль {payload} открыт. Войдите в нужные соцсети; "
                        "после этого можно нажать «Старт»"
                    )
                elif kind == "profile_error":
                    self._set_running_controls(False)
                    QMessageBox.critical(self, "Профиль Chrome", str(payload))
        except queue.Empty:
            pass

    def _append_log(self, text: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.log_text.append(f"[{stamp}] {text}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_close:
            event.accept()
            return
        if self.worker and self.worker.running:
            answer = QMessageBox.question(
                self,
                "Выход",
                "Остановить бота и закрыть приложение? Chrome останется открытым.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._close_when_finished = True
            self.worker.stop()
            event.ignore()
            return
        event.accept()


class BehancerApp:
    def __init__(self) -> None:
        self.application = QApplication.instance() or QApplication(sys.argv)
        self.window = MainWindow()

    def run(self) -> None:
        self.window.show()
        raise SystemExit(self.application.exec())
