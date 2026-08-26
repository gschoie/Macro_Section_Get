import argparse
import hashlib
import html
import json
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
STATE_PATH = BASE_DIR / "macro_section_state.json"


@dataclass(frozen=True)
class Section:
    name: str
    url: str
    limit: int | None = None  # None이면 전역 MACRO_ARTICLE_LIMIT 사용
    # 특정 사이트(네이버 등)는 CSS 셀렉터로 기사 링크를 직접 잡는다.
    # 지정되면 기하학적 추출 대신 이 셀렉터의 DOM 순서를 사용.
    list_selector: str | None = None


# 회전(--rotate) 발송 순서이기도 하다: 10분 간격으로 위에서부터 한 섹션씩.
SECTIONS = [
    Section("Naver economy", "https://news.naver.com/section/101", limit=5, list_selector="a.sa_text_title"),
    Section(
        "Naver global economy",
        "https://news.naver.com/breakingnews/section/101/262",
        limit=5,
        list_selector="a.sa_text_title",
    ),
    Section("Infomax macro", "https://news.einfomax.co.kr/news/articleList.html?sc_section_code=S1N16&view_type=sm"),
    Section("Yonhap economy", "https://www.yna.co.kr/economy/all"),
    Section("Maeil Business index", "https://www.mk.co.kr/news/economy/business-index"),
    Section("Hankyung macro", "https://www.hankyung.com/economy/macro"),
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
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    day = weekdays[now.weekday()]
    date_text = now.strftime("%y.%m.%d")
    time_text = f"{now.hour}:{now.minute:02d}"
    return (
        "==============================\n"
        f"{date_text}({day}) {time_text} 업데이트입니다\n"
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
          // Ad/overlay removal. Use token/prefix matching so substrings like
          // "headline"/"header" (which contain "ad") are NOT removed by accident.
          for (const selector of [
            'iframe',
            '[class~="ad"]',
            '[class~="ads"]',
            '[class~="advertisement"]',
            '[class*="advert"]',
            '[class*="-ad-"]',
            '[class*="_ad_"]',
            '[class^="ad-"]',
            '[class^="ad_"]',
            '[class$="-ad"]',
            '[class$="_ad"]',
            '[class*="banner"]',
            '[id*="advert"]',
            '[id*="banner"]',
            '[id^="ad-"]',
            '[id^="ad_"]',
            '[id~="ad"]',
            '[class*="popup"]',
            '[class~="layer"]',
            '[class*="cookie"]'
          ]) {
            document.querySelectorAll(selector).forEach((node) => node.remove());
          }
        }
        """
    )


def _first_line_title_js() -> str:
    # Title = first non-empty line; if too short (category badge like "방산"),
    # append the next line.
    return (
        "const lines = (anchor.innerText || anchor.textContent || '')"
        ".split('\\n').map((s) => s.trim()).filter(Boolean);"
        "let title = lines[0] || '';"
        "if (title.length < 8 && lines.length > 1) { title = (title + ' ' + lines[1]).trim(); }"
    )


def extract_articles(page: Page, section: Section, limit: int) -> list[dict[str, str]]:
    if section.list_selector:
        # 사이트 전용 셀렉터: DOM 순서를 그대로 사용(정렬하지 않음).
        raw_items = page.evaluate(
            """
            (selector) => Array.from(document.querySelectorAll(selector)).map((anchor) => {
              %s
              return { title: title, href: anchor.getAttribute('href') || '' };
            })
            """
            % _first_line_title_js(),
            section.list_selector,
        )
        candidates = []
        for item in raw_items:
            title = normalize_space(str(item.get("title", "")))
            href = str(item.get("href", "")).strip()
            if not looks_like_article(title, href):
                continue
            candidates.append({"title": title, "url": absolute_url(section.url, href)})
        return unique_articles(candidates, limit)

    # 기본: 화면에 보이는 본문(왼쪽) 열의 링크만 위치 순으로 추출.
    # 오른쪽 사이드바("많이 본 뉴스"/"베스트 클릭")와 숨겨진 링크를 제거한다.
    main_col_ratio = float(os.getenv("MACRO_MAIN_COLUMN_RATIO", "0.66"))
    raw_items = page.evaluate(
        """
        (ratio) => {
          const maxLeft = window.innerWidth * ratio;
          return Array.from(document.querySelectorAll('a[href]')).map((anchor) => {
            const rect = anchor.getBoundingClientRect();
            const style = getComputedStyle(anchor);
            const visible =
              style.display !== 'none' &&
              style.visibility !== 'hidden' &&
              style.opacity !== '0' &&
              rect.width > 1 &&
              rect.height > 1;
            %s
            return {
              title: title,
              href: anchor.getAttribute('href') || '',
              top: rect.top + window.scrollY,
              left: rect.left + window.scrollX,
              visible: visible,
            };
          }).filter(
            (item) =>
              item.visible && item.left >= 0 && item.top >= 0 && item.left < maxLeft
          );
        }
        """
        % _first_line_title_js(),
        main_col_ratio,
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


def fingerprint_articles(articles: list[dict[str, str]]) -> str:
    """Stable fingerprint of the current top-N article list (URL based)."""
    keys = [item["url"].split("#", 1)[0] for item in articles]
    digest = hashlib.sha256("\n".join(keys).encode("utf-8"))
    return digest.hexdigest()


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def format_article_message(
    section: Section,
    articles: list[dict[str, str]],
    checked_at: str,
    changed: bool,
) -> str:
    lines = [
        f"<b>{html.escape(section.name)}</b>",
        f"Checked: {html.escape(checked_at)} KST",
        f"Section: {html.escape(section.url)}",
    ]
    if not changed:
        lines.append("🔁 새로운 뉴스 없음 (상단 기사 동일)")
    lines.append("")
    if not articles:
        lines.append("No article titles were extracted. Please check the screenshot.")
    else:
        for index, item in enumerate(articles, start=1):
            title = html.escape(item["title"])
            url = html.escape(item["url"])
            lines.append(f'{index}. <a href="{url}">{title}</a>')
    return "\n".join(lines)


def run_once(sections: list[Section] | None = None, send_header: bool = True,
             advance_rotation: bool = False) -> int:
    OUTPUT_DIR.mkdir(exist_ok=True)
    now_kst = datetime.now(KST)
    checked_at = now_kst.strftime("%Y-%m-%d %H:%M")
    stamp = now_kst.strftime("%Y%m%d_%H%M%S")
    limit = int(os.getenv("MACRO_ARTICLE_LIMIT", "5"))
    detect_n = int(os.getenv("MACRO_DETECT_TOP_N", "2"))
    width = int(os.getenv("MACRO_VIEWPORT_WIDTH", "1440"))
    height = int(os.getenv("MACRO_VIEWPORT_HEIGHT", "1800"))
    headless = os.getenv("MACRO_HEADLESS", "true").lower() != "false"

    state = load_state()
    targets = sections if sections is not None else SECTIONS
    if send_header:
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
            for section in targets:
                page = context.new_page()
                try:
                    prepare_page(page, section)
                    articles = extract_articles(page, section, section.limit or limit)

                    # Change is judged only on the top-N articles (default 2),
                    # even though up to `limit` articles are shown in the message.
                    fingerprint = fingerprint_articles(articles[:detect_n])
                    previous = state.get(section.name, {}).get("fingerprint")
                    # No articles => force a screenshot so the reader can verify.
                    changed = (fingerprint != previous) or not articles

                    if changed:
                        screenshot = screenshot_page(page, section, stamp)
                        caption = f"<b>{html.escape(section.name)}</b>\n{html.escape(checked_at)} KST"
                        send_photo(screenshot, caption)
                        send_message(format_article_message(section, articles, checked_at, changed=True))
                        print(f"Sent {section.name}: {len(articles)} article(s) [changed]", flush=True)
                    else:
                        send_message(format_article_message(section, articles, checked_at, changed=False))
                        print(f"Sent {section.name}: no change (screenshot skipped)", flush=True)

                    state[section.name] = {"fingerprint": fingerprint, "checked_at": checked_at}
                except Exception as exc:
                    message = f"<b>{html.escape(section.name)}</b>\nFailed: {html.escape(str(exc))}"
                    print(message, file=sys.stderr, flush=True)
                    if os.getenv("TELEGRAM_NOTIFY_ERRORS", "false").lower() == "true":
                        send_message(message)
                finally:
                    page.close()
            if advance_rotation:
                state["_rotation"] = (int(state.get("_rotation", 0)) + len(targets)) % len(SECTIONS)
                state["_last_rotate_ts"] = time.time()
            save_state(state)
        finally:
            browser.close()
    return 0


def run_rotation() -> int:
    """10분 간격 크론용: 이번 차례 섹션 1개만 보낸다. 순서는 SECTIONS 정의 순.

    회전 포인터는 변경감지 상태 파일(macro_section_state.json)에 함께 저장되어
    Actions 캐시로 유지된다.

    슬롯 시작 판정은 포인터가 아니라 '직전 회전 실행과의 시간 공백'으로 한다
    (슬롯 간격은 3시간+, 슬롯 안 틱은 10분이라 30분이 확실한 경계).
    새 슬롯이면 포인터를 0으로 되돌려 항상 네이버 경제부터 + 헤더를 보낸다 —
    이전 슬롯에서 크론 틱이 유실돼 포인터가 밀려 있어도 여기서 자동 복구된다.
    """
    state = load_state()
    now_ts = time.time()
    last_ts = float(state.get("_last_rotate_ts", 0))
    new_slot = (now_ts - last_ts) > 1800
    idx = 0 if new_slot else int(state.get("_rotation", 0)) % len(SECTIONS)
    if new_slot and int(state.get("_rotation", 0)) != 0:
        state["_rotation"] = 0
        save_state(state)
    return run_once(sections=[SECTIONS[idx]], send_header=new_slot, advance_rotation=True)


def run_serve_slot() -> int:
    """슬롯당 1회 실행용: 러너 잡 안에서 10분씩 쉬며 6개 섹션을 차례로 발송.

    GitHub 무료 러너는 10분 간격 크론 틱을 대부분 유실/지연시켜(실측: 슬롯당
    6틱 중 1~2틱만 발화, 발화 간격 30~55분) --rotate 방식으로는 10분 간격을
    지킬 수 없다. 대신 슬롯 시작 틱 1개만 받아 잡 안에서 sleep으로 간격을
    보장한다. public 리포는 러너 시간이 무제한이라 비용이 없다.

    같은 슬롯의 폴백 틱(시작 틱 유실 대비)이 겹치면 최근 서빙 시각(90분 이내)
    으로 중복을 걸러낸다 — 슬롯 길이(~55분) < 90분 < 슬롯 간격(3.5시간+).
    """
    state = load_state()
    now_ts = time.time()
    last_ts = float(state.get("_last_serve_ts", 0))
    if (now_ts - last_ts) < 5400:
        print("Slot already served recently; skipping (fallback tick).", flush=True)
        return 0
    state["_last_serve_ts"] = now_ts
    save_state(state)

    interval = int(os.getenv("MACRO_SERVE_INTERVAL_SEC", "600"))
    next_at = time.monotonic()
    for i, section in enumerate(SECTIONS):
        try:
            run_once(sections=[section], send_header=(i == 0), advance_rotation=False)
        except Exception as exc:  # 한 섹션이 죽어도 나머지 섹션은 계속 보낸다
            print(f"Serve-slot section failed ({section.name}): {exc}", file=sys.stderr, flush=True)
        if i < len(SECTIONS) - 1:
            next_at += interval
            delay = next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    return 0


def sleep_until_next_hour() -> None:
    now = time.time()
    time.sleep(3600 - (int(now) % 3600))


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture macro news sections and send them to Telegram.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--loop", action="store_true", help="Run every hour.")
    parser.add_argument("--rotate", action="store_true",
                        help="Send only the next section in rotation (for 10-minute staggered crons).")
    parser.add_argument("--serve-slot", action="store_true",
                        help="Send all sections 10 minutes apart within one job (for one cron tick per slot).")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    loop = args.loop or os.getenv("RUN_MODE", "once").lower() == "loop"
    if args.once:
        loop = False

    if args.serve_slot or os.getenv("RUN_MODE", "").lower() == "serve-slot":
        return run_serve_slot()

    if args.rotate or os.getenv("RUN_MODE", "").lower() == "rotate":
        return run_rotation()

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
