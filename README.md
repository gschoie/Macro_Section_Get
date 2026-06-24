# Macro Section Telegram Bot

This bot opens selected macro/economy news sections in Chromium, sends each page screenshot to Telegram, then sends the extracted article titles and links.

## Sections

- Infomax macro: `https://news.einfomax.co.kr/news/articleList.html?sc_section_code=S1N16&view_type=sm`
- Maeil Business index: `https://www.mk.co.kr/news/economy/business-index`
- Hankyung macro: `https://www.hankyung.com/economy/macro`
- Yonhap economy: `https://www.yna.co.kr/economy/all`

Edit `SECTIONS` in `macro_section_telegram_bot.py` if you want to replace these with your own macro collection section URLs.

## Local Run

```powershell
cd .\macro_section_telegram_bot
pip install -r .\requirements.txt
python -m playwright install chromium
python .\macro_section_telegram_bot.py --once
```

For Windows Task Scheduler, run `run_macro_section_bot.ps1` every hour. The wrapper runs one check and exits.

## GitHub Actions

The cloud schedule is defined at the repository root:

```text
.github/workflows/macro-section-bot.yml
```

Add these GitHub repository secrets:

- `MACRO_TELEGRAM_BOT_TOKEN`
- `MACRO_TELEGRAM_CHAT_ID`

Then run `Macro Section Bot` manually once from the Actions tab, or wait for the scheduled run.

## Colab

See `COLAB_MACRO_BOT.md`.

## Options

Set these as environment variables if needed:

- `MACRO_ARTICLE_LIMIT=15`
- `MACRO_VIEWPORT_WIDTH=1440`
- `MACRO_VIEWPORT_HEIGHT=1800`
- `MACRO_FULL_PAGE_SCREENSHOT=false`
- `MACRO_HEADLESS=true`
