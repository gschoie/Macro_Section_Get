# Colab Macro Section Telegram Bot

Colab can run this bot while the notebook runtime is alive. It is useful for testing or temporary monitoring, but it is not a reliable always-on scheduler because Colab can disconnect idle or long-running sessions.

For a reliable 1-hour schedule, use GitHub Actions (included), Windows Task Scheduler, a small VPS, or another always-on machine.

## 1. Install Playwright in Colab

```python
!pip -q install playwright
!python -m playwright install --with-deps chromium
```

## 2. Upload the bot file

```python
from google.colab import files
files.upload()  # choose macro_section_telegram_bot.py
```

## 3. Set Telegram secrets

```python
import getpass, os
os.environ["TELEGRAM_BOT_TOKEN"] = getpass.getpass("Telegram bot token: ")
os.environ["TELEGRAM_CHAT_ID"] = getpass.getpass("Telegram chat id: ")
os.environ["RUN_MODE"] = "once"
os.environ["MACRO_ARTICLE_LIMIT"] = "5"
os.environ["MACRO_DETECT_TOP_N"] = "2"
```

## 4. Run one test

```python
!python macro_section_telegram_bot.py --once
```

## 5. Run hourly while Colab stays alive

```python
!python macro_section_telegram_bot.py --loop
```

## Notes

- Screenshots are saved to `macro_section_captures/` in the Colab runtime.
- Change detection state is stored in `macro_section_state.json`. Colab runtimes are ephemeral, so this file is lost when the session ends; every fresh session treats the first run as "changed". Mount Google Drive and keep the file there if you want change detection to persist.
- When a section is unchanged, only the top-5 text list is sent and the screenshot is skipped.
- Naver sections are extracted via the `a.sa_text_title` selector (grid layout); the others use visible left-column extraction.
