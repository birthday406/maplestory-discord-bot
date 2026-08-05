import html
import json
import logging
import os
import re
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from openai import AsyncOpenAI


NEWS_URL = "https://g.nexonstatic.com/maplestory/cms/v1/news"
NEWS_DETAIL_URL = "https://g.nexonstatic.com/maplestory/cms/v1/news/{post_id}"
GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"
SITE_ORIGIN = "https://g.nexonstatic.com"
SITE_URL = "https://www.nexon.com/maplestory/news"
WATCHED_CATEGORIES = {"maintenance", "sale", "general", "update", "events"}
# 이전 state.json에 카테고리 기록이 없을 때 사용하던 기존 감시 목록입니다.
LEGACY_WATCHED_CATEGORIES = {"maintenance", "sale", "general", "update"}
CATEGORY_COLORS = {
    "maintenance": 0xED4245,  # 빨강
    "sale": 0x9B59B6,  # 보라
    "general": 0x3498DB,  # 파랑
    "update": 0xF1C40F,  # 금색
    "events": 0x57F287,  # 초록
}
STATE_PATH = Path("state.json")
POLL_INTERVAL_MINUTES = 5
MODEL = "gpt-5.6-luna"

# 각 튜플의 0번째 값은 0→1, 29번째 값은 29→30 강화 비용입니다.
# 코어별로 첫 번째 튜플은 솔 에르다, 두 번째 튜플은 솔 에르다 조각 비용입니다.
HEXA_CORE_COSTS = {
    "스킬 코어": (
        (5, 1, 1, 1, 2, 2, 2, 3, 3, 10, 3, 3, 4, 4, 4, 4, 4, 4, 5, 15, 5, 5, 5, 5, 5, 6, 6, 6, 7, 20),
        (100, 30, 35, 40, 45, 50, 55, 60, 65, 200, 80, 90, 100, 110, 120, 130, 140, 150, 160, 350, 170, 180, 190, 200, 210, 220, 230, 240, 250, 500),
    ),
    "3rd 스킬 코어": (
        (7, 1, 1, 1, 1, 2, 2, 2, 2, 8, 2, 2, 3, 3, 3, 3, 3, 3, 3, 12, 4, 4, 4, 4, 4, 4, 5, 5, 5, 14),
        (140, 21, 26, 30, 34, 38, 43, 47, 51, 142, 62, 69, 77, 83, 91, 98, 105, 112, 120, 252, 128, 136, 145, 152, 161, 168, 177, 184, 193, 357),
    ),
    "마스터리 코어": (
        (3, 1, 1, 1, 1, 1, 1, 2, 2, 5, 2, 2, 2, 2, 2, 2, 2, 2, 3, 8, 3, 3, 3, 3, 3, 3, 3, 3, 4, 10),
        (50, 15, 18, 20, 23, 25, 28, 30, 33, 100, 40, 45, 50, 55, 60, 65, 70, 75, 80, 175, 85, 90, 95, 100, 105, 110, 115, 120, 125, 250),
    ),
    "강화 코어": (
        (4, 1, 1, 1, 2, 2, 2, 3, 3, 8, 3, 3, 3, 3, 3, 3, 3, 3, 4, 12, 4, 4, 4, 4, 4, 5, 5, 5, 6, 15),
        (75, 23, 27, 30, 34, 38, 42, 45, 49, 150, 60, 68, 75, 83, 90, 98, 105, 113, 120, 263, 128, 135, 143, 150, 158, 165, 173, 180, 188, 375),
    ),
    "공용 코어": (
        (7, 2, 2, 2, 3, 3, 3, 5, 5, 14, 5, 5, 6, 6, 6, 6, 6, 6, 7, 17, 7, 7, 7, 7, 7, 9, 9, 9, 10, 20),
        (125, 38, 44, 50, 57, 63, 69, 75, 82, 300, 110, 124, 138, 152, 165, 179, 193, 207, 220, 525, 234, 248, 262, 275, 289, 303, 317, 330, 344, 750),
    ),
    "직업군 공용 코어": (
        (4, 1, 1, 1, 2, 2, 2, 3, 3, 9, 3, 3, 3, 3, 4, 4, 4, 4, 4, 14, 4, 5, 5, 5, 5, 5, 5, 5, 6, 18),
        (90, 25, 30, 35, 40, 45, 50, 55, 60, 180, 73, 81, 90, 98, 107, 115, 124, 132, 141, 315, 151, 160, 170, 179, 189, 198, 208, 217, 227, 450),
    ),
}


def watched_posts(posts: list[dict]) -> list[dict]:
    # 메이플 공식 API가 준 모든 글에서, 봇이 알릴 카테고리만 남깁니다.
    return [post for post in posts if post.get("category") in WATCHED_CATEGORIES]


def post_url(post: dict) -> str:
    # Discord 알림을 클릭했을 때 원문 공지로 이동할 수 있도록 주소를 만듭니다.
    title_slug = "-".join(
        "".join(char if char.isalnum() else " " for char in post["name"]).lower().split()
    )
    return f"{SITE_URL}/{post['category']}/{post['id']}/{title_slug}"


