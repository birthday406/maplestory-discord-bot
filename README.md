# 메이플스토리 Discord 공지 봇

메이플스토리 공식 홈페이지의 새 공지 중 `maintenance`, `sale`, `general`, `update` 카테고리만 Discord 채널에 알려주는 파이썬 봇입니다. 이벤트(`events`) 공지는 보내지 않습니다.

## 봇이 하는 일

1. 5분마다 메이플스토리 공식 뉴스 API에서 새 공지를 확인합니다.
2. 새 공지의 영어 본문을 OpenAI가 3~5줄 영어 요약으로 만듭니다.
3. Google Cloud Translation이 그 짧은 요약을 한국어로 번역합니다.
4. 한국어 요약과 원문 링크를 지정한 Discord 채널에 보냅니다.
5. 이미 보낸 공지는 `state.json`에 기록해 다시 보내지 않습니다.

원문 전체를 번역하지 않고 요약만 번역하므로 번역 API 사용량을 줄일 수 있습니다.

## 준비물

- Python 3.10 이상
- Discord 봇 토큰과 알림 채널 ID
- OpenAI API 키
- Google Cloud Translation API 키

API 키는 절대로 채팅, Discord, GitHub, 스크린샷에 올리지 마세요.

## 처음 설치하기

PowerShell을 열고 프로젝트 폴더로 이동합니다.

```powershell
cd "D:\Code\파이썬\maplestory-discord-bot"
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

생성된 `.env` 파일을 메모장으로 열고 아래 네 값을 실제 값으로 바꿉니다.

```env
DISCORD_TOKEN=Discord_봇_토큰
DISCORD_CHANNEL_ID=알림을_보낼_채널_ID
OPENAI_API_KEY=OpenAI_API_키
GOOGLE_TRANSLATE_API_KEY=Google_번역_API_키
```

## 실행하기

```powershell
python maple_bot.py
```

처음 실행할 때는 기존 공지를 보내지 않습니다. 그 시점의 공지 목록을 기준으로 저장한 뒤, 이후에 올라오는 새 글만 알립니다.

## 테스트하기

```powershell
python -m unittest discover -v
```

## 파일 설명

- `maple_bot.py`: 봇의 실제 동작 코드
- `.env`: 내 API 키와 Discord 설정을 보관하는 개인 파일. 공유하면 안 됨
- `.env.example`: `.env` 작성 예시. 실제 키를 넣지 않음
- `state.json`: 이미 Discord에 보낸 공지 번호 기록
- `CHANGELOG.md`: 수정한 내용 기록
