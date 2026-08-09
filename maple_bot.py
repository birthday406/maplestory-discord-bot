import html
import json
import logging
import os
import re
from datetime import datetime, timezone
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
SUNNY_SUNDAY_IMAGE_PATH = Path(__file__).parent / "assets" / "title-sunny-sunday.webp"
POLL_INTERVAL_MINUTES = 5
SUNNY_SUNDAY_DURATION_SECONDS = 24 * 60 * 60
MODEL = "gpt-5.6-luna"
ALERT_NEWS = "news"
ALERT_SUNNY_DAY = "sunny_day"
ALERT_SUNNY_LIST = "sunny_list"
ALERT_TYPES = (ALERT_NEWS, ALERT_SUNNY_DAY, ALERT_SUNNY_LIST)

# Discord 애플리케이션에 등록한 HEXA 계산기용 일반 이모지입니다.
HEXA_EMOJI = "<:HEXA:1534436226751529031>"
SOL_ERDA_EMOJI = "<:SolErda:1534436216139944108>"
FRAGMENT_EMOJI = "<:Fragment:1534436205796790324>"
ANIMATED_TWINKLE_EMOJI = "<a:Animated_Twinkle:1534436193276792873>"

# 써니 선데이에서 자주 반복되는 영문은 사용자가 지정한 표현으로 고정 번역합니다.
# 값이 빈 문자열인 주문의 흔적 항목은 Discord 안내에서 제외합니다.
SUNNY_SUNDAY_TRANSLATIONS = (
    ("monster park clear exp", "몬스터 파크 클리어 경험치 250% 증가 (익몬 제외)"),
    ("reduced chance of item destruction", "21성 이하에서 스타포스 강화 시 파괴 확률 30% 감소"),
    ("off star force enhancements", "스타포스 강화 비용 30% 할인"),
    ("elite monster appearance increase", "앨리트 몬스터 증가 (1마리 → 3마리)"),
    ("[hexa matrix]", "[헥사 매트릭스] 헥사 스탯의 메인 레벨 5이상 강화 확률 20% 증가"),
    ("off ability resets", "어빌리티 재설정 비용 50% 할인"),
    ("1+1 star force", "10성 이하에서 스타포스 강화시 1+1"),
    ("extra star force when enhancing", "10성 이하에서 스타포스 강화시 1+1"),
    ("treasure hunter exp", "트레져 헌터 경험치 3배"),
    ("sol erda when hunting", "사냥을 통해 획득할 수 있는 솔 에르다 2배 증가"),
    ("rune appearance cooldown reduction", "룬 재등장 및 재사용 대기시간 감소 (15분 → 10분)"),
    ("combo kill exp", "콤보킬 경험치 획득량 300% 증가"),
    ("rune exp buff effect", "룬 경험치 버프 효과 100% 증가"),
    ("magnificent soul", "소울 조각 사용 시 위대한 소울 획득 확률 5배"),
    ("chance to register a new monster", "몬스터 컬렉션 신규 몬스터 등록 확률 추가 100%"),
    ("mysterious monsterbloom", "의문의 모몽 (x3): 교환불가, 영구"),
    ("off spell trace enhancements", ""),
)

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


def normalize_alert_channels(
    stored_channels: dict | None,
    news_channel_id: int,
    sunny_channel_id: int,
) -> dict[str, set[int]]:
    # 예전 환경변수 채널은 설정 데이터가 없는 최초 한 번만 새 알리미 구조로 옮깁니다.
    if stored_channels is None:
        return {
            ALERT_NEWS: {news_channel_id},
            ALERT_SUNNY_DAY: {sunny_channel_id},
            ALERT_SUNNY_LIST: {sunny_channel_id},
        }
    return {
        alert_type: {
            int(channel_id) for channel_id in stored_channels.get(alert_type, [])
        }
        for alert_type in ALERT_TYPES
    }


def update_alert_channel(
    alert_channels: dict[str, set[int]],
    alert_type: str,
    channel_id: int,
    enabled: bool,
) -> bool:
    # 같은 설정을 반복해도 state.json과 Discord 메시지가 중복 변경되지 않게 합니다.
    channels = alert_channels[alert_type]
    if enabled:
        if channel_id in channels:
            return False
        channels.add(channel_id)
        return True
    if channel_id not in channels:
        return False
    channels.remove(channel_id)
    return True