def thumbnail_url(post: dict) -> str:
    # 공식 목록 API의 상대 썸네일 경로를 Discord가 읽을 수 있는 전체 주소로 바꿉니다.
    return f"{SITE_ORIGIN}{post['imageThumbnail']}"


def html_to_text(source: str) -> str:
    # 공지 본문은 HTML입니다. AI에게 읽기 쉬운 일반 텍스트만 전달합니다.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", source, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def calculate_hexa_cost(core_type: str, current_level: int, target_level: int) -> tuple[int, int]:
    # 배열 인덱스가 강화 시작 레벨과 같으므로 현재 레벨부터 목표 레벨 직전까지 더합니다.
    if not 0 <= current_level < target_level <= 30:
        raise ValueError("현재 레벨은 목표 레벨보다 낮아야 하며 레벨 범위는 0~30입니다.")

    sol_erda_costs, fragment_costs = HEXA_CORE_COSTS[core_type]
    return (
        sum(sol_erda_costs[current_level:target_level]),
        sum(fragment_costs[current_level:target_level]),
    )


@app_commands.command(name="헥사", description="HEXA 코어 강화에 필요한 재료를 계산합니다.")
@app_commands.describe(
    core_type="계산할 HEXA 코어 종류",
    current_level="현재 코어 레벨 (0~29)",
    target_level="목표 코어 레벨 (1~30)",
)
@app_commands.rename(
    core_type="코어종류",
    current_level="현재레벨",
    target_level="목표레벨",
)
@app_commands.choices(
    core_type=[
        app_commands.Choice(name=core_name, value=core_name)
        for core_name in HEXA_CORE_COSTS
    ]
)
async def hexa_command(
    interaction: discord.Interaction,
    core_type: app_commands.Choice[str],
    current_level: app_commands.Range[int, 0, 29],
    target_level: app_commands.Range[int, 1, 30],
) -> None:
    # 목표 레벨이 더 높지 않으면 계산할 강화 구간이 없으므로 사용자에게만 오류를 보여 줍니다.
    if current_level >= target_level:
        await interaction.response.send_message(
            "목표 레벨은 현재 레벨보다 높아야 합니다.", ephemeral=True
        )
        return

    sol_erda, fragments = calculate_hexa_cost(
        core_type.value, current_level, target_level
    )
    embed = discord.Embed(
        title="HEXA 매트릭스 강화 계산",
        description=(
            f"**{core_type.name}**\n"
            f"◆ **{current_level} → {target_level}** 강화 비용\n\n"
            f"솔 에르다　**{sol_erda:,}개**\n"
            f"솔 에르다 조각　**{fragments:,}개**"
        ),
        color=0x3498DB,
    )
    await interaction.response.send_message(embed=embed)


def load_state() -> tuple[set[int] | None, set[str]]:
    # 이전 실행에서 이미 알린 공지 번호를 불러와 같은 글을 다시 보내지 않습니다.
    if not STATE_PATH.exists():
        return None, set()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return (
        set(state["sent_ids"]),
        set(state.get("watched_categories", LEGACY_WATCHED_CATEGORIES)),
    )


