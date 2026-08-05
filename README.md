# 메이플스토리 Discord 공지 봇

메이플스토리 공식 홈페이지의 새 공지 중 `maintenance`, `sale`, `general`, `update`, `events` 카테고리를 Discord 채널에 알려주는 파이썬 봇입니다.

## 봇이 하는 일

1. 5분마다 메이플스토리 공식 뉴스 API에서 새 공지를 확인합니다.
2. 새 공지의 영어 본문을 OpenAI가 3~5줄 영어 요약으로 만듭니다.
3. Google Cloud Translation이 그 짧은 요약을 한국어로 번역합니다.
4. 한국어 요약과 원문 링크를 지정한 Discord 채널에 보냅니다.
5. 공식 공지 목록의 썸네일을 Discord 임베드 이미지로 함께 보냅니다.
5. 이미 보낸 공지는 `state.json`에 기록해 다시 보내지 않습니다.

원문 전체를 번역하지 않고 요약만 번역하므로 번역 API 사용량을 줄일 수 있습니다.

실제 `update` 패치노트에 `Sunny Sunday` 표가 있으면 날짜별 혜택을 모두 추출해 전용 임베드 필드와 배너 이미지로 함께 보냅니다. 날짜는 Discord를 보는 사람의 현지 시간으로 표시됩니다. 반복 혜택은 지정된 한국어 표현을 사용하고, 새로운 보상 문구만 Google 번역으로 처리합니다. 제목이 `Preview`인 업데이트 소개 글에는 이 기능이 동작하지 않습니다.

번역된 Sunny Sunday 일정은 `state.json`에 저장됩니다. 각 일정의 시작 시각에는 공지 채널에 그 주 혜택을 자동으로 보내고, 24시간 뒤 자동 메시지를 삭제합니다. `/썬데이` 명령어는 외부 API를 다시 호출하지 않고 저장된 일정 중 종료되지 않은 항목만 보여줍니다.

Discord에서 `/썬데이`를 입력하면 현재 진행 중이거나 앞으로 열릴 Sunny Sunday 목록을 확인할 수 있습니다.

## HEXA 강화 계산 명령어

Discord에서 `/헥사`를 입력하고 코어 종류, 현재 레벨, 목표 레벨을 선택하면 필요한 솔 에르다와 솔 에르다 조각 합계를 계산합니다.

```text
/헥사 코어종류:강화 코어 현재레벨:7 목표레벨:20
```

지원하는 코어 종류는 `스킬 코어`, `3rd 스킬 코어`, `마스터리 코어`, `강화 코어`, `공용 코어`, `직업군 공용 코어`입니다.
현재 레벨은 0~29, 목표 레벨은 1~30 범위에서 선택할 수 있습니다.

### 개인 Discord 계정에 `/헥사` 설치하기

공지 자동 전송은 기존처럼 지정한 서버 채널에서만 동작하고, `/헥사` 명령어만 개인 계정에 설치해 서버와 DM에서 사용할 수 있습니다.

1. Discord Developer Portal의 `Installation`에서 `사용자 설치`와 `길드 설치`를 모두 켭니다.
2. 비공개 애플리케이션은 `설치 링크`를 `없음`으로 둡니다.
3. `OAuth2`의 URL 생성기에서 `applications.commands`와 `User Install`을 선택합니다.
4. 생성된 링크를 열고 `내 앱에 추가`를 선택합니다.

개인 계정에 설치해도 봇 프로그램은 Oracle 서버에서 계속 실행되어야 합니다.

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

생성된 `.env` 파일을 메모장으로 열고 아래 다섯 값을 실제 값으로 바꿉니다.

```env
DISCORD_TOKEN=Discord_봇_토큰
DISCORD_CHANNEL_ID=알림을_보낼_채널_ID
SUNNY_SUNDAY_CHANNEL_ID=Sunny_Sunday_전용_채널_ID
OPENAI_API_KEY=OpenAI_API_키
GOOGLE_TRANSLATE_API_KEY=Google_번역_API_키
```

`SUNNY_SUNDAY_CHANNEL_ID`에는 전체 Sunny Sunday 일정과 해당 주의 자동 알림을 받을 채널 ID를 입력합니다. 이 값을 비우거나 작성하지 않으면 일반 공지 채널인 `DISCORD_CHANNEL_ID`를 사용합니다. `/썬데이` 명령 결과는 명령어를 실행한 채널에 표시됩니다.

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
