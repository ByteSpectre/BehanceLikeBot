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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .comments import pick_comment
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
        return self._perform_on_project(urls, action="like")

    def perform_comment_task(self, urls: Iterable[str]) -> bool:
        return self._perform_on_project(urls, action="comment")

    def _perform_on_project(self, urls: Iterable[str], action: str) -> bool:
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
                if action == "comment":
                    self.log(
                        f"Проект открыт. Ожидание перед комментарием: {delay:.0f} сек."
                    )
                    self.control.sleep(delay)
                    if self._post_comment():
                        return True
                else:
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

    def _post_comment(self) -> bool:
        assert self.driver is not None
        self.driver.switch_to.default_content()
        field = self._wait_for_comment_field()
        comment = pick_comment()
        self.log(f"Выбран комментарий: {comment}")
        if not self._fill_comment_field(field, comment):
            self.log("Не удалось ввести текст комментария")
            return False
        self.control.sleep(0.6)
        if not self._submit_comment(field, comment):
            self.log("Комментарий введён, но кнопка отправки его не опубликовала")
            return False
        if self._wait_until_comment_visible(comment, field):
            self.log("Комментарий опубликован")
            return True
        self.log("Комментарий не появился на странице проекта")
        return False

    def _wait_for_comment_field(self) -> object:
        """Reload the project until the comment box is actually visible."""
        attempt = 1
        while True:
            self.control.checkpoint()
            field = self._locate_comment_field()
            if field is not None:
                return field
            self.log(
                "Комментарии не отображаются — обновляю страницу "
                f"(попытка {attempt})"
            )
            self._reload_page()
            attempt += 1

    def _locate_comment_field(self) -> object | None:
        self._scroll_to_comments()
        field = self._find_comment_field()
        if field is None and self._click_comments_opener():
            self.control.sleep(1.5)
            field = self._find_comment_field()
        return field

    def _reload_page(self) -> None:
        assert self.driver is not None
        self.control.checkpoint()
        self.driver.switch_to.default_content()
        self.driver.refresh()
        WebDriverWait(self.driver, 30).until(
            lambda driver: driver.execute_script("return document.readyState")
            in ("interactive", "complete")
        )
        self.control.sleep(1)

    def _scroll_to_comments(self) -> None:
        assert self.driver is not None
        self.driver.execute_script(
            """
            const height = Math.max(
              document.body ? document.body.scrollHeight : 0,
              document.documentElement ? document.documentElement.scrollHeight : 0
            );
            window.scrollTo(0, height);
            """
        )
        self.control.sleep(1)

    def _click_comments_opener(self) -> bool:
        assert self.driver is not None
        clicked = bool(
            self.driver.execute_script(
                """
                const labelOf = (el) => [
                  el.innerText, el.getAttribute('aria-label'), el.getAttribute('title')
                ].filter(Boolean).join(' ');
                const opener = [...document.querySelectorAll('button,[role="button"],a')]
                  .find(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) return false;
                    const label = labelOf(el);
                    return /\\bcomments?\\b|комментар/i.test(label) &&
                      !/add a comment|leave a comment|напишите|добавить комментарий/i.test(label);
                  });
                if (!opener) return false;
                opener.click();
                return true;
                """
            )
        )
        if clicked:
            self.log("Открываю блок комментариев")
        return clicked

    def _find_comment_field(self) -> object | None:
        assert self.driver is not None
        self.driver.switch_to.default_content()
        field = self._find_comment_field_here()
        if field is not None:
            return field
        for frame in self.driver.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame(frame)
            except WebDriverException:
                continue
            field = self._find_comment_field_here()
            if field is not None:
                return field
        self.driver.switch_to.default_content()
        return None

    def _find_comment_field_here(self) -> object | None:
        assert self.driver is not None
        field = self._find_known_comment_field()
        if field is not None:
            self.log("Найдено поле комментария Behance")
            return field
        try:
            return self.driver.execute_script(
                """
                const all = [];
                const visit = (root) => {
                  if (!root || !root.querySelectorAll) return;
                  all.push(...root.querySelectorAll(
                    'textarea, input[type="text"], [contenteditable="true"]'
                  ));
                  for (const el of root.querySelectorAll('*')) {
                    if (el.shadowRoot) visit(el.shadowRoot);
                  }
                };
                visit(document);
                const blob = (el) => [
                  el.placeholder, el.getAttribute('aria-label'),
                  el.getAttribute('data-placeholder'),
                  el.getAttribute('aria-placeholder'), el.innerText,
                  el.name, el.id, el.className
                ].filter(Boolean).join(' ');
                const visible = (el) => {
                  const rect = el.getBoundingClientRect();
                  const style = getComputedStyle(el);
                  return rect.width > 20 && rect.height > 8 &&
                    style.visibility !== 'hidden' && style.display !== 'none';
                };
                const inComments = (el) => {
                  let node = el;
                  for (let depth = 0; depth < 8 && node; depth += 1) {
                    const marker = [
                      node.id, node.className, node.getAttribute?.('aria-label')
                    ].filter(Boolean).join(' ');
                    if (/comment|коммент/i.test(marker)) return true;
                    node = node.parentElement;
                  }
                  return false;
                };
                const ranked = all.map(el => {
                  const text = blob(el);
                  let score = 0;
                  if (/comment|коммент|отзыв|feedback/i.test(text)) score += 20;
                  if (inComments(el)) score += 15;
                  if (visible(el)) score += 8;
                  if (el.tagName === 'TEXTAREA' || el.isContentEditable) score += 3;
                  if (/search|поиск|email|password|пароль/i.test(text)) score -= 40;
                  return [score, el];
                }).filter(pair => pair[0] >= 20).sort((a, b) => b[0] - a[0]);
                if (!ranked.length) return null;
                ranked[0][1].scrollIntoView({block: 'center'});
                return ranked[0][1];
                """
            )
        except JavascriptException:
            return None

    def _find_known_comment_field(self) -> object | None:
        """Find the Behance project comment box by its stable class prefixes."""
        assert self.driver is not None
        selector = (
            "textarea[class*='ProjectCommentInput-commentTextArea'], "
            "textarea[class*='TextArea-textarea']"
        )
        try:
            WebDriverWait(self.driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
        except TimeoutException:
            return None
        field = self._prefer_comment_field(
            self.driver.find_elements(By.CSS_SELECTOR, selector)
        )
        if field is None:
            return None
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", field
            )
        except JavascriptException:
            pass
        return field

    @staticmethod
    def _prefer_comment_field(elements: list[object]) -> object | None:
        def score(element: object) -> int:
            classes = element.get_attribute("class") or ""
            if "ProjectCommentInput-commentTextArea" in classes:
                return 2
            if "TextArea-textarea" in classes:
                return 1
            return 0

        visible: list[object] = []
        for element in elements:
            try:
                if score(element) and element.is_displayed():
                    visible.append(element)
            except WebDriverException:
                continue
        if not visible:
            return None
        field = max(visible, key=score)
        return field

    def _fill_comment_field(self, element: object, text: str) -> bool:
        assert self.driver is not None
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'}); arguments[0].focus();",
                element,
            )
        except JavascriptException:
            pass
        self._click_element(element)
        self.control.sleep(0.3)
        try:
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.DELETE)
            element.send_keys(text)
        except WebDriverException:
            pass
        self._notify_comment_input(element, text)
        return text in self._field_text(element)

    def _field_text(self, element: object) -> str:
        assert self.driver is not None
        try:
            return (
                self.driver.execute_script(
                    """
                    const el = arguments[0];
                    if (!el) return '';
                    if (el.isContentEditable) {
                      return (el.innerText || el.textContent || '').trim();
                    }
                    return (el.value || '').trim();
                    """,
                    element,
                )
                or ""
            )
        except (JavascriptException, WebDriverException):
            return ""

    def _notify_comment_input(self, element: object, text: str) -> None:
        """Tell React the textarea changed so the Post button becomes active."""
        assert self.driver is not None
        try:
            self.driver.execute_script(
                """
                const el = arguments[0];
                const value = arguments[1];
                el.focus();
                const proto = el.tagName === 'TEXTAREA'
                  ? HTMLTextAreaElement.prototype
                  : HTMLInputElement.prototype;
                const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
                if (descriptor && descriptor.set) descriptor.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new InputEvent('input', {
                  bubbles: true,
                  cancelable: true,
                  data: value,
                  inputType: 'insertFromPaste'
                }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                element,
                text,
            )
        except JavascriptException:
            return

    def _submit_comment(self, field: object, text: str) -> bool:
        assert self.driver is not None
        for attempt in range(1, 4):
            self.control.checkpoint()
            self._notify_comment_input(field, text)
            button = self._find_comment_submit_button(field)
            if button is None:
                self.log(
                    f"Кнопка отправки внутри блока комментария не найдена "
                    f"({attempt}/3)"
                )
                self.control.sleep(0.6)
                continue
            self._wait_until_comment_button_enabled(button)
            if self._comment_button_disabled(button):
                self.log(f"Кнопка отправки ещё неактивна ({attempt}/3)")
                self.control.sleep(0.6)
                continue
            label = self._button_label(button)
            self.log(f"Нажимаю кнопку «{label or 'Post a Comment'}»")
            self._click_post_comment_button(button)
            self._request_comment_form_submit(field)
            self.control.sleep(0.8)
            if self._comment_left_field(field, text):
                return True
            self.log("Текст остался в поле — повторяю отправку")
        return False

    def _find_comment_submit_button(self, field: object) -> object | None:
        assert self.driver is not None
        try:
            return self.driver.execute_script(
                """
                const field = arguments[0];
                const hasPrefix = (el, prefix) => [...el.classList].some(name =>
                  name === prefix || name.startsWith(prefix + '-')
                );
                const isCommentButton = (button) => {
                  if (
                    !hasPrefix(button, 'Btn-button') ||
                    !hasPrefix(button, 'Btn-base') ||
                    !hasPrefix(button, 'Btn-normal')
                  ) {
                    return false;
                  }
                  const label = [
                    button.innerText, button.getAttribute('aria-label'), button.className
                  ].join(' ');
                  return !/appreciate|оценить|\\blike\\b|лайк/i.test(label);
                };
                const textOf = (el) => (el.innerText || el.textContent || '')
                  .replace(/\\s+/g, ' ')
                  .trim();
                const postButton = [...document.querySelectorAll('button')]
                  .find(button => /post a comment/i.test(textOf(button)));
                if (postButton) {
                  postButton.scrollIntoView({block: 'center'});
                  return postButton;
                }
                let composer = null;
                let node = field.parentElement;
                for (let depth = 0; depth < 12 && node; depth += 1) {
                  if (/ProjectComment/i.test(String(node.className || ''))) composer = node;
                  node = node.parentElement;
                }
                composer = composer || field.closest('form') || document.body;
                const buttons = [...composer.querySelectorAll('button')]
                  .filter(isCommentButton);
                return buttons.find(button => /post|опублик|отправ/i.test(textOf(button)))
                  || buttons[buttons.length - 1]
                  || null;
                """,
                field,
            )
        except JavascriptException:
            return None

    def _button_label(self, button: object) -> str:
        assert self.driver is not None
        try:
            return (
                self.driver.execute_script(
                    """
                    const button = arguments[0];
                    return (button.innerText || button.textContent || '')
                      .replace(/\\s+/g, ' ')
                      .trim();
                    """,
                    button,
                )
                or ""
            )
        except JavascriptException:
            return ""

    def _click_post_comment_button(self, button: object) -> None:
        assert self.driver is not None
        try:
            self.driver.execute_script(
                """
                const button = arguments[0];
                button.scrollIntoView({block: 'center'});
                const label = button.querySelector('[class*="Btn-label"]') || button;
                for (const target of [label, button]) {
                  target.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
                  target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                  target.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
                  target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                  target.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                }
                """,
                button,
            )
        except JavascriptException:
            pass
        self._click_element(button)

    def _comment_button_disabled(self, button: object) -> bool:
        assert self.driver is not None
        try:
            return bool(
                self.driver.execute_script(
                    """
                    const button = arguments[0];
                    return Boolean(
                      button.disabled || button.getAttribute('aria-disabled') === 'true'
                    );
                    """,
                    button,
                )
            )
        except JavascriptException:
            return False

    def _request_comment_form_submit(self, field: object) -> None:
        assert self.driver is not None
        try:
            self.driver.execute_script(
                """
                const field = arguments[0];
                const form = field.closest('form');
                if (!form || typeof form.requestSubmit !== 'function') return;
                const submitter = [...form.querySelectorAll('button, [type="submit"]')]
                  .find(button => !button.disabled && button.getAttribute('aria-disabled') !== 'true');
                if (submitter) form.requestSubmit(submitter);
                else form.requestSubmit();
                """,
                field,
            )
        except JavascriptException:
            return

    def _comment_left_field(self, field: object, text: str) -> bool:
        return text not in self._field_text(field)

    def _wait_until_comment_button_enabled(self, button: object) -> None:
        assert self.driver is not None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.control.checkpoint()
            try:
                disabled = bool(
                    self.driver.execute_script(
                        """
                        const button = arguments[0];
                        return Boolean(
                          button.disabled || button.getAttribute('aria-disabled') === 'true'
                        );
                        """,
                        button,
                    )
                )
            except JavascriptException:
                return
            if not disabled:
                return
            self.control.sleep(0.25)

    def _wait_until_comment_visible(self, comment: str, field: object) -> bool:
        assert self.driver is not None
        deadline = time.monotonic() + 8
        retry_after = time.monotonic() + 2
        retried_submit = False
        saw_text_in_field = False
        empty_since: float | None = None
        while time.monotonic() < deadline:
            self.control.checkpoint()
            presence = self._comment_presence(comment, field)
            page_count = presence["page_count"]
            field_count = presence["field_count"]
            if field_count > 0:
                saw_text_in_field = True
                empty_since = None
            elif page_count >= 1:
                return True
            elif saw_text_in_field:
                if empty_since is None:
                    empty_since = time.monotonic()
                elif time.monotonic() - empty_since >= 1.5:
                    self.log("Поле комментария очистилось после отправки")
                    return True
            if (
                not retried_submit
                and field_count > 0
                and time.monotonic() >= retry_after
            ):
                self._submit_comment(field, comment)
                retried_submit = True
            self.control.sleep(0.5)
        return False

    def _comment_presence(self, comment: str, field: object) -> dict[str, int]:
        assert self.driver is not None
        try:
            raw = self.driver.execute_script(
                """
                const text = arguments[0];
                const field = arguments[1];
                const fieldText = !field ? '' : (
                  field.isContentEditable
                    ? (field.innerText || field.textContent || '')
                    : (field.value || '')
                );
                const page = document.body ? (document.body.innerText || '') : '';
                const count = (source) => source.split(text).length - 1;
                return {pageCount: count(page), fieldCount: count(fieldText)};
                """,
                comment,
                field,
            )
        except (JavascriptException, WebDriverException):
            return {"page_count": 0, "field_count": 1}
        if not isinstance(raw, dict):
            return {"page_count": 0, "field_count": 1}
        return {
            "page_count": int(raw.get("pageCount") or 0),
            "field_count": int(raw.get("fieldCount") or 0),
        }

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
