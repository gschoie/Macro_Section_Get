# Colab Macro Section Telegram Bot

Colab can run this bot while the notebook runtime is alive. It is useful for testing or temporary monitoring, but it is not a reliable always-on scheduler because Colab can disconnect idle or long-running sessions.

For a more reliable schedule, use GitHub Actions, Windows Task Scheduler, a small VPS, or another always-on machine.

## 1. Install Playwright in Colab

Run this in the first Colab cell:

```python
!pip -q install playwright
!python -m playwright install --with-deps chromium
```

## 2. Upload the bot file

Upload `macro_section_telegram_bot.py` to Colab, or mount Google Drive and copy it into the current working directory.

Simple upload cell:

```python
from google.colab import files
files.upload()  # choose macro_section_telegram_bot.py
```

## 3. Set Telegram secrets

Run this cell and paste your values when prompted:

```python
import getpass
import os

os.environ["TELEGRAM_BOT_TOKEN"] = getpass.getpass("Telegram bot token: ")
os.environ["TELEGRAM_CHAT_ID"] = getpass.getpass("Telegram chat id: ")

os.environ["RUN_MODE"] = "once"
os.environ["MACRO_ARTICLE_LIMIT"] = "15"
os.environ["MACRO_VIEWPORT_WIDTH"] = "1440"
os.environ["MACRO_VIEWPORT_HEIGHT"] = "1800"
os.environ["MACRO_FULL_PAGE_SCREENSHOT"] = "false"
os.environ["MACRO_HEADLESS"] = "true"
```

## 4. Run one test

```python
!python macro_section_telegram_bot.py --once
```

## 5. Run hourly while Colab stays alive

```python
!python macro_section_telegram_bot.py --loop
```

Stop the cell when you want to end monitoring. If Colab disconnects, run the setup cells again.

## Notes

- Screenshots are saved to `macro_section_captures/` in the Colab runtime.
- If you want screenshots saved persistently, mount Google Drive before running and change `OUTPUT_DIR` in the script, or download the folder after a run.
- Telegram photo captions are short by design; article titles and links are sent as a separate message after each screenshot.