def save_state(sent_ids: set[int], watched_categories: set[str]) -> None:
    # 봇을 껐다 켜도 중복 알림을 막을 수 있도록 공지 번호를 파일에 저장합니다.
    STATE_PATH.write_text(
        json.dumps(
            {
                "sent_ids": sorted(sent_ids)[-500:],
                "watched_categories": sorted(watched_categories),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class MapleNewsBot(commands.Bot):
    def __init__(self, channel_id: int) -> None:
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.channel_id = channel_id
        self.sent_ids, self.saved_categories = load_state()
        self.session: aiohttp.ClientSession | None = None
        # OpenAI 키는 코드에 적지 않고 .env 파일에서만 읽습니다.
        self.openai = AsyncOpenAI()
        # Google 번역 키도 .env 파일에서 읽습니다. 키를 Discord나 GitHub에 올리면 안 됩니다.
        self.google_api_key = os.environ["GOOGLE_TRANSLATE_API_KEY"]

    async def setup_hook(self) -> None:
        # Discord 연결이 준비되면 5분마다 새 공지를 확인하는 작업을 시작합니다.
        self.session = aiohttp.ClientSession()
        # 전역 슬래시 명령을 Discord에 등록합니다. 명령 내용이 바뀌어도 재시작 시 동기화됩니다.
        self.tree.add_command(hexa_command)
        await self.tree.sync()

    async def on_ready(self) -> None:
        # 디스코드 연결이 끝난 뒤에만 첫 공지 확인을 시작합니다.
        # 재연결되더라도 같은 확인 작업을 중복으로 시작하지 않습니다.
        if not self.check_news.is_running():
            self.check_news.start()

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
        await super().close()

    async def fetch_posts(self) -> list[dict]:
        # 메이플스토리 공식 뉴스 목록 API에서 최신 공지를 가져옵니다.
        assert self.session is not None
        async with self.session.get(NEWS_URL, timeout=aiohttp.ClientTimeout(total=20)) as response:
            response.raise_for_status()
            return watched_posts(await response.json())

    async def fetch_post_detail(self, post_id: int) -> dict:
        # 목록에는 본문이 없으므로, 새 공지의 본문을 별도로 가져옵니다.
        assert self.session is not None
        async with self.session.get(
            NEWS_DETAIL_URL.format(post_id=post_id), timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def summarize(self, post: dict) -> str:
        # 먼저 OpenAI가 영어 원문을 짧은 영어 요약으로 줄입니다.
        # 원문 전체를 번역기에 보내지 않아 번역 API 사용량을 줄이는 방식입니다.
        response = await self.openai.responses.create(
            model=MODEL,
            instructions=(
                "Summarize MapleStory announcements in English. "
                "Return a concise 3-5 bullet summary. Do not add facts that are not in the source."
            ),
            input=f"Title: {post['name']}\n\nBody:\n{html_to_text(post['body'])}",
        )
        return response.output_text

    async def translate_summary(self, text: str) -> str:
        # OpenAI가 만든 짧은 영어 요약만 Google 번역으로 한국어 변환합니다.
        assert self.session is not None
        async with self.session.post(
            GOOGLE_TRANSLATE_URL,
            headers={"X-Goog-Api-Key": self.google_api_key},
            json={"q": text, "source": "en", "target": "ko", "format": "text"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
            return html.unescape(payload["data"]["translations"][0]["translatedText"])

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def check_news(self) -> None:
        # 이 함수는 5분마다 자동 실행되는 봇의 핵심 작업입니다.
        posts = await self.fetch_posts()
        current_ids = {post["id"] for post in posts}

        if self.sent_ids is None:
            # 첫 실행에는 과거 공지를 한꺼번에 보내지 않고, 현재 글을 기준점으로만 저장합니다.
            self.sent_ids = current_ids
            self.saved_categories = set(WATCHED_CATEGORIES)
            save_state(self.sent_ids, self.saved_categories)
            print("Initial news state saved; no existing posts were sent.")
            return

        new_categories = WATCHED_CATEGORIES - self.saved_categories
        if new_categories:
            # 새로 켠 카테고리의 과거 글은 기준점으로만 저장해 채널 도배를 막습니다.
            self.sent_ids.update(
                post["id"] for post in posts if post["category"] in new_categories
            )
            self.saved_categories.update(new_categories)
            save_state(self.sent_ids, self.saved_categories)

        new_posts = [post for post in posts if post["id"] not in self.sent_ids]
        if not new_posts:
            logging.info("No new MapleStory announcements found.")
            return

        channel = self.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"DISCORD_CHANNEL_ID {self.channel_id} is not an accessible text channel")

        for post in sorted(new_posts, key=lambda item: item["liveDate"]):
            # 새 공지 한 건의 본문을 가져와 요약하고, 그 요약을 한국어로 번역합니다.
            detail = await self.fetch_post_detail(post["id"])
            korean_summary = await self.translate_summary(await self.summarize(detail))
            embed = discord.Embed(
                title=post["name"],
                description=korean_summary[:4_096],
                url=post_url(post),
                timestamp=discord.utils.parse_time(post["liveDate"]),
                # 카테고리마다 다른 색을 써서 공지 성격을 한눈에 구분합니다.
                color=CATEGORY_COLORS[post["category"]],
            )
            # Discord 임베드 왼쪽 위에 표시되는 작은 출처/카테고리 라벨입니다.
            embed.set_author(name=f"MapleStory | {post['category'].upper()}")
            # 공식 홈페이지 카드에 쓰인 썸네일을 임베드 하단의 큰 이미지로 보여 줍니다.
            embed.set_image(url=thumbnail_url(post))
            await channel.send(embed=embed)
            # Discord 전송에 성공한 뒤에만 '이미 보냄' 목록에 기록합니다.
            self.sent_ids.add(post["id"])
            save_state(self.sent_ids, self.saved_categories)
            logging.info("Sent announcement %s to Discord.", post["id"])

    @check_news.error
    async def check_news_error(self, error: Exception) -> None:
        # API나 전송 단계의 오류를 서버 로그에 남겨 원인을 확인할 수 있게 합니다.
        logging.exception("MapleStory announcement check failed.", exc_info=error)

    @check_news.before_loop
    async def before_check_news(self) -> None:
        # 디스코드 기본 연결 대기 함수를 가리지 않도록 다른 이름을 사용합니다.
        await self.wait_until_ready()


def main() -> None:
    # .env 파일을 읽은 뒤 Discord 봇을 실행합니다.
    load_dotenv()
    MapleNewsBot(int(os.environ["DISCORD_CHANNEL_ID"])).run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
