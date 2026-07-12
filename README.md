# Macro Section Telegram Bot (Claude 개조판)

거시·경제 뉴스 섹션을 돌면서 텔레그램으로 전송한다. 원본(88.codex)과 달리 **변경 감지**가 붙어 있고, 표시는 상위 5개로 제한된다.

## 동작

실행 시작 시 헤더(`날짜(요일) 시각 업데이트입니다`)를 먼저 보낸다. 이후 각 섹션마다:

1. 상위 5개 기사(제목/링크)를 추출한다. (`MACRO_ARTICLE_LIMIT`, 기본 5)
2. **상위 2개** 기사 URL로 지문(fingerprint)을 만들어 직전 실행값과 비교한다. (`MACRO_DETECT_TOP_N`, 기본 2) 상태는 `macro_section_state.json`에 섹션별로 저장된다.
3. **바뀌었으면** → 페이지 스크린샷 + 상위 5개 리스트 전송.
4. **안 바뀌었으면** → 스크린샷 **생략**, `🔁 새로운 뉴스 없음 (상단 기사 동일)` + 상위 5개 리스트만 텍스트로 전송.
5. 기사 추출이 0건이면 안전하게 스크린샷을 전송한다.

## 섹션

- Infomax macro: `https://news.einfomax.co.kr/news/articleList.html?sc_section_code=S1N16&view_type=sm`
- Maeil Business index: `https://www.mk.co.kr/news/economy/business-index`
- Hankyung macro: `https://www.hankyung.com/economy/macro`
- Yonhap economy: `https://www.yna.co.kr/economy/all`
- Naver economy(헤드): `https://news.naver.com/section/101`
- Naver global economy(글로벌): `https://news.naver.com/breakingnews/section/101/262`

## 기사 추출 방식

- 일반 사이트: 화면에 보이는 **본문(왼쪽) 열**의 링크만 위치 순으로 추출. 오른쪽 사이드바("많이 본 뉴스"/"베스트 클릭")와 숨겨진 링크는 제외.
- **네이버**: 그리드 레이아웃이라 위치 추출이 안 통해서, 섹션별 `list_selector`(`a.sa_text_title`)로 기사 링크를 직접 잡는다.

## 로컬 실행

```powershell
cd .\macro_section_telegram_bot
pip install -r .\requirements.txt
python -m playwright install chromium
python .\macro_section_telegram_bot.py --once
```

Windows 작업 스케줄러에서는 `run_macro_section_bot.ps1`을 매시간 돌리면 된다(1회 실행 후 종료).

## 상태 파일 (중요)

변경 감지는 `macro_section_state.json`이 실행 사이에 **유지되어야** 동작한다.

- 로컬 / 작업 스케줄러 / VPS: 파일이 그대로 남으므로 문제 없음.
- **GitHub Actions**: 매 실행이 새 러너라 이 파일이 사라진다 → actions cache로 유지하도록 워크플로우에 설정돼 있음. 첫 실행은 전부 "변경"으로 잡힌다(정상 재기준화).

## GitHub Actions

`.github/workflows/macro-section-bot.yml`이 매시간 실행한다. repo secrets에 다음을 등록해야 한다:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 옵션 (환경변수)

- `MACRO_ARTICLE_LIMIT=5` — 표시할 상위 기사 수
- `MACRO_DETECT_TOP_N=2` — 변경 판단에 쓰는 상단 기사 수 (표시 개수와 별개)
- `MACRO_MAIN_COLUMN_RATIO=0.66` — 본문 열 판정 폭 비율(오른쪽 사이드바 제외용)
- `MACRO_VIEWPORT_WIDTH=1440` / `MACRO_VIEWPORT_HEIGHT=1800`
- `MACRO_FULL_PAGE_SCREENSHOT=false`
- `MACRO_HEADLESS=true`
- `TELEGRAM_NOTIFY_ERRORS=false`

## Colab

`COLAB_MACRO_BOT.md` 참고.
