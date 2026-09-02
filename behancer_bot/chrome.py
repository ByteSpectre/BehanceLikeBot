from __future__ import annotations

import random
import shutil
import time
import zlib
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    JavascriptException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import DATA_DIR, AppConfig
from .control import RunControl
from .links import expand_redirect_url, is_behance_project

LogFn = Callable[[str], None]
StatusFn = Callable[[str], None]


class ChromeController:
    def __init__(
        self, config: AppConfig, control: RunControl, log: LogFn, status: StatusFn
    ) -> None:
        self.config = config
        self.control = control
        self.log = log
        self.status = status
        self.driver: ChromeWebDriver | None = None

    def start(self) -> None:
        self.status("Запуск")
        self.log(f"Выбран профиль Chrome: {self.config.chrome_profile}")
        if self._attach_to_debug_chrome():
            return
        self.log(
            "Рабочие окна Chrome не затрагиваются; запускаю отдельное управляемое "
            "окно на копии выбранного профиля"
        )
        self._launch_with_profile(self._prepare_automation_profile(), cloned=True)

    def _attach_to_debug_chrome(self) -> bool:
        target_root = self._automation_profile_root()
        if not (target_root / ".profile-initialized").exists():
            return False
        address = f"127.0.0.1:{self._debug_port()}"
        try:
            with urlopen(f"http://{address}/json/version", timeout=1):
                pass
        except (OSError, URLError):
            return False

        options = Options()
        if self.config.chrome_binary.strip():
            options.binary_location = self.config.chrome_binary.strip()
        options.debugger_address = address
        try:
            self.driver = ChromeWebDriver(options=options)
            self.driver.set_page_load_timeout(45)
        except WebDriverException as exc:
            self.log(f"Не удалось подключиться к Chrome на {address}: {exc.msg}")
            return False
        self.status("Подключён к открытому")
        self.log(f"Подключился к уже открытому Chrome через порт {address}")
        return True

    def _launch_with_profile(self, user_data_dir: Path, cloned: bool) -> None:
        options = Options()
        if self.config.chrome_binary.strip():
            options.binary_location = self.config.chrome_binary.strip()
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument(f"--profile-directory={self.config.chrome_profile}")
        options.add_argument(f"--remote-debugging-port={self._debug_port()}")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("detach", True)
        try:
            self.driver = ChromeWebDriver(options=options)
            self.driver.set_page_load_timeout(45)
        except WebDriverException as exc:
            self.status("Ошибка")
            raise RuntimeError(
                "ChromeDriver не смог запустить браузер: "
                f"{exc.msg.splitlines()[0] if exc.msg else type(exc).__name__}"
            ) from exc
        if cloned:
            self.status("Подключён (копия профиля)")
            self.log(
                "Google Chrome запущен на локальной копии профиля. Если Behance "
                "попросит вход, авторизуйтесь один раз в этом окне"
            )
        else:
            self.status("Подключён")
            self.log("Google Chrome запущен с пользовательским профилем")

    def _prepare_automation_profile(self) -> Path:
        source_root = Path(self.config.chrome_user_data_dir)
        source_profile = source_root / self.config.chrome_profile
        target_root = self._automation_profile_root()
        target_profile = target_root / self.config.chrome_profile
        marker = target_root / ".profile-initialized"
        if marker.exists() and target_profile.exists():
            return target_root

        self.status("Копирование профиля")
        self.log("Создаю локальную копию профиля Chrome (только при первом запуске)")
        target_root.mkdir(parents=True, exist_ok=True)
        if not source_profile.exists():
            raise RuntimeError(f"Профиль Chrome не найден: {source_profile}")

        local_state = source_root / "Local State"
        if local_state.exists():
            try:
                shutil.copy2(local_state, target_root / "Local State")
            except OSError as exc:
                self.log(f"Не удалось скопировать Local State: {exc}")

        ignored_names = {
            "Cache",
            "Code Cache",
            "GPUCache",
            "DawnCache",
            "GrShaderCache",
            "GraphiteDawnCache",
            "ShaderCache",
            "CacheStorage",
            "Crashpad",
            # Chrome 136+ intentionally cannot decrypt these databases after
            # copying them to a custom user-data directory. The managed profile
            # gets its own persistent sessions after the user signs in once.
            "Cookies",
            "Cookies-journal",
            "Login Data",
            "Login Data-journal",
        }

        def ignore_cache(_directory: str, names: list[str]) -> set[str]:
            return {
                name
                for name in names
                if name in ignored_names or name.startswith("Singleton")
            }

        try:
            shutil.copytree(
                source_profile,
                target_profile,
                dirs_exist_ok=True,
                ignore=ignore_cache,
            )
        except shutil.Error as exc:
            # Chrome may lock a nonessential file while the source profile is
            # open. copytree still copies all other accessible profile data.
            self.log(f"Часть файлов активного профиля пропущена: {len(exc.args[0])}")
        except OSError as exc:
            self.log(f"Часть профиля не удалось скопировать: {exc}")

        marker.write_text("initialized\n", encoding="utf-8")
        return target_root

    def _automation_profile_root(self) -> Path:
        safe_profile = (
            "".join(
                character if character.isalnum() else "_"
                for character in self.config.chrome_profile
            ).strip("_")
            or "Default"
        )
        return DATA_DIR / "chrome-user-data" / safe_profile

    def _debug_port(self) -> int:
        profile = self.config.chrome_profile
        if profile == "Default":
            return self.config.chrome_debug_port
        if profile.startswith("Profile "):
            suffix = profile.removeprefix("Profile ")
            if suffix.isdigit():
                return self.config.chrome_debug_port + max(1, int(suffix))
        offset = zlib.crc32(profile.encode("utf-8")) % 1000 + 1
        return self.config.chrome_debug_port + offset

    def perform_task(self, urls: Iterable[str]) -> bool:
        if self.driver is None:
            raise RuntimeError("Chrome не запущен")
        for url in urls:
            self.control.checkpoint()
            try:
                project_url = self._resolve_to_project(url)
                if not project_url:
                    self.log(f"Ссылка не ведёт на проект Behance: {url}")
                    continue
                self._navigate(project_url)
                if not is_behance_project(self.driver.current_url):
                    self.log("Открыта страница Behance, но это не проект")
                    continue
                delay = random.uniform(18, 25)
                self.log(f"Проект открыт. Ожидание перед лайком: {delay:.0f} сек.")
                self.control.sleep(delay)
                if self._appreciate():
                    return True
            except TimeoutException:
                self.log(f"Страница не загрузилась вовремя: {url}")
            except WebDriverException as exc:
                self.log(
                    f"Ошибка Chrome при обработке ссылки: {exc.msg.splitlines()[0]}"
                )
        return False

    def _navigate(self, url: str) -> None:
        assert self.driver is not None
        self.control.checkpoint()
        self.log(f"Открываю: {url}")
        self.driver.get(url)
        WebDriverWait(self.driver, 30).until(
            lambda driver: (
                driver.execute_script("return document.readyState")
                in ("interactive", "complete")
            )
        )
        self.control.sleep(1)

    def _resolve_to_project(self, url: str) -> str | None:
        assert self.driver is not None
        self._navigate(url)
        current = self.driver.current_url
        if is_behance_project(current):
            return current

        link_detection_delay = 5
        self.log(
            f"Ожидание загрузки внешней ссылки перед проверкой: "
            f"{link_detection_delay} сек."
        )
        self.control.sleep(link_detection_delay)
        current = self.driver.current_url
        if is_behance_project(current):
            return current

        host = (urlparse(current).hostname or "").lower()
        found = self._find_behance_link()
        if found:
            return found
        found = self._try_explicit_links()
        if found:
            return found

        if host == "facebook.com" or host.endswith(".facebook.com"):
            for attempt in range(5):
                self.control.checkpoint()
                found = self._find_behance_link()
                if found:
                    return found
                self.log(f"Ожидаю ссылку Behance в посте Facebook ({attempt + 1}/5)")
                self.control.sleep(2)
            return None
        if host == "x.com" or host.endswith(".x.com") or "twitter.com" in host:
            direct = self._find_behance_link()
            if direct:
                return direct
            short_links = (
                self.driver.execute_script(
                    """
                return [...document.querySelectorAll('a[href*="t.co/"]')]
                  .map(a => a.href);
                """
                )
                or []
            )
            for short_link in list(dict.fromkeys(short_links))[:10]:
                self.control.checkpoint()
                self._navigate(short_link)
                if is_behance_project(self.driver.current_url):
                    return self.driver.current_url
                self.driver.back()
                self.control.sleep(1)
        if "linkedin.com" in host:
            self._close_linkedin_popup()
            for attempt in range(5):
                self.control.checkpoint()
                found = self._find_behance_link()
                if found:
                    return found
                self._click_linkedin_post_content()
                self.control.sleep(2 + attempt)
                if is_behance_project(self.driver.current_url):
                    return self.driver.current_url
        else:
            found = self._find_behance_link()
            if found:
                return found
        return None

    def _try_explicit_links(self) -> str | None:
        links = self._collect_explicit_links()
        if not links:
            self.log("На странице не найдено явных ссылок для проверки")
            return None
        self.log(f"Найдено явных ссылок для проверки: {len(links)}")
        for index, (href, text) in enumerate(links, start=1):
            self.control.checkpoint()
            label = text if len(text) <= 80 else f"{text[:77]}..."
            self.log(f"Проверяю ссылку {index}/{len(links)}: {label or href}")
            project_url = self._visit_explicit_link(href, text)
            if project_url:
                self.log(f"Ссылка привела на проект Behance: {project_url}")
                return project_url
            self.log("Это не проект Behance — возвращаюсь к следующей ссылке")
        return None

    def _collect_explicit_links(self) -> list[tuple[str, str]]:
        assert self.driver is not None
        raw_links = (
            self.driver.execute_script(
                """
            const explicit = /(?:https?:\\/\\/|www\\.|behance\\.net|be\\.net|lnkd\\.in|t\\.co)/i;
            return [...document.querySelectorAll('a[href]')]
              .filter(a => {
                const rect = a.getBoundingClientRect();
                const style = getComputedStyle(a);
                const text = (a.innerText || a.textContent || '').trim();
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' && style.display !== 'none' &&
                  explicit.test(text);
              })
              .map(a => [a.href, (a.innerText || a.textContent || '').trim()]);
            """
            )
            or []
        )
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in raw_links:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            href, text = str(item[0]), str(item[1])
            if not href.lower().startswith(("http://", "https://")) or href in seen:
                continue
            seen.add(href)
            links.append((href, text))
        return links

    def _visit_explicit_link(self, href: str, text: str) -> str | None:
        assert self.driver is not None
        source_handle = self.driver.current_window_handle
        source_url = self.driver.current_url
        handles_before = set(self.driver.window_handles)
        element = self.driver.execute_script(
            """
            const [href, text] = arguments;
            return [...document.querySelectorAll('a[href]')].find(a =>
              a.href === href && (a.innerText || a.textContent || '').trim() === text
            ) || [...document.querySelectorAll('a[href]')].find(a => a.href === href) || null;
            """,
            href,
            text,
        )
        if element is None or not self._click_element(element):
            self.log("Не удалось кликнуть по явной ссылке")
            return None

        opened_new_tab = False
        started = time.monotonic()
        while time.monotonic() - started < 12:
            self.control.checkpoint()
            new_handles = set(self.driver.window_handles) - handles_before
            if new_handles and not opened_new_tab:
                self.driver.switch_to.window(next(iter(new_handles)))
                opened_new_tab = True
            current_url = self.driver.current_url
            if is_behance_project(current_url):
                return current_url
            elapsed = time.monotonic() - started
            if elapsed >= 2 and current_url not in (source_url, "about:blank"):
                try:
                    ready = self.driver.execute_script("return document.readyState")
                except WebDriverException:
                    ready = None
                if ready == "complete":
                    break
            self.control.sleep(0.25)

        self._restore_source_page(source_handle, source_url, opened_new_tab)
        return None

    def _restore_source_page(
        self, source_handle: str, source_url: str, opened_new_tab: bool
    ) -> None:
        assert self.driver is not None
        if opened_new_tab:
            try:
                self.driver.close()
            except WebDriverException:
                pass
            self.driver.switch_to.window(source_handle)
            return
        if self.driver.current_url == source_url:
            return
        try:
            self.driver.back()
            WebDriverWait(self.driver, 10).until(
                lambda driver: (
                    driver.current_url == source_url
                    or driver.execute_script("return document.readyState") == "complete"
                )
            )
        except (TimeoutException, WebDriverException):
            self.driver.get(source_url)
        self.control.sleep(1)

    def _close_linkedin_popup(self) -> None:
        assert self.driver is not None
        selectors = (
            "button[aria-label*='Dismiss']",
            "button[aria-label*='Закрыть']",
            ".contextual-sign-in-modal__modal-dismiss",
            "button.modal__dismiss",
        )
        for selector in selectors:
            try:
                button = self.driver.find_element(By.CSS_SELECTOR, selector)
                if button.is_displayed():
                    button.click()
                    self.log("Popup LinkedIn закрыт")
                    return
            except (NoSuchElementException, ElementClickInterceptedException):
                pass

    def _click_linkedin_post_content(self) -> None:
        assert self.driver is not None
        xpaths = (
            "//article//a[contains(@href,'behance.net') or contains(@href,'be.net')]",
            "//article//*[self::h1 or self::h2 or self::h3]//a",
            "//*[contains(@class,'attributed-text')]//a",
        )
        for xpath in xpaths:
            try:
                element = self.driver.find_element(By.XPATH, xpath)
                if element.is_displayed():
                    element.click()
                    return
            except (NoSuchElementException, ElementClickInterceptedException):
                pass

    def _find_behance_link(self) -> str | None:
        assert self.driver is not None
        raw_candidates = (
            self.driver.execute_script(
                """
            const values = [];
            for (const a of document.querySelectorAll('a')) {
              values.push(
                a.href,
                a.getAttribute('href'),
                a.getAttribute('data-lynx-uri'),
                a.getAttribute('data-url'),
                a.getAttribute('data-href')
              );
            }
            return values.filter(value => value && (
              /behance(?:\\.|%2e)net|be(?:\\.|%2e)net/i.test(value) ||
              /facebook\\.com\\/l\\.php/i.test(value)
            ));
            """
            )
            or []
        )
        candidates: list[str] = []
        for raw_candidate in raw_candidates:
            for candidate in expand_redirect_url(str(raw_candidate)):
                if candidate not in candidates:
                    candidates.append(candidate)
        projects = [
            candidate for candidate in candidates if is_behance_project(candidate)
        ]
        if projects:
            self.log(f"Найдена ссылка на проект Behance: {projects[0]}")
            return projects[0]
        # be.net is a Behance short URL; navigating it is required to know whether
        # it resolves to a project. Plain Behance profile links are not candidates.
        short_urls = [
            candidate
            for candidate in candidates
            if (urlparse(candidate).hostname or "").lower().removeprefix("www.")
            == "be.net"
        ]
        if short_urls:
            self._navigate(short_urls[0])
            return (
                self.driver.current_url
                if is_behance_project(self.driver.current_url)
                else None
            )
        return None

    def _appreciate(self) -> bool:
        assert self.driver is not None
        if self._already_appreciated():
            self.log("Лайк уже был поставлен")
            return True

        selectors = (
            (By.CSS_SELECTOR, "button[data-adobe-analytics='AppreciateClick']"),
            (By.CSS_SELECTOR, "[aria-label='Оценить']"),
            (By.CSS_SELECTOR, "[aria-label*='Appreciate']"),
            (
                By.XPATH,
                "//button[contains(translate(., 'APPRECIATELIKEОЦЕНИТЬ', 'appreciatelikeоценить'), 'оценить')]",
            ),
            (
                By.XPATH,
                "//button[contains(translate(., 'APPRECIATE', 'appreciate'), 'appreciate')]",
            ),
        )
        for by, selector in selectors:
            self.control.checkpoint()
            try:
                element = WebDriverWait(self.driver, 4).until(
                    EC.presence_of_element_located((by, selector))
                )
                if self._click_element(element):
                    self.control.sleep(1.5)
                    self.log("Лайк поставлен")
                    return True
            except TimeoutException:
                continue

        try:
            element = self.driver.execute_script(
                """
                const words = /appreciate|like|оценить|лайк/i;
                const nodes = [...document.querySelectorAll('button,[role="button"],a')];
                const scored = nodes.map(el => {
                  const text = [el.innerText, el.ariaLabel, el.title,
                    el.dataset?.adobeAnalytics, el.className].join(' ');
                  let score = words.test(text) ? 10 : 0;
                  if (/AppreciateClick/i.test(text)) score += 20;
                  if (el.offsetParent !== null) score += 2;
                  return [score, el];
                }).filter(x => x[0] >= 10).sort((a,b) => b[0] - a[0]);
                return scored.length ? scored[0][1] : null;
                """
            )
            if element is not None and self._click_element(element):
                self.control.sleep(1.5)
                self.log("Лайк поставлен (резервный поиск)")
                return True
        except JavascriptException:
            pass
        self.log("Не удалось найти или нажать кнопку лайка")
        return False

    def _already_appreciated(self) -> bool:
        assert self.driver is not None
        return bool(
            self.driver.execute_script(
                """
                const nodes = [...document.querySelectorAll(
                  '[data-adobe-analytics="AppreciateClick"],[aria-label*="Appreciate"],[aria-label*="Оценить"]'
                )];
                return nodes.some(el =>
                  el.getAttribute('aria-pressed') === 'true' ||
                  /appreciated|active|selected/i.test(el.className) ||
                  /remove appreciation|убрать оценку/i.test(el.ariaLabel || '')
                );
                """
            )
        )

    def _click_element(self, element: object) -> bool:
        assert self.driver is not None
        methods = (
            lambda: element.click(),
            lambda: (
                ActionChains(self.driver)
                .move_to_element(element)
                .pause(0.2)
                .click()
                .perform()
            ),
            lambda: self.driver.execute_script("arguments[0].click()", element),
        )
        for method in methods:
            try:
                method()
                return True
            except WebDriverException:
                continue
        return False

    def release(self) -> None:
        """Release WebDriver while leaving detached Chrome open."""
        if self.driver is None:
            return
        try:
            self.driver.service.stop()
        except (OSError, WebDriverException) as exc:
            self.log(f"Не удалось корректно отпустить ChromeDriver: {exc}")
        self.driver = None
        self.status("Оставлен открытым")
        self.log("Управление Chrome отпущено; окно оставлено открытым")
