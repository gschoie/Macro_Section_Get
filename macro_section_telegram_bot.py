import argparse
import html
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


KST = timezone(timedelta(hours=9), name="KST")
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "macro_section_captures"


@dataclass(frozen=True)
class Section:
    name: str
    url: str


SECTIONS = [
    Section("Infomax macro", "https://news.einfomax.co.kr/news/articleList.html?sc_section_code=S1N16&view_type=sm"),
    Section("Maeil Business index", "https://www.mk.co.kr/news/economy/business-index"),
    Section("Hankyung macro", "https://www.hankyung.com/economy/macro"),
    Section("Yonhap economy", "https://www.yna.co.kr/economy/all"),
]


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def telegram_api(method: str) -> str:
    return f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/{method}"


def post_form(method: str, fields: dict[str, str]) -> None:
    payload = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        telegram_api(method),
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


def post_multipart(method: str, fields: dict[str, str], files: dict[str, Path]) -> None:
    boundary = f"----codex-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    for name, path in files.items():
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
                path.read_bytes(),
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    request = urllib.request.Request(
        telegram_api(method),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        response.read()


def send_message(text: str) -> None:
    post_form(
        "sendMessage",
        {
            "chat_id": env("TELEGRAM_CHAT_ID"),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
    )


def send_photo(path: Path, caption: str) -> None:
    post_multipart(
        "sendPhoto",
        {
            "chat_id": env("TELEGRAM_CHAT_ID"),
            "caption": caption[:1024],
            "parse_mode": "HTML",
        },
        {"photo": path},
    )


def format_run_header(now: datetime) -> str:
    weekdays = ["\uc6d4", "\ud654", "\uc218", "\ubaa9", "\uae08", "\ud1a0", "\uc77c"]
    day = weekdays[now.weekday()]
    date_text = now.strftime("%y.%m.%d")
    time_text = f"{now.hour}:{now.minute:02d}"
    return (
        "==============================\n"
        f"{date_text}({day}) {time_text} \uc5c5\ub370\uc774\ud2b8\uc785\ub2c8\ub2e4\n"
        "=============================="
    )


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def absolute_url(page_url: str, href: str) -> str:
    return urllib.parse.urljoin(page_url, href)


def looks_like_article(title: str, href: str) -> bool:
    if not href.startswith(("http://", "https://", "/")):
        return False
    if len(title) < int(os.getenv("MACRO_MIN_TITLE_LENGTH", "8")):
        return False
    if len(title) > 130:
        return False
    lowered = title.lower()
    blocked_words = {
        "login",
        "facebook",
        "twitter",
        "youtube",
        "instagram",
    }
    if any(word in lowered for word in blocked_words):
        return False
    return True


def unique_articles(items: Iterable[dict[str, str]], limit: int) -> list[dict[str, str]]:
    seen: set[str] = set()
    articles: list[dict[str, str]] = []
    for item in items:
        key = item["url"].split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        articles.append(item)
        if len(articles) >= limit:
            break
    return articles


def prepare_page(page: Page, section: Section) -> None:
    page.goto(section.url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    page.evaluate(
        """
        () => {
          for (const selector of [
            'iframe',
            '[id*="ad"]',
            '[class*="ad"]',
            '[class*="popup"]',
            '[class*="layer"]',
            '[class*="cookie"]'
          ]) {
            document.querySelectorAll(selector).forEach((node) => node.remove());
          }
        }
        """
    )


def extract_articles(page: Page, section: Section, limit: int) -> list[dict[str, str]]:
    raw_items = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href]')).map((anchor) => {
          const rect = anchor.getBoundingClientRect();
          return {
            title: anchor.innerText || anchor.textContent || '',
            href: anchor.getAttribute('href') || '',
            top: rect.top + window.scrollY,
            left: rect.left + window.scrollX
          };
        })
        """
    )
    candidates = []
    for item in raw_items:
        title = normalize_space(str(item.get("title", "")))
        href = str(item.get("href", "")).strip()
        if not looks_like_article(title, href):
            continue
        candidates.append(
            {
                "title": title,
                "url": absolute_url(section.url, href),
                "top": float(item.get("top") or 0),
                "left": float(item.get("left") or 0),
            }
        )
    candidates.sort(key=lambda item: (item["top"], item["left"]))
    return unique_articles(candidates, limit)


def screenshot_page(page: Page, section: Section, stamp: str) -> Path:
    safe_name = re.sub(r"[^0-9A-Za-z_-]+", "_", section.name).strip("_")
    path = OUTPUT_DIR / f"{stamp}_{safe_name}.png"
    full_page = os.getenv("MACRO_FULL_PAGE_SCREENSHOT", "false").lower() == "true"
    page.screenshot(path=str(path), full_page=full_page)
    return path


def format_article_message(section: Section, articles: list[dict[str, str]], checked_at: str) -> str:
    lines = [
        f"<b>{html.escape(section.name)}</b>",
        f"Checked: {html.escape(checked_at)} KST",
        f"Section: {html.escape(section.url)}",
        "",
    ]
    if not articles:
        lines.append("No article titles were extracted. Please check the screenshot.")
    else:
        for index, item in enumerate(articles, start=1):
            title = html.escape(item["title"])
            url = html.escape(item["url"])
            lines.append(f"{index}. <a href=\"{url}\">{title}</a>")
    return "\n".join(lines)


def run_once() -> int:
    OUTPUT_DIR.mkdir(exist_ok=True)
    now_kst = datetime.now(KST)
    checked_at = now_kst.strftime("%Y-%m-%d %H:%M")
    stamp = now_kst.strftime("%Y%m%d_%H%M%S")
    limit = int(os.getenv("MACRO_ARTICLE_LIMIT", "15"))
    width = int(os.getenv("MACRO_VIEWPORT_WIDTH", "1440"))
    height = int(os.getenv("MACRO_VIEWPORT_HEIGHT", "1800"))
    headless = os.getenv("MACRO_HEADLESS", "true").lower() != "false"

    send_message(format_run_header(now_kst))

    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                viewport={"width": width, "height": height},
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            for section in SECTIONS:
                page = context.new_page()
                try:
                    prepare_page(page, section)
                    articles = extract_articles(page, section, limit)
                    screenshot = screenshot_page(page, section, stamp)
                    caption = f"<b>{html.escape(section.name)}</b>\n{html.escape(checked_at)} KST"
                    send_photo(screenshot, caption)
                    send_message(format_article_message(section, articles, checked_at))
                    print(f"Sent {section.name}: {len(articles)} article(s)", flush=True)
                except Exception as exc:
                    message = f"<b>{html.escape(section.name)}</b>\nFailed: {html.escape(str(exc))}"
                    print(message, file=sys.stderr, flush=True)
                    if os.getenv("TELEGRAM_NOTIFY_ERRORS", "false").lower() == "true":
                        send_message(message)
                finally:
                    page.close()
        finally:
            browser.close()
    return 0


def sleep_until_next_hour() -> None:
    now = time.time()
    time.sleep(3600 - (int(now) % 3600))


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture macro news sections and send them to Telegram.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--loop", action="store_true", help="Run every hour.")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    loop = args.loop or os.getenv("RUN_MODE", "once").lower() == "loop"
    if args.once:
        loop = False

    while True:
        try:
            run_once()
        except (urllib.error.URLError, RuntimeError) as exc:
            print(f"Macro section bot error: {exc}", file=sys.stderr, flush=True)
            if os.getenv("TELEGRAM_NOTIFY_ERRORS", "false").lower() == "true":
                send_message(f"Macro section bot error: {html.escape(str(exc))}")
        if not loop:
            return 0
        sleep_until_next_hour()


if __name__ == "__main__":
    raise SystemExit(main())