def migrate_sunny_sunday_state(schedule: dict | None, channel_id: int) -> bool:
    # 단일 채널 시절의 message_id를 채널별 message_ids 구조로 한 번 변환합니다.
    if schedule is None:
        return False
    changed = schedule.pop("announcement_channel_id", None) is not None
    for entry in schedule["entries"]:
        if "message_ids" not in entry:
            entry["message_ids"] = {}
            changed = True
        old_message_id = entry.pop("message_id", None)
        if old_message_id is not None:
            entry["message_ids"][str(channel_id)] = old_message_id
            changed = True
    return changed


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


def is_patch_notes(post: dict) -> bool:
    # update 카테고리라도 Preview나 단독 콘텐츠 소개 글은 제외하고 실제 패치노트만 찾습니다.
    title = post.get("name", "").lower()
    return post.get("category") == "update" and "patch notes" in title and "preview" not in title


def extract_sunny_sunday(source: str) -> list[tuple[str, bool, list[str]]]:
    # 공식 패치노트의 SunnySunday 앵커 다음 표에서 날짜와 혜택 목록만 꺼냅니다.
    section = re.search(
        r'id=["\']SunnySunday["\'].*?(<table\b.*?</table>)',
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if section is None:
        return []

    entries = []
    for row in re.findall(r"<tr\b.*?</tr>", section.group(1), flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(
            r"<td\b[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL
        )
        if len(cells) < 2:
            continue

        date = html_to_text(cells[0])
        perks = [
            html_to_text(perk)
            for perk in re.findall(
                r"<li\b[^>]*>(.*?)</li>", cells[1], flags=re.IGNORECASE | re.DOTALL
            )
        ]
        entries.append(
            (date, "special sunny sunday" in html_to_text(cells[1]).lower(), perks)
        )
    return entries


def known_sunny_sunday_translation(perk: str) -> str | None:
    # 대소문자, 공백, 곱하기 기호가 달라도 같은 고정 번역을 찾을 수 있게 정규화합니다.
    normalized = re.sub(r"\s+", " ", perk).strip().lower().replace("×", "x")
    for phrase, translation in SUNNY_SUNDAY_TRANSLATIONS:
        if phrase in normalized:
            return translation
    return None


def sunny_sunday_timestamp(date: str) -> int:
    # 공식 표의 날짜는 UTC 자정 기준이므로 저장과 자동 알림에 사용할 Unix 시간으로 바꿉니다.
    moment = datetime.strptime(date, "%B %d, %Y").replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def format_sunny_sunday_date(date: str) -> str:
    # Discord 시간 태그는 각 사용자의 현지 시간과 상대 시간으로 자동 표시됩니다.
    timestamp = sunny_sunday_timestamp(date)
    return f"<t:{timestamp}:F> (<t:{timestamp}:R>)"


def visible_sunny_sunday_entries(
    entries: list[dict], now_timestamp: int | None = None
) -> list[dict]:
    # 시작 시각에서 24시간이 지나지 않은 현재 및 미래 일정만 목록에 남깁니다.
    if now_timestamp is None:
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
    return [
        entry
        for entry in entries
        if now_timestamp < entry["timestamp"] + SUNNY_SUNDAY_DURATION_SECONDS
    ]


def current_sunny_sunday_entry(
    entries: list[dict], now_timestamp: int | None = None
) -> dict | None:
    # 진행 중인 일정이 있으면 그 일정을, 없으면 가장 가까운 다음 일정 한 건을 고릅니다.
    visible_entries = visible_sunny_sunday_entries(entries, now_timestamp)
    return min(visible_entries, key=lambda entry: entry["timestamp"], default=None)


def sunny_sunday_entry_action(
    entry: dict, channel_id: int, now_timestamp: int
) -> str | None:
    # 주간 메시지를 보낼지, 24시간이 지나 삭제할지를 시간과 저장된 메시지 ID로 결정합니다.
    start = entry["timestamp"]
    end = start + SUNNY_SUNDAY_DURATION_SECONDS
    has_message = str(channel_id) in entry.get("message_ids", {})
    if not has_message and start <= now_timestamp < end:
        return "send"
    if has_message and now_timestamp >= end:
        return "delete"
    return None


def build_sunny_sunday_embed(
    title: str, url: str, entries: list[dict]
) -> discord.Embed:
    # 전체 일정과 주간 자동 알림, /썬데이 명령어가 같은 모양을 사용합니다.
    embed = discord.Embed(
        title=title,
        url=url,
        color=CATEGORY_COLORS["update"],
    )
    embed.set_author(name="MapleStory | SUNNY SUNDAY")
    for entry in entries:
        embed.add_field(name=entry["name"], value=entry["value"], inline=False)
    embed.set_image(url=f"attachment://{SUNNY_SUNDAY_IMAGE_PATH.name}")
    return embed


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
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
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
        title=f"{HEXA_EMOJI} HEXA 매트릭스 강화 계산",
        description=(
            f"**{core_type.name}**\n"
            f"◆ **{current_level} → {target_level}** 강화 비용\n\n"
            f"{SOL_ERDA_EMOJI} 솔 에르다　**{sol_erda:,}개**\n"
            f"{FRAGMENT_EMOJI} 솔 에르다 조각　**{fragments:,}개**"
        ),
        color=0x3498DB,
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="썬데이", description="이번 주 Sunny Sunday 일정을 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def sunny_sunday_command(interaction: discord.Interaction) -> None:
    # 패치노트를 다시 요청하지 않고 봇이 state.json에서 불러온 일정만 사용합니다.
    schedule = getattr(interaction.client, "sunny_sunday", None)
    if schedule is None:
        await interaction.response.send_message(
            "저장된 Sunny Sunday 일정이 없습니다.", ephemeral=True
        )
        return

    entry = current_sunny_sunday_entry(schedule["entries"])
    if entry is None:
        await interaction.response.send_message(
            "남아 있는 Sunny Sunday 일정이 없습니다.", ephemeral=True
        )
        return

    embed = build_sunny_sunday_embed(
        f"☀️ 이번 주 Sunny Sunday ☀️", schedule["url"], [entry]
    )
    await interaction.response.send_message(
        embed=embed, file=discord.File(SUNNY_SUNDAY_IMAGE_PATH)
    )


@app_commands.command(name="썬데이목록", description="저장된 Sunny Sunday 전체 일정을 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def sunny_sunday_list_command(interaction: discord.Interaction) -> None:
    # 이미 번역해 저장한 최신 패치노트의 전체 목록을 API 호출 없이 보여 줍니다.
    schedule = getattr(interaction.client, "sunny_sunday", None)
    if schedule is None:
        await interaction.response.send_message(
            "저장된 Sunny Sunday 일정이 없습니다.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=build_sunny_sunday_embed(
            f"☀️ {schedule['title']} ☀️", schedule["url"], schedule["entries"]
        ),
        file=discord.File(SUNNY_SUNDAY_IMAGE_PATH),
    )


ALERT_ACTION_CHOICES = [
    app_commands.Choice(name="ON", value="on"),
    app_commands.Choice(name="OFF", value="off"),
]


async def run_alert_setting_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
    alert_type: str,
    alert_name: str,
) -> None:
    # 세 설정 명령은 표시 이름만 다르고 권한 검사와 저장 동작은 함께 사용합니다.
    await interaction.client.configure_alert_channel(
        interaction, channel, action.value == "on", alert_type, alert_name
    )


@app_commands.command(name="공지알림", description="번역 공지 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="알림을 받을 텍스트 채널", action="알림 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def news_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    await run_alert_setting_command(
        interaction, channel, action, ALERT_NEWS, "공지 알림"
    )


@app_commands.command(name="썬데이알림", description="당일 Sunny Sunday 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="알림을 받을 텍스트 채널", action="알림 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def sunny_day_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    await run_alert_setting_command(
        interaction, channel, action, ALERT_SUNNY_DAY, "썬데이 당일 알림"
    )


@app_commands.command(name="썬데이목록알림", description="전체 Sunny Sunday 목록 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="알림을 받을 텍스트 채널", action="알림 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def sunny_list_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    await run_alert_setting_command(
        interaction, channel, action, ALERT_SUNNY_LIST, "썬데이 목록 알림"
    )


def load_state() -> tuple[set[int] | None, set[str], dict | None, dict | None]:
    # 이전 실행에서 이미 알린 공지 번호를 불러와 같은 글을 다시 보내지 않습니다.
    if not STATE_PATH.exists():
        return None, set(), None, None
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    stored_sent_ids = state.get("sent_ids")
    return (
        None if stored_sent_ids is None else set(stored_sent_ids),
        set(state.get("watched_categories", LEGACY_WATCHED_CATEGORIES)),
        state.get("sunny_sunday"),
        state.get("alert_channels"),
    )


def save_state(
    sent_ids: set[int] | None,
    watched_categories: set[str],
    sunny_sunday: dict | None,
    alert_channels: dict[str, set[int]],
) -> None:
    # 봇을 껐다 켜도 중복 알림을 막을 수 있도록 공지 번호를 파일에 저장합니다.
    STATE_PATH.write_text(
        json.dumps(
            {
                "sent_ids": None if sent_ids is None else sorted(sent_ids)[-500:],
                "watched_categories": sorted(watched_categories),
                "sunny_sunday": sunny_sunday,
                "alert_channels": {
                    alert_type: sorted(channel_ids)
                    for alert_type, channel_ids in alert_channels.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class MapleNewsBot(commands.Bot):
    def __init__(self, channel_id: int) -> None:
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        stored_sunny_channel_id = os.getenv("SUNNY_SUNDAY_CHANNEL_ID")
        sunny_channel_id = (
            int(stored_sunny_channel_id) if stored_sunny_channel_id else channel_id
        )
        (
            self.sent_ids,
            self.saved_categories,
            self.sunny_sunday,
            stored_alert_channels,
        ) = load_state()
        self.alert_channels = normalize_alert_channels(
            stored_alert_channels, channel_id, sunny_channel_id
        )
        migrate_sunny_sunday_state(self.sunny_sunday, sunny_channel_id)
        self.session: aiohttp.ClientSession | None = None
        # OpenAI 키는 코드에 적지 않고 .env 파일에서만 읽습니다.
        self.openai = AsyncOpenAI()
        # Google 번역 키도 .env 파일에서 읽습니다. 키를 Discord나 GitHub에 올리면 안 됩니다.
        self.google_api_key = os.environ["GOOGLE_TRANSLATE_API_KEY"]

    async def setup_hook(self) -> None:
        # Discord 연결이 준비되면 5분마다 새 공지를 확인하는 작업을 시작합니다.
        self.session = aiohttp.ClientSession()
        # 전역 슬래시 명령을 Discord에 등록합니다. 명령 내용이 바뀌어도 재시작 시 동기화됩니다.
        for command in (
            hexa_command,
            sunny_sunday_command,
            sunny_sunday_list_command,
            news_alert_command,
            sunny_day_alert_command,
            sunny_list_alert_command,
        ):
            self.tree.add_command(command)
        await self.tree.sync()
        self.persist_state()

    def persist_state(self) -> None:
        save_state(
            self.sent_ids,
            self.saved_categories,
            self.sunny_sunday,
            self.alert_channels,
        )

    async def on_ready(self) -> None:
        # 디스코드 연결이 끝난 뒤에만 첫 공지 확인을 시작합니다.
        # 재연결되더라도 같은 확인 작업을 중복으로 시작하지 않습니다.
        if not self.check_news.is_running():
            self.check_news.start()
        if not self.check_sunny_sunday.is_running():
            self.check_sunny_sunday.start()

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

    async def translate_texts(self, texts: list[str]) -> list[str]:
        # 여러 써니 선데이 문구도 한 요청으로 보내 번역 API 호출 횟수를 줄입니다.
        if not texts:
            return []
        assert self.session is not None
        async with self.session.post(
            GOOGLE_TRANSLATE_URL,
            headers={"X-Goog-Api-Key": self.google_api_key},
            json={"q": texts, "source": "en", "target": "ko", "format": "text"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
            return [
                html.unescape(translation["translatedText"])
                for translation in payload["data"]["translations"]
            ]

    async def translate_sunny_sunday(
        self, entries: list[tuple[str, bool, list[str]]]
    ) -> list[dict]:
        # 고정 번역이 없는 혜택만 Google 번역으로 한꺼번에 처리합니다.
        unknown_perks = [
            perk
            for _, _, perks in entries
            for perk in perks
            if known_sunny_sunday_translation(perk) is None
        ]
        google_translations = dict(
            zip(unknown_perks, await self.translate_texts(unknown_perks))
        )

        stored_entries = []
        for date, is_special, perks in entries:
            lines = []
            if is_special:
                lines.append(
                    f"{ANIMATED_TWINKLE_EMOJI} **스페셜: 샤이닝 스타포스** "
                    f"{ANIMATED_TWINKLE_EMOJI}"
                )
            for perk in perks:
                translation = known_sunny_sunday_translation(perk)
                if translation is None:
                    translation = google_translations[perk]
                if translation:
                    lines.append(f"- {translation}")
            stored_entries.append(
                {
                    "timestamp": sunny_sunday_timestamp(date),
                    "name": f"· __{format_sunny_sunday_date(date)}__",
                    "value": "\n".join(lines),
                    "message_ids": {},
                }
            )
        return stored_entries

    async def create_sunny_sunday_schedule(
        self, post: dict, detail: dict | None = None
    ) -> dict | None:
        # 새 패치노트에서 한 번만 추출·번역한 결과를 state.json에 저장할 형태로 만듭니다.
        if detail is None:
            detail = await self.fetch_post_detail(post["id"])
        entries = extract_sunny_sunday(detail["body"])
        if not entries:
            return None

        patch_title = re.sub(r"^\[[^]]+\]\s*", "", post["name"])
        patch_title = re.sub(
            r"\s+Patch Notes$", "", patch_title, flags=re.IGNORECASE
        )
        return {
            "post_id": post["id"],
            "title": patch_title,
            "url": post_url(post),
            "entries": await self.translate_sunny_sunday(entries),
        }

    def alert_text_channels(self, alert_type: str) -> list[discord.TextChannel]:
        # 삭제되었거나 봇이 볼 수 없는 채널은 건너뛰고 서버 로그에 남깁니다.
        channels = []
        for channel_id in sorted(self.alert_channels[alert_type]):
            channel = self.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                channels.append(channel)
            else:
                logging.warning("Alert channel %s is not accessible.", channel_id)
        return channels

    async def send_sunny_sunday_to_channel(
        self, channel: discord.TextChannel, title: str, entries: list[dict]
    ) -> discord.Message:
        assert self.sunny_sunday is not None
        return await channel.send(
            embed=build_sunny_sunday_embed(
                title, self.sunny_sunday["url"], entries
            ),
            file=discord.File(SUNNY_SUNDAY_IMAGE_PATH),
        )

    async def send_alert_embed(
        self, alert_type: str, embed: discord.Embed, attach_sunny_image: bool = False
    ) -> dict[int, int]:
        # 한 채널의 권한 오류가 다른 채널 전송과 다음 공지 처리를 막지 않게 합니다.
        sent_message_ids = {}
        for channel in self.alert_text_channels(alert_type):
            try:
                file = (
                    discord.File(SUNNY_SUNDAY_IMAGE_PATH)
                    if attach_sunny_image
                    else None
                )
                message = await channel.send(embed=embed, file=file)
            except discord.HTTPException:
                logging.exception("Failed to send alert to channel %s.", channel.id)
            else:
                sent_message_ids[channel.id] = message.id
        return sent_message_ids

    async def delete_sunny_day_message(self, channel: discord.TextChannel) -> None:
        # 당일 알림을 끄면 그 채널에 남아 있는 임시 주간 메시지도 함께 제거합니다.
        if self.sunny_sunday is None:
            return
        for entry in self.sunny_sunday["entries"]:
            message_id = entry["message_ids"].get(str(channel.id))
            if message_id is None:
                continue
            try:
                await channel.get_partial_message(message_id).delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                logging.exception(
                    "Failed to delete Sunny Sunday message %s.", message_id
                )
                continue
            del entry["message_ids"][str(channel.id)]

    async def configure_alert_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        enabled: bool,
        alert_type: str,
        alert_name: str,
    ) -> None:
        # Discord 명령 표시 권한과 별개로 실행 순간의 실제 관리자 권한도 검사합니다.
        if interaction.guild is None or not interaction.permissions.administrator:
            await interaction.response.send_message(
                "이 설정은 서버 관리자만 변경할 수 있습니다.", ephemeral=True
            )
            return
        if channel.guild.id != interaction.guild.id:
            await interaction.response.send_message(
                "현재 서버의 텍스트 채널만 선택할 수 있습니다.", ephemeral=True
            )
            return

        already_enabled = channel.id in self.alert_channels[alert_type]
        if enabled == already_enabled:
            state = "이미 켜져" if enabled else "이미 꺼져"
            await interaction.response.send_message(
                f"{channel.mention}의 {alert_name}이 {state} 있습니다.", ephemeral=True
            )
            return

        if enabled:
            bot_member = channel.guild.me
            permissions = channel.permissions_for(bot_member) if bot_member else None
            needs_attachment = alert_type in {ALERT_SUNNY_DAY, ALERT_SUNNY_LIST}
            if permissions is None or not (
                permissions.view_channel
                and permissions.send_messages
                and permissions.embed_links
                and (permissions.attach_files or not needs_attachment)
            ):
                await interaction.response.send_message(
                    "선택한 채널에서 봇의 채널 보기·메시지 보내기·링크 첨부"
                    + ("·파일 첨부" if needs_attachment else "")
                    + " 권한을 확인해주세요.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        if enabled and self.sunny_sunday is not None:
            try:
                if alert_type == ALERT_SUNNY_LIST:
                    await self.send_sunny_sunday_to_channel(
                        channel,
                        f"☀️ {self.sunny_sunday['title']} ☀️",
                        self.sunny_sunday["entries"],
                    )
                elif alert_type == ALERT_SUNNY_DAY:
                    now_timestamp = int(datetime.now(timezone.utc).timestamp())
                    entry = current_sunny_sunday_entry(
                        self.sunny_sunday["entries"], now_timestamp
                    )
                    if (
                        entry is not None
                        and entry["timestamp"] <= now_timestamp
                        < entry["timestamp"] + SUNNY_SUNDAY_DURATION_SECONDS
                    ):
                        message = await self.send_sunny_sunday_to_channel(
                            channel, "☀️ 이번 주 Sunny Sunday ☀️", [entry]
                        )
                        entry["message_ids"][str(channel.id)] = message.id
            except discord.HTTPException:
                await interaction.followup.send(
                    "선택한 채널에 테스트 알림을 보내지 못했습니다.", ephemeral=True
                )
                return
        elif not enabled and alert_type == ALERT_SUNNY_DAY:
            await self.delete_sunny_day_message(channel)

        update_alert_channel(self.alert_channels, alert_type, channel.id, enabled)
        self.persist_state()
        await interaction.followup.send(
            f"{channel.mention}의 {alert_name}을 {'켰습니다' if enabled else '껐습니다'}.",
            ephemeral=True,
        )

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def check_news(self) -> None:
        # 이 함수는 5분마다 자동 실행되는 봇의 핵심 작업입니다.
        posts = await self.fetch_posts()
        current_ids = {post["id"] for post in posts}

        if self.sunny_sunday is None:
            # 기존 state.json에는 일정이 없으므로, 이미 처리한 최신 패치노트에서 최초 한 번만 채웁니다.
            latest_patch = next((post for post in posts if is_patch_notes(post)), None)
            should_bootstrap = latest_patch is not None and (
                self.sent_ids is None or latest_patch["id"] in self.sent_ids
            )
            if should_bootstrap:
                schedule = await self.create_sunny_sunday_schedule(latest_patch)
                if schedule is not None:
                    await self.send_alert_embed(
                        ALERT_SUNNY_LIST,
                        build_sunny_sunday_embed(
                            f"☀️ {schedule['title']} ☀️",
                            schedule["url"],
                            schedule["entries"],
                        ),
                        attach_sunny_image=True,
                    )
                    self.sunny_sunday = schedule
                    if self.sent_ids is not None:
                        self.persist_state()

        if self.sent_ids is None:
            # 첫 실행에는 과거 공지를 한꺼번에 보내지 않고, 현재 글을 기준점으로만 저장합니다.
            self.sent_ids = current_ids
            self.saved_categories = set(WATCHED_CATEGORIES)
            self.persist_state()
            print("Initial news state saved; no existing posts were sent.")
            return

        new_categories = WATCHED_CATEGORIES - self.saved_categories
        if new_categories:
            # 새로 켠 카테고리의 과거 글은 기준점으로만 저장해 채널 도배를 막습니다.
            self.sent_ids.update(
                post["id"] for post in posts if post["category"] in new_categories
            )
            self.saved_categories.update(new_categories)
            self.persist_state()

        new_posts = [post for post in posts if post["id"] not in self.sent_ids]
        if not new_posts:
            logging.info("No new MapleStory announcements found.")
            return

        for post in sorted(new_posts, key=lambda item: item["liveDate"]):
            is_sunny_patch = is_patch_notes(post)
            sends_news = bool(self.alert_channels[ALERT_NEWS])
            if not sends_news and not is_sunny_patch:
                # 알림 채널이 없으면 불필요한 요약·번역 API를 호출하지 않고 기준점만 저장합니다.
                self.sent_ids.add(post["id"])
                self.persist_state()
                continue

            detail = await self.fetch_post_detail(post["id"])
            if sends_news:
                # 새 공지 한 건을 요약·번역한 뒤 등록된 모든 공지 채널에 같은 임베드를 보냅니다.
                korean_summary = (
                    await self.translate_texts([await self.summarize(detail)])
                )[0]
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
                await self.send_alert_embed(ALERT_NEWS, embed)

            new_sunny_schedule = None
            if is_sunny_patch:
                new_sunny_schedule = await self.create_sunny_sunday_schedule(
                    post, detail
                )
                if new_sunny_schedule is not None:
                    await self.send_alert_embed(
                        ALERT_SUNNY_LIST,
                        build_sunny_sunday_embed(
                            f"☀️ {new_sunny_schedule['title']} ☀️",
                            new_sunny_schedule["url"],
                            new_sunny_schedule["entries"],
                        ),
                        attach_sunny_image=True,
                    )
            # 새 공지를 처리한 뒤 같은 글을 다시 요약하거나 전송하지 않도록 기록합니다.
            if new_sunny_schedule is not None:
                self.sunny_sunday = new_sunny_schedule
            self.sent_ids.add(post["id"])
            self.persist_state()
            logging.info("Sent announcement %s to Discord.", post["id"])

    @tasks.loop(minutes=1)
    async def check_sunny_sunday(self) -> None:
        # 저장된 일정만 확인해 모든 당일 알림 채널에 보내고 24시간 뒤 각각 삭제합니다.
        if self.sunny_sunday is None or self.sent_ids is None:
            return

        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        state_changed = False
        for entry in self.sunny_sunday["entries"]:
            message_ids = entry["message_ids"]
            channel_ids = self.alert_channels[ALERT_SUNNY_DAY] | {
                int(channel_id) for channel_id in message_ids
            }
            for channel_id in sorted(channel_ids):
                action = sunny_sunday_entry_action(
                    entry, channel_id, now_timestamp
                )
                if action is None:
                    continue
                channel = self.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    logging.warning(
                        "Sunny Sunday channel %s is not accessible.", channel_id
                    )
                    if action == "delete":
                        del message_ids[str(channel_id)]
                        state_changed = True
                    continue

                if action == "send":
                    try:
                        message = await self.send_sunny_sunday_to_channel(
                            channel, "☀️ 이번 주 Sunny Sunday ☀️", [entry]
                        )
                    except discord.HTTPException:
                        logging.exception(
                            "Failed to send weekly Sunny Sunday to %s.", channel_id
                        )
                        continue
                    message_ids[str(channel_id)] = message.id
                    state_changed = True
                    logging.info(
                        "Sent weekly Sunny Sunday message %s to %s.",
                        message.id,
                        channel_id,
                    )
                elif action == "delete":
                    message_id = message_ids[str(channel_id)]
                    try:
                        await channel.get_partial_message(message_id).delete()
                    except discord.NotFound:
                        pass
                    except discord.HTTPException:
                        logging.exception(
                            "Failed to delete Sunny Sunday message %s.", message_id
                        )
                        continue
                    del message_ids[str(channel_id)]
                    state_changed = True
                    logging.info("Removed expired Sunny Sunday message %s.", message_id)

        if state_changed:
            self.persist_state()

    @check_news.error
    async def check_news_error(self, error: Exception) -> None:
        # API나 전송 단계의 오류를 서버 로그에 남겨 원인을 확인할 수 있게 합니다.
        logging.exception("MapleStory announcement check failed.", exc_info=error)

    @check_sunny_sunday.error
    async def check_sunny_sunday_error(self, error: Exception) -> None:
        # 주간 팝업 전송이나 삭제 실패를 서버 로그에서 확인할 수 있게 합니다.
        logging.exception("Sunny Sunday schedule check failed.", exc_info=error)

    @check_news.before_loop
    async def before_check_news(self) -> None:
        # 디스코드 기본 연결 대기 함수를 가리지 않도록 다른 이름을 사용합니다.
        await self.wait_until_ready()

    @check_sunny_sunday.before_loop
    async def before_check_sunny_sunday(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    # .env 파일을 읽은 뒤 Discord 봇을 실행합니다.
    load_dotenv()
    MapleNewsBot(int(os.environ["DISCORD_CHANNEL_ID"])).run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
