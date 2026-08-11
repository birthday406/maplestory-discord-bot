import html
import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from openai import AsyncOpenAI

from maple_calculators import (
    calculate_arcane_symbol_completion,
    calculate_epic_dungeon,
    calculate_exp_coupons,
    calculate_growth_potions,
    calculate_hexa_cost,
    calculate_symbol,
    simulate_extreme_growth_potions,
)
from maple_data import (
    ELANOS_SYMBOL_BONUS_END,
    EPIC_DUNGEON_BONUSES,
    EPIC_DUNGEONS,
    EXP_COUPON_BURNING_OPTIONS,
    EXP_COUPONS,
    GROWTH_POTIONS,
    HEXA_CORE_COSTS,
    LEVEL_EXP,
    SYMBOL_REGIONS,
    SYMBOL_TYPES,
)


NEWS_URL = "https://g.nexonstatic.com/maplestory/cms/v1/news"
NEWS_DETAIL_URL = "https://g.nexonstatic.com/maplestory/cms/v1/news/{post_id}"
PSSB_RATES_API_URL = "https://g.nexonstatic.com/maplestory/cms/v1/general-posts/5797"
PSSB_RATES_PAGE_URL = "https://www.nexon.com/maplestory/general-post/5797"
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
ALERT_MIRACLE_TIME = "miracle_time"
ALERT_CASH_TRANSFER = "cash_transfer"
ALERT_CUBE_SALE = "cube_sale"
ALERT_TYPES = (
    ALERT_NEWS,
    ALERT_SUNNY_DAY,
    ALERT_SUNNY_LIST,
    ALERT_MIRACLE_TIME,
    ALERT_CASH_TRANSFER,
    ALERT_CUBE_SALE,
)

# Discord 애플리케이션에 등록한 HEXA 계산기용 일반 이모지입니다.
HEXA_EMOJI = "<:HEXA:1534436226751529031>"
SOL_ERDA_EMOJI = "<:SolErda:1534436216139944108>"
FRAGMENT_EMOJI = "<:Fragment:1534436205796790324>"
ANIMATED_TWINKLE_EMOJI = "<a:Animated_Twinkle:1534436193276792873>"
EGP_EMOJI = "<:EGP:1536685490789679104>"
LADY_BLAIR_EMOJI = "<:ladyblair:1536691685017518110>"
BONUS_CUBE_EMOJI = "<:BC:1536697424251330630>"
PSSB_EMOJI = "<:SSB:1536697384011178015>"
EXP_COUPON_EMOJIS = {
    "EXP 교환권": "<:EV:1536691867293323274>",
    "상급 EXP 교환권": "<:AEV:1536691857692565554>",
}
EPIC_DUNGEON_EMOJIS = {
    "하이마운틴": "<:HMountain:1536686575558205540>",
    "앵글러컴퍼니": "<:Angler:1536686640045756446>",
    "악몽선경": "<:Nightmare:1536686565210722324>",
}

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

# Google 번역 결과와 기존 state.json에 남은 표현을 게임 내 공식 명칭으로 고칩니다.
SUNNY_SUNDAY_LOCALIZATIONS = (
    ("Spiegelette", "슈피겔라"),
    ("슈피겔레트", "슈피겔라"),
    ("스피겔레트", "슈피겔라"),
    ("Haste Fever Time Booster", "헤이스트 피버 타임 부스터"),
    ("가속 열풍 시간 부스터", "헤이스트 피버 타임 부스터"),
)

MIRACLE_TIME_EQUIPMENT_TRANSLATIONS = {
    "Emblem, Mechanical Heart, Ring, Accessory": "엠블렘, 기계 심장, 반지, 장신구",
    "Weapon, Secondary Weapon, Shield": "무기, 보조무기, 방패",
    "Top, Bottom, Outfit, Cape": "상의, 하의, 한벌옷, 망토",
    "Hat": "모자",
    "Gloves": "장갑",
    "Shoes": "신발",
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
            ALERT_MIRACLE_TIME: set(),
            ALERT_CASH_TRANSFER: set(),
            ALERT_CUBE_SALE: set(),
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


def parse_pssb_rates(source: str) -> list[tuple[str, float]]:
    # 공식 확률표의 각 행에서 아이템 이름, 성별, 확률만 꺼냅니다.
    entries: list[tuple[str, float]] = []
    pending_gender_item: tuple[str, float] | None = None

    for row in re.findall(r"<tr\b.*?</tr>", source, flags=re.IGNORECASE | re.DOTALL):
        cells = [
            html.unescape(html_to_text(cell))
            for cell in re.findall(
                r"<t[dh]\b[^>]*>(.*?)</t[dh]>",
                row,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        if len(cells) < 2 or not cells[0]:
            continue

        name, gender = cells[:2]
        rate_match = (
            re.fullmatch(r"(\d+(?:\.\d+)?)%", cells[2])
            if len(cells) >= 3
            else None
        )
        rate = float(rate_match.group(1)) if rate_match else None

        if gender == "All" and rate is not None:
            # 성별 제한이 없는 행은 그대로 하나의 보상으로 추가합니다.
            if pending_gender_item is not None:
                entries.append(pending_gender_item)
                pending_gender_item = None
            entries.append((name, rate))
        elif gender in {"Male", "Female"} and rate is not None:
            # rowspan으로 확률을 공유하는 성별 아이템의 첫 번째 행을 잠시 보관합니다.
            if pending_gender_item is not None:
                entries.append(pending_gender_item)
            pending_gender_item = (name, rate)
        elif gender in {"Male", "Female"} and pending_gender_item is not None:
            # 두 성별 아이템은 실제로 하나의 보상 칸이므로 확률을 두 번 더하지 않습니다.
            entries.append(
                (f"{pending_gender_item[0]} / {name}", pending_gender_item[1])
            )
            pending_gender_item = None

    if pending_gender_item is not None:
        entries.append(pending_gender_item)
    return entries


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


def utc_event_timestamp(value: str) -> int:
    # 공식 일정의 영문 UTC 날짜를 Discord와 알림 검사에 쓰는 Unix 시간으로 바꿉니다.
    normalized = value.replace(" at ", " ").strip()
    moment = datetime.strptime(normalized, "%B %d, %Y %I:%M %p UTC")
    return int(moment.replace(tzinfo=timezone.utc).timestamp())


def extract_cash_shop_transfer(source: str) -> dict | None:
    # CashShopTransfer 제목 바로 다음 문단에 적힌 시작·종료 시간만 꺼냅니다.
    section = re.search(
        r'id=["\']CashShopTransfer["\'].*?<p\b[^>]*>(.*?)</p>',
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if section is None:
        return None

    period = html_to_text(section.group(1))
    date_pattern = (
        r"[A-Z][a-z]+ \d{1,2}, \d{4} (?:at )?\d{1,2}:\d{2} [AP]M UTC"
    )
    dates = re.findall(date_pattern, period)
    if len(dates) != 2:
        return None
    return {
        "start_timestamp": utc_event_timestamp(dates[0]),
        "end_timestamp": utc_event_timestamp(dates[1]),
    }


def extract_miracle_time(source: str) -> list[dict]:
    # MiracleTime 표의 장비 부위와 날짜를 한 행씩 저장합니다.
    section = re.search(
        r'id=["\']MiracleTime["\'].*?(<table\b.*?</table>)',
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if section is None:
        return []

    entries = []
    for row in re.findall(
        r"<tr\b.*?</tr>", section.group(1), flags=re.IGNORECASE | re.DOTALL
    ):
        cells = [
            html.unescape(html_to_text(cell))
            for cell in re.findall(
                r"<td\b[^>]*>(.*?)</td>",
                row,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        if len(cells) != 2:
            continue

        date_match = re.fullmatch(
            r"([A-Z][a-z]+ \d{1,2}, \d{4}) "
            r"(\d{1,2}:\d{2} [AP]M UTC) - (\d{1,2}:\d{2} [AP]M UTC)",
            cells[1],
        )
        if date_match is None:
            continue

        date, start_time, end_time = date_match.groups()
        entries.append(
            {
                "equipment": MIRACLE_TIME_EQUIPMENT_TRANSLATIONS.get(
                    cells[0], cells[0]
                ),
                "start_timestamp": utc_event_timestamp(f"{date} {start_time}"),
                "end_timestamp": utc_event_timestamp(f"{date} {end_time}"),
                "notified_channel_ids": [],
            }
        )
    return entries


def merge_patch_events(current: dict | None, updated: dict) -> dict:
    # 같은 패치노트가 수정돼도 이미 보낸 미라클 알림 기록은 유지합니다.
    if current is None or current.get("post_id") != updated["post_id"]:
        return updated
    current_cash_transfer = current.get("cash_shop_transfer") or {}
    updated_cash_transfer = updated.get("cash_shop_transfer")
    if updated_cash_transfer is not None:
        updated_cash_transfer["notified_channel_ids"] = current_cash_transfer.get(
            "notified_channel_ids", []
        )
    notified_by_start = {
        entry["start_timestamp"]: entry.get("notified_channel_ids", [])
        for entry in current.get("miracle_time", [])
    }
    for entry in updated.get("miracle_time", []):
        entry["notified_channel_ids"] = notified_by_start.get(
            entry["start_timestamp"], []
        )
    return updated


def should_send_miracle_time(
    entry: dict, channel_id: int, now_timestamp: int
) -> bool:
    return (
        entry["start_timestamp"] <= now_timestamp <= entry["end_timestamp"]
        and channel_id not in entry.get("notified_channel_ids", [])
    )


def should_send_cash_shop_transfer(
    event: dict, channel_id: int, now_timestamp: int
) -> bool:
    # 이벤트 시작부터 24시간 동안만 당일 알림을 보내고 채널별 전송 기록으로 중복을 막습니다.
    alert_end = min(event["end_timestamp"], event["start_timestamp"] + 86_400)
    return (
        event["start_timestamp"] <= now_timestamp < alert_end
        and channel_id not in event.get("notified_channel_ids", [])
    )


def known_sunny_sunday_translation(perk: str) -> str | None:
    # 대소문자, 공백, 곱하기 기호가 달라도 같은 고정 번역을 찾을 수 있게 정규화합니다.
    normalized = re.sub(r"\s+", " ", perk).strip().lower().replace("×", "x")
    for phrase, translation in SUNNY_SUNDAY_TRANSLATIONS:
        if phrase in normalized:
            return translation
    return None


def localize_sunny_sunday_text(text: str) -> str:
    """번역기 표현을 메이플스토리에서 사용하는 명칭으로 바꿉니다."""
    for translated_name, localized_name in SUNNY_SUNDAY_LOCALIZATIONS:
        text = text.replace(translated_name, localized_name)
    return text


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
        embed.add_field(
            name=entry["name"],
            value=localize_sunny_sunday_text(entry["value"]),
            inline=False,
        )
    embed.set_image(url=f"attachment://{SUNNY_SUNDAY_IMAGE_PATH.name}")
    return embed


def build_cash_shop_transfer_embed(schedule: dict) -> discord.Embed:
    event = schedule["cash_shop_transfer"]
    start = event["start_timestamp"]
    end = event["end_timestamp"]
    embed = discord.Embed(
        title=f"{LADY_BLAIR_EMOJI} 캐시 보관함 이동 이벤트",
        url=schedule["url"],
        description=(
            f"**시작**　<t:{start}:F> (<t:{start}:R>)\n"
            f"**종료**　<t:{end}:F> (<t:{end}:R>)\n\n"
            "◆ **참여 조건**　Lv.101 이상\n"
            "　제로 캐릭터는 스토리 퀘스트 Act 2 완료 필요\n\n"
            "캐시샵의 캐시 보관함에서 **Cash Transfer** 버튼을 눌러 "
            "다른 직업군 캐릭터로 아이템을 옮길 수 있습니다."
        ),
        color=0x3498DB,
    )
    embed.set_author(name="MapleStory | CASH SHOP TRANSFER")
    return embed


def build_miracle_time_embed(
    schedule: dict,
    entries: list[dict],
    title: str = f"{BONUS_CUBE_EMOJI} 미라클 타임 일정",
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        url=schedule["url"],
        description=(
            "대상 장비에 큐브를 사용할 때 **잠재능력 등급 상승 확률이 2배**가 됩니다.\n"
            "사용 가능: Glowing·Bright·Bonus Glowing·Bonus Bright·Violet Cube"
        ),
        color=0x9B59B6,
    )
    embed.set_author(name="MapleStory | MIRACLE TIME")
    for entry in entries:
        start = entry["start_timestamp"]
        embed.add_field(
            name=f"· __<t:{start}:F> (<t:{start}:R>)__",
            value=f"**대상 장비**　{entry['equipment']}",
            inline=False,
        )
    return embed


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


@app_commands.command(name="익성비", description="익스트림 성장의 비약 결과를 무작위로 추첨합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(current_level="시작레벨", count="개수")
@app_commands.describe(
    current_level="시작 캐릭터 레벨 (130~199)",
    count="사용할 익스트림 성장의 비약 개수 (1~100)",
)
async def extreme_growth_potion_command(
    interaction: discord.Interaction,
    current_level: app_commands.Range[int, 130, 199],
    count: app_commands.Range[int, 1, 100],
) -> None:
    result_level, level_gains = simulate_extreme_growth_potions(current_level, count)

    level = current_level
    result_lines = []
    for index, level_gain in enumerate(level_gains, start=1):
        next_level = min(level + level_gain, 200)
        result_lines.append(
            f"**{index}회**　Lv.{level} → Lv.{next_level} (+{next_level - level})"
        )
        level = next_level

    count_text = f"{count}개"
    if len(level_gains) < count:
        count_text += f" (Lv.200 도달로 {len(level_gains)}개 사용)"

    embed = discord.Embed(
        title=f"{EGP_EMOJI} 익스트림 성장의 비약 시뮬레이터",
        description=(
            f"**사용 전**　Lv.{current_level}\n"
            f"**입력 개수**　{count_text}\n\n"
            + "\n".join(result_lines)
            + f"\n\n◆ **최종 결과**　Lv.{result_level}"
        ),
        color=0x57F287,
    )
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="성장의비약", description="성장의 비약 사용 결과를 계산합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(
    potion="비약종류",
    current_level="시작레벨",
    current_exp_percent="경험치",
    hyper_burning="하이퍼버닝",
    beyond_burning="비욘드버닝",
    count="개수",
)
@app_commands.describe(
    potion="사용할 성장의 비약 종류",
    current_level="시작 캐릭터 레벨 (200~299)",
    current_exp_percent="현재 경험치 퍼센트 (0 이상 100 미만)",
    hyper_burning="하이퍼 버닝 캐릭터인지 선택",
    beyond_burning="비욘드 버닝 캐릭터인지 선택",
    count="사용할 비약 개수 (1~100)",
)
@app_commands.choices(
    potion=[
        app_commands.Choice(name=potion_name, value=potion_name)
        for potion_name in GROWTH_POTIONS
    ],
    hyper_burning=[
        app_commands.Choice(name=name, value=name) for name in ("적용", "미적용")
    ],
    beyond_burning=[
        app_commands.Choice(name=name, value=name) for name in ("적용", "미적용")
    ],
)
async def growth_potion_command(
    interaction: discord.Interaction,
    potion: app_commands.Choice[str],
    current_level: app_commands.Range[int, 200, 299],
    current_exp_percent: app_commands.Range[float, 0.0, 99.999],
    hyper_burning: app_commands.Choice[str],
    beyond_burning: app_commands.Choice[str],
    count: app_commands.Range[int, 1, 100],
) -> None:
    # Discord에는 한글 선택지를 보여주고 계산 함수에는 기존 bool 값으로 전달합니다.
    hyper_burning_enabled = hyper_burning.value == "적용"
    beyond_burning_enabled = beyond_burning.value == "적용"
    result_level, result_exp, gained_exp, used_count = calculate_growth_potions(
        potion.value,
        current_level,
        current_exp_percent,
        count,
        hyper_burning_enabled,
        beyond_burning_enabled,
    )
    result_text = "Lv.300 (MAX)"
    if result_level < 300:
        result_percent = result_exp / LEVEL_EXP[result_level - 200] * 100
        result_text = f"Lv.{result_level} ({result_percent:.3f}%)"

    count_text = f"{count}개"
    if used_count < count:
        count_text += f" (Lv.300 도달로 {used_count}개 적용)"

    embed = discord.Embed(
        title="🌱 성장의 비약 계산기",
        description=(
            f"**비약**　{potion.name}\n"
            f"**사용 전**　Lv.{current_level} ({current_exp_percent:.3f}%)\n"
            f"**하이퍼 버닝**　{hyper_burning.name}\n"
            f"**비욘드 버닝**　{beyond_burning.name}\n"
            f"**사용 개수**　{count_text}\n\n"
            f"◆ **사용 후**　{result_text}\n"
            f"◆ **지급 경험치**　{gained_exp:,}"
        ),
        color=0x57F287,
    )
    embed.set_footer(text="입력한 경험치 퍼센트를 실제 경험치로 환산한 근사 결과입니다.")
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="exp쿠폰", description="EXP 교환권 사용 결과를 계산합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(
    coupon="쿠폰종류",
    current_level="시작레벨",
    current_exp_percent="경험치",
    burning="버닝",
    count="개수",
)
@app_commands.describe(
    coupon="EXP 교환권 종류",
    current_level="시작 캐릭터 레벨 (200~299)",
    current_exp_percent="현재 경험치 퍼센트 (0 이상 100 미만)",
    burning="생략하면 마지막 선택 사용 (첫 사용은 X)",
    count="사용할 교환권 개수 (1~1억)",
)
@app_commands.choices(
    coupon=[
        app_commands.Choice(name=coupon_name, value=coupon_name)
        for coupon_name in EXP_COUPONS
    ],
    burning=[
        app_commands.Choice(name=burning_name, value=burning_name)
        for burning_name in EXP_COUPON_BURNING_OPTIONS
    ],
)
async def exp_coupon_command(
    interaction: discord.Interaction,
    coupon: app_commands.Choice[str],
    current_level: app_commands.Range[int, 200, 299],
    current_exp_percent: app_commands.Range[float, 0.0, 99.999],
    count: app_commands.Range[int, 1, 100_000_000],
    burning: app_commands.Choice[str] | None = None,
) -> None:
    # 버닝을 생략하면 이 사용자가 마지막으로 고른 값을 쓰고, 첫 사용은 X로 계산합니다.
    user_id = str(interaction.user.id)
    preferences = interaction.client.exp_coupon_burning_preferences
    burning_name = preferences.get(user_id, "X") if burning is None else burning.value
    if burning is not None and preferences.get(user_id) != burning_name:
        preferences[user_id] = burning_name
        interaction.client.persist_state()

    try:
        result_level, result_exp, gained_exp, used_count = calculate_exp_coupons(
            coupon.value, current_level, current_exp_percent, count, burning_name
        )
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    result_text = "Lv.300 (MAX)"
    if result_level < 300:
        result_percent = result_exp / LEVEL_EXP[result_level - 200] * 100
        result_text = f"Lv.{result_level} ({result_percent:.3f}%)"

    count_text = f"{count:,}개"
    if used_count < count:
        stop_reason = "Lv.300 도달" if result_level == 300 else "사용 가능 레벨 초과"
        count_text += f" ({stop_reason}로 {used_count:,}개 적용)"

    embed = discord.Embed(
        title=f"{EXP_COUPON_EMOJIS[coupon.value]} {coupon.name} 계산기",
        description=(
            f"**교환권**　{coupon.name}\n"
            f"**사용 전**　Lv.{current_level} ({current_exp_percent:.3f}%)\n"
            f"**버닝**　{burning_name}\n"
            f"**입력 개수**　{count_text}\n\n"
            f"◆ **사용 후**　{result_text}\n"
            f"◆ **지급 경험치**　{gained_exp:,}"
        ),
        color=0xF1C40F,
    )
    embed.set_footer(text="입력한 경험치 퍼센트를 실제 경험치로 환산한 근사 결과입니다.")
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="에픽던전", description="에픽 던전 완료 후 경험치를 계산합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(
    dungeon="던전",
    current_level="레벨",
    current_exp_percent="경험치",
    experience_bonus="경험치보너스",
)
@app_commands.describe(
    dungeon="계산할 에픽 던전",
    current_level="현재 캐릭터 레벨 (260~299)",
    current_exp_percent="현재 경험치 퍼센트 (0 이상 100 미만)",
    experience_bonus="적용할 경험치 배율",
)
@app_commands.choices(
    dungeon=[
        app_commands.Choice(name=dungeon_name, value=dungeon_name)
        for dungeon_name in EPIC_DUNGEONS
    ],
    experience_bonus=[
        app_commands.Choice(name=f"{bonus:g}배", value=bonus)
        for bonus in EPIC_DUNGEON_BONUSES
    ],
)
async def epic_dungeon_command(
    interaction: discord.Interaction,
    dungeon: app_commands.Choice[str],
    current_level: app_commands.Range[int, 260, 299],
    current_exp_percent: app_commands.Range[float, 0.0, 99.999],
    experience_bonus: app_commands.Choice[float],
) -> None:
    try:
        result_level, result_exp, base_exp, gained_exp = calculate_epic_dungeon(
            dungeon.value,
            current_level,
            current_exp_percent,
            experience_bonus.value,
        )
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    result_text = "Lv.300 (MAX)"
    if result_level < 300:
        result_percent = result_exp / LEVEL_EXP[result_level - 200] * 100
        result_text = f"Lv.{result_level} ({result_percent:.3f}%)"

    dungeon_info = EPIC_DUNGEONS[dungeon.value]
    embed = discord.Embed(
        title=(
            f"{EPIC_DUNGEON_EMOJIS[dungeon.value]} "
            "에픽 던전 경험치 계산기"
        ),
        description=(
            f"**던전**　{dungeon.name}\n"
            f"**사용 전**　Lv.{current_level} ({current_exp_percent:.3f}%)\n"
            f"**경험치 보너스**　{experience_bonus.name}\n\n"
            f"◆ **기본 경험치**　{base_exp:,}\n"
            f"◆ **적용 경험치**　{gained_exp:,}\n"
            f"◆ **완료 후**　{result_text}\n\n"
            f"{SOL_ERDA_EMOJI} **솔 에르다 보상**　"
            f"{dungeon_info['sol_erda_reward']}\n"
            f"{FRAGMENT_EMOJI} **솔 에르다 조각**　"
            f"{dungeon_info['fragment_reward']}개"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="입력한 경험치 퍼센트를 실제 경험치로 환산한 근사 결과입니다.")
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="심볼계산기", description="심볼 성장에 필요한 개수와 메소를 계산합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(
    region="지역",
    current_level="현재레벨",
    current_growth="현재성장치",
    target_level="목표레벨",
    potion_level="보약레벨",
    elanos="엘라노스",
)
@app_commands.describe(
    region="지역을 선택하면 심볼 종류를 자동으로 판별합니다.",
    current_level="현재 심볼 레벨",
    current_growth="현재 심볼에 누적된 성장치",
    target_level="도달하려는 심볼 레벨",
    potion_level="생략하면 마지막 선택 사용 (첫 사용은 없음)",
    elanos="생략하면 마지막 선택 사용 (첫 사용은 미적용)",
)
@app_commands.choices(
    region=[
        app_commands.Choice(name=name, value=name) for name in SYMBOL_REGIONS
    ],
    potion_level=[
        app_commands.Choice(name="없음" if level == 0 else f"{level}레벨", value=level)
        for level in range(7)
    ],
    elanos=[
        app_commands.Choice(name=name, value=name) for name in ("적용", "미적용")
    ],
)
async def symbol_calculator_command(
    interaction: discord.Interaction,
    region: app_commands.Choice[str],
    current_level: app_commands.Range[int, 1, 20],
    current_growth: app_commands.Range[int, 0, 10_000],
    target_level: app_commands.Range[int, 2, 20],
    potion_level: app_commands.Choice[int] | None = None,
    elanos: app_commands.Choice[str] | None = None,
) -> None:
    # 생략한 항목은 이 사용자의 마지막 선택을 쓰고, 첫 사용은 보약 없음·엘라노스 미적용입니다.
    user_id = str(interaction.user.id)
    preferences = interaction.client.symbol_calculator_preferences
    saved_preferences = preferences.get(user_id, {})
    potion_level_value = (
        saved_preferences.get("potion_level", 0)
        if potion_level is None
        else potion_level.value
    )
    elanos_name = (
        saved_preferences.get("elanos", "미적용") if elanos is None else elanos.value
    )
    selected_preferences = {
        "potion_level": potion_level_value,
        "elanos": elanos_name,
    }
    if (potion_level is not None or elanos is not None) and (
        saved_preferences != selected_preferences
    ):
        preferences[user_id] = selected_preferences
        interaction.client.persist_state()

    start_date = datetime.now(timezone.utc).date()
    symbol_type = SYMBOL_REGIONS[region.value]["symbol_type"]
    try:
        (
            required_symbols,
            meso_cost,
            base_daily_symbols,
            selected_daily_symbols,
            required_days,
            completion_date,
        ) = calculate_symbol(
            region.value,
            current_level,
            current_growth,
            target_level,
            potion_level_value,
            elanos_name == "적용",
            start_date,
        )
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    symbol = SYMBOL_TYPES[symbol_type]
    potion_bonus = symbol["potion_bonus"][potion_level_value]
    potion_level_name = (
        "없음" if potion_level_value == 0 else f"{potion_level_value}레벨"
    )
    current_level_requirement = symbol["growth"][current_level - 1]
    event_end_timestamp = int(ELANOS_SYMBOL_BONUS_END.timestamp())
    completion_timestamp = int(
        datetime(
            completion_date.year,
            completion_date.month,
            completion_date.day,
            tzinfo=timezone.utc,
        ).timestamp()
    )
    completion_text = "이미 목표 성장치를 확보했습니다."
    if required_days:
        completion_text = (
            f"{required_days:,}일 · <t:{completion_timestamp}:D> "
            f"(<t:{completion_timestamp}:R>)"
        )

    weekly_completion_text = ""
    if symbol_type == "아케인 심볼" and required_symbols:
        weekly_lines = []
        for label, current_weekly_quest in (
            ("이번 주 주간퀘 함", True),
            ("이번 주 주간퀘 안 함", False),
        ):
            weekly_days, weekly_date = calculate_arcane_symbol_completion(
                required_symbols,
                base_daily_symbols,
                selected_daily_symbols,
                start_date,
                current_weekly_quest,
            )
            weekly_timestamp = int(
                datetime(
                    weekly_date.year,
                    weekly_date.month,
                    weekly_date.day,
                    tzinfo=timezone.utc,
                ).timestamp()
            )
            weekly_lines.append(
                f"◆ **{label}**　{weekly_days:,}일 · "
                f"<t:{weekly_timestamp}:D> (<t:{weekly_timestamp}:R>)"
            )
        weekly_completion_text = "\n" + "\n".join(weekly_lines)

    embed = discord.Embed(
        title="🔮 아케인·어센틱 심볼 계산기",
        description=(
            f"**심볼**　{symbol_type} · {region.name}\n"
            f"**성장 구간**　Lv.{current_level} → Lv.{target_level}\n"
            f"**현재 성장치**　{current_growth:,} / {current_level_requirement:,}\n"
            f"**보약**　{potion_level_name} (+{potion_bonus}개)\n\n"
            f"**엘라노스**　{elanos_name}\n\n"
            f"◆ **추가 필요 심볼**　{required_symbols:,}개\n"
            f"◆ **강화 비용**　{meso_cost:,} 메소\n"
            f"◆ **일일퀘만 수행**　{completion_text}"
            f"{weekly_completion_text}\n\n"
            f"**평소 일일 획득**　{base_daily_symbols}개\n"
            f"**선택 조건 일일 획득**　{selected_daily_symbols}개\n"
            f"**엘라노스 종료**　<t:{event_end_timestamp}:F>"
        ),
        color=0x9B59B6,
    )
    footer_text = "오늘 일일 퀘스트를 아직 받지 않은 성장치 기준입니다."
    if symbol_type == "아케인 심볼":
        footer_text = "오늘 일일 퀘스트와 이번 주 주간 퀘스트를 아직 받지 않은 성장치 기준입니다."
    embed.set_footer(text=footer_text)
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="명령어", description="일반 사용자가 쓸 수 있는 명령어를 안내합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def help_command(interaction: discord.Interaction) -> None:
    """일반 사용자가 쓸 수 있는 명령어를 기능별로 보여줍니다."""
    embed = discord.Embed(
        title="📚 메이플스토리 봇 명령어",
        description="명령어를 입력하면 Discord가 필요한 선택 항목을 안내합니다.",
        color=0x5865F2,
    )
    embed.add_field(
        name="계산기",
        value=(
            "`/헥사` `/성장의비약` `/exp쿠폰`\n"
            "`/에픽던전` `/심볼계산기`"
        ),
        inline=False,
    )
    embed.add_field(
        name="시뮬레이터",
        value="`/익성비` `/스스비` `/채널추천`",
        inline=False,
    )
    embed.add_field(
        name="일정 확인",
        value=(
            "`/썬데이` `/썬데이목록` `/캐시이동`\n"
            "`/미라클큐브` `/핫위크` `/큐브세일`"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(
    name="채널추천",
    description="메이플스토리 1~40채널 중 하나를 추천합니다.",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def channel_recommend_command(interaction: discord.Interaction) -> None:
    # Discord 닉네임에 마크다운 문자가 있어도 메시지 모양이 깨지지 않게 처리합니다.
    display_name = discord.utils.escape_markdown(interaction.user.display_name)
    # 메이플스토리 게임 채널 1번부터 40번까지 중 하나를 같은 확률로 선택합니다.
    channel_number = random.randint(1, 40)

    # 말투를 바꾸고 싶다면 아래 문자열만 수정하면 됩니다.
    message = (
        f"우우우... **{display_name}**, 오늘도 많이 힘들었구나요! 😭\n"
        "간절한 마음을 모아 카미쨩이 아이템이 쏟아질 "
        "**행운의 채널**을 점지해드릴게요! ✨\n\n"
        "두구두구... 🥁 오늘의 추천 채널은 바로\n"
        f"🍀 **[ {channel_number}채널 ]** 🍀\n\n"
        "여기서 꼭 대박 아이템이 팡팡 터지길 바랄게요! 💖✅"
    )
    await interaction.response.send_message(message)


@app_commands.command(
    name="스스비",
    description="현재 PSSB 공식 확률표로 1회 또는 5회 시뮬레이션합니다.",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(count="횟수")
@app_commands.describe(count="시뮬레이션 횟수")
@app_commands.choices(
    count=[
        app_commands.Choice(name="1회", value=1),
        app_commands.Choice(name="5회", value=5),
    ]
)
async def pssb_command(
    interaction: discord.Interaction,
    count: app_commands.Choice[int],
) -> None:
    # 공식 확률표를 읽는 동안 Discord의 3초 응답 제한이 지나지 않게 먼저 대기 상태를 보냅니다.
    await interaction.response.defer()
    try:
        rates = await interaction.client.fetch_pssb_rates()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        logging.exception("Failed to load the official PSSB rates.")
        await interaction.followup.send(
            "공식 PSSB 확률표를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.",
            ephemeral=True,
        )
        return

    # 각 상자는 독립적으로 추첨하므로 같은 아이템이 여러 번 나올 수 있습니다.
    results = random.choices(
        rates,
        weights=[rate for _, rate in rates],
        k=count.value,
    )
    result_lines = [
        f"**{index}.** {name}　`{rate:.2f}%`"
        for index, (name, rate) in enumerate(results, start=1)
    ]
    embed = discord.Embed(
        title=f"{PSSB_EMOJI} Premium Surprise Style Box",
        url=PSSB_RATES_PAGE_URL,
        description="\n".join(result_lines),
        color=0xFF69B4,
    )
    embed.set_footer(text="공식 페이지에 표시된 반올림 확률 기준 · 실제 게임 결과와 무관")
    await interaction.followup.send(embed=embed)


@app_commands.command(name="썬데이", description="이번 주 썬데이 메이플 일정을 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def sunny_sunday_command(interaction: discord.Interaction) -> None:
    # 패치노트를 다시 요청하지 않고 봇이 state.json에서 불러온 일정만 사용합니다.
    schedule = getattr(interaction.client, "sunny_sunday", None)
    if schedule is None:
        await interaction.response.send_message(
            "저장된 썬데이 메이플 일정이 없습니다.", ephemeral=True
        )
        return

    entry = current_sunny_sunday_entry(schedule["entries"])
    if entry is None:
        await interaction.response.send_message(
            "남아 있는 썬데이 메이플 일정이 없습니다.", ephemeral=True
        )
        return

    embed = build_sunny_sunday_embed(
        "☀️ 이번 주 썬데이 메이플 ☀️", schedule["url"], [entry]
    )
    await interaction.response.send_message(
        embed=embed, file=discord.File(SUNNY_SUNDAY_IMAGE_PATH)
    )


@app_commands.command(name="썬데이목록", description="남아 있는 썬데이 메이플 일정을 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def sunny_sunday_list_command(interaction: discord.Interaction) -> None:
    # 이미 번역해 저장한 최신 패치노트의 전체 목록을 API 호출 없이 보여 줍니다.
    schedule = getattr(interaction.client, "sunny_sunday", None)
    if schedule is None:
        await interaction.response.send_message(
            "저장된 썬데이 메이플 일정이 없습니다.", ephemeral=True
        )
        return
    visible_entries = visible_sunny_sunday_entries(schedule["entries"])
    if not visible_entries:
        await interaction.response.send_message(
            "남아 있는 썬데이 메이플 일정이 없습니다.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=build_sunny_sunday_embed(
            f"☀️ {schedule['title']} ☀️", schedule["url"], visible_entries
        ),
        file=discord.File(SUNNY_SUNDAY_IMAGE_PATH),
    )


@app_commands.command(name="캐시이동", description="저장된 캐시 보관함 이동 일정을 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cash_shop_transfer_command(interaction: discord.Interaction) -> None:
    schedule = getattr(interaction.client, "patch_events", None)
    if schedule is None or schedule.get("cash_shop_transfer") is None:
        await interaction.response.send_message(
            "저장된 캐시이동 일정이 없습니다.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=build_cash_shop_transfer_embed(schedule)
    )


@app_commands.command(name="핫위크", description="핫위크 일정 안내를 확인합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def hot_week_command(interaction: discord.Interaction) -> None:
    # 실제 패치노트 수집을 연결하기 전까지 가짜 날짜나 보상을 안내하지 않습니다.
    embed = discord.Embed(
        title="🔥 핫위크",
        description="현재 진행 중인 핫위크 이벤트가 없습니다.",
        color=0xE67E22,
    )
    embed.set_author(name="MapleStory | HOT WEEK")
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="큐브세일", description="큐브세일 일정 안내를 확인합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cube_sale_command(interaction: discord.Interaction) -> None:
    # 실제 일정 수집을 연결하기 전까지 가짜 날짜나 할인율을 안내하지 않습니다.
    embed = discord.Embed(
        title="🧊 큐브세일",
        description="현재 진행 중인 큐브세일 이벤트가 없습니다.",
        color=0x5DADE2,
    )
    embed.set_author(name="MapleStory | CUBE SALE")
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="미라클큐브", description="저장된 미라클 타임 일정을 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def miracle_time_command(interaction: discord.Interaction) -> None:
    schedule = getattr(interaction.client, "patch_events", None)
    if schedule is None:
        await interaction.response.send_message(
            "저장된 미라클 타임 일정이 없습니다.", ephemeral=True
        )
        return

    now_timestamp = int(datetime.now(timezone.utc).timestamp())
    entries = [
        entry
        for entry in schedule.get("miracle_time", [])
        if now_timestamp <= entry["end_timestamp"]
    ]
    if not entries:
        await interaction.response.send_message(
            "남아 있는 미라클 타임 일정이 없습니다.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=build_miracle_time_embed(schedule, entries)
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
    # 알림 설정 명령은 표시 이름만 다르고 권한 검사와 저장 동작은 함께 사용합니다.
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


@app_commands.command(name="미라클큐브알림", description="미라클 타임 당일 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="알림을 받을 텍스트 채널", action="알림 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def miracle_time_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    await run_alert_setting_command(
        interaction, channel, action, ALERT_MIRACLE_TIME, "미라클 타임 당일 알림"
    )


@app_commands.command(name="캐시이동알림", description="캐시이동 당일 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="알림을 받을 텍스트 채널", action="알림 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def cash_shop_transfer_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    await run_alert_setting_command(
        interaction, channel, action, ALERT_CASH_TRANSFER, "캐시이동 당일 알림"
    )


@app_commands.command(name="큐브세일알림", description="큐브세일 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="알림을 받을 텍스트 채널", action="알림 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def cube_sale_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    # 실제 일정 알림을 연결하기 전에도 관리자가 받을 채널을 미리 저장할 수 있습니다.
    await run_alert_setting_command(
        interaction, channel, action, ALERT_CUBE_SALE, "큐브세일 알림"
    )


def load_state() -> tuple[
    set[int] | None,
    set[str],
    dict | None,
    dict | None,
    dict | None,
    dict[str, str],
    dict[str, dict],
]:
    # 이전 실행에서 이미 알린 공지 번호를 불러와 같은 글을 다시 보내지 않습니다.
    if not STATE_PATH.exists():
        return None, set(), None, None, None, {}, {}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    stored_sent_ids = state.get("sent_ids")
    return (
        None if stored_sent_ids is None else set(stored_sent_ids),
        set(state.get("watched_categories", LEGACY_WATCHED_CATEGORIES)),
        state.get("sunny_sunday"),
        state.get("patch_events"),
        state.get("alert_channels"),
        state.get("exp_coupon_burning_preferences", {}),
        state.get("symbol_calculator_preferences", {}),
    )


def save_state(
    sent_ids: set[int] | None,
    watched_categories: set[str],
    sunny_sunday: dict | None,
    alert_channels: dict[str, set[int]],
    patch_events: dict | None = None,
    exp_coupon_burning_preferences: dict[str, str] | None = None,
    symbol_calculator_preferences: dict[str, dict] | None = None,
) -> None:
    # 봇을 껐다 켜도 중복 알림을 막을 수 있도록 공지 번호를 파일에 저장합니다.
    STATE_PATH.write_text(
        json.dumps(
            {
                "sent_ids": None if sent_ids is None else sorted(sent_ids)[-500:],
                "watched_categories": sorted(watched_categories),
                "sunny_sunday": sunny_sunday,
                "patch_events": patch_events,
                "alert_channels": {
                    alert_type: sorted(channel_ids)
                    for alert_type, channel_ids in alert_channels.items()
                },
                "exp_coupon_burning_preferences": (
                    exp_coupon_burning_preferences or {}
                ),
                "symbol_calculator_preferences": symbol_calculator_preferences or {},
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
            self.patch_events,
            stored_alert_channels,
            self.exp_coupon_burning_preferences,
            self.symbol_calculator_preferences,
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
            help_command,
            hexa_command,
            extreme_growth_potion_command,
            growth_potion_command,
            exp_coupon_command,
            epic_dungeon_command,
            symbol_calculator_command,
            channel_recommend_command,
            pssb_command,
            sunny_sunday_command,
            sunny_sunday_list_command,
            cash_shop_transfer_command,
            hot_week_command,
            cube_sale_command,
            miracle_time_command,
            news_alert_command,
            sunny_day_alert_command,
            sunny_list_alert_command,
            miracle_time_alert_command,
            cash_shop_transfer_alert_command,
            cube_sale_alert_command,
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
            self.patch_events,
            self.exp_coupon_burning_preferences,
            self.symbol_calculator_preferences,
        )

    async def on_ready(self) -> None:
        # 디스코드 연결이 끝난 뒤에만 첫 공지 확인을 시작합니다.
        # 재연결되더라도 같은 확인 작업을 중복으로 시작하지 않습니다.
        if not self.check_news.is_running():
            self.check_news.start()
        if not self.check_sunny_sunday.is_running():
            self.check_sunny_sunday.start()
        if not self.check_miracle_time.is_running():
            self.check_miracle_time.start()
        if not self.check_cash_shop_transfer.is_running():
            self.check_cash_shop_transfer.start()

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

    async def fetch_pssb_rates(self) -> list[tuple[str, float]]:
        # 구성품이 바뀌면 바로 반영되도록 명령어를 실행할 때마다 공식 확률표를 요청합니다.
        assert self.session is not None
        async with self.session.get(
            PSSB_RATES_API_URL, timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            rates = parse_pssb_rates((await response.json())["body"])
        if not rates:
            raise ValueError("The official PSSB rate table is empty.")
        return rates

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
                    lines.append(f"- {localize_sunny_sunday_text(translation)}")
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

    def create_patch_event_schedule(self, post: dict, detail: dict) -> dict | None:
        # 비용이 드는 AI 번역 없이 공식 표의 날짜와 장비 부위만 저장합니다.
        cash_shop_transfer = extract_cash_shop_transfer(detail["body"])
        miracle_time = extract_miracle_time(detail["body"])
        if cash_shop_transfer is not None:
            cash_shop_transfer["notified_channel_ids"] = []
        if cash_shop_transfer is None and not miracle_time:
            return None
        return {
            "post_id": post["id"],
            "title": post["name"],
            "url": post_url(post),
            "cash_shop_transfer": cash_shop_transfer,
            "miracle_time": miracle_time,
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
        if (
            enabled
            and alert_type in {ALERT_SUNNY_DAY, ALERT_SUNNY_LIST}
            and self.sunny_sunday is not None
        ):
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
        latest_patch = next((post for post in posts if is_patch_notes(post)), None)
        latest_patch_detail = None

        # 이미 읽은 최신 패치노트도 다시 확인해 사후 추가된 이벤트 일정을 반영합니다.
        should_refresh_patch = latest_patch is not None and (
            self.sent_ids is None or latest_patch["id"] in self.sent_ids
        ) and (
            self.patch_events is None
            or self.patch_events.get("post_id") == latest_patch["id"]
        )
        if should_refresh_patch:
            latest_patch_detail = await self.fetch_post_detail(latest_patch["id"])
            updated_events = self.create_patch_event_schedule(
                latest_patch, latest_patch_detail
            )
            if updated_events is not None:
                merged_events = merge_patch_events(self.patch_events, updated_events)
                if merged_events != self.patch_events:
                    self.patch_events = merged_events
                    if self.sent_ids is not None:
                        self.persist_state()

        if self.sunny_sunday is None:
            # 기존 state.json에는 일정이 없으므로, 이미 처리한 최신 패치노트에서 최초 한 번만 채웁니다.
            should_bootstrap = latest_patch is not None and (
                self.sent_ids is None or latest_patch["id"] in self.sent_ids
            )
            if should_bootstrap:
                schedule = await self.create_sunny_sunday_schedule(
                    latest_patch, latest_patch_detail
                )
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
                    # 카테고리마다 다른 색을 써서 공지 성격을 한눈에 구분합니다.
                    color=CATEGORY_COLORS[post["category"]],
                )
                # Discord 임베드 왼쪽 위에 표시되는 작은 출처/카테고리 라벨입니다.
                embed.set_author(name=f"MapleStory | {post['category'].upper()}")
                # 공식 홈페이지 카드에 쓰인 썸네일을 임베드 하단의 큰 이미지로 보여 줍니다.
                embed.set_image(url=thumbnail_url(post))
                await self.send_alert_embed(ALERT_NEWS, embed)

            new_sunny_schedule = None
            new_patch_events = None
            if is_sunny_patch:
                new_sunny_schedule = await self.create_sunny_sunday_schedule(
                    post, detail
                )
                new_patch_events = self.create_patch_event_schedule(post, detail)
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
            if new_patch_events is not None:
                self.patch_events = merge_patch_events(
                    self.patch_events, new_patch_events
                )
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

    @tasks.loop(minutes=1)
    async def check_miracle_time(self) -> None:
        # UTC 자정부터 해당 날짜가 끝나기 전까지 채널별로 한 번만 알립니다.
        if self.patch_events is None or self.sent_ids is None:
            return

        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        state_changed = False
        for entry in self.patch_events.get("miracle_time", []):
            for channel_id in sorted(self.alert_channels[ALERT_MIRACLE_TIME]):
                if not should_send_miracle_time(entry, channel_id, now_timestamp):
                    continue
                channel = self.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    logging.warning(
                        "Miracle Time channel %s is not accessible.", channel_id
                    )
                    continue
                try:
                    await channel.send(
                        embed=build_miracle_time_embed(
                            self.patch_events,
                            [entry],
                            f"{BONUS_CUBE_EMOJI} 오늘의 미라클 타임",
                        )
                    )
                except discord.HTTPException:
                    logging.exception(
                        "Failed to send Miracle Time alert to %s.", channel_id
                    )
                    continue
                entry["notified_channel_ids"].append(channel_id)
                state_changed = True

        if state_changed:
            self.persist_state()

    @tasks.loop(minutes=1)
    async def check_cash_shop_transfer(self) -> None:
        # 저장된 일정의 시작 시각부터 24시간 안에 등록 채널별로 한 번만 알립니다.
        if self.patch_events is None or self.sent_ids is None:
            return

        event = self.patch_events.get("cash_shop_transfer")
        if event is None:
            return
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        state_changed = False
        for channel_id in sorted(self.alert_channels[ALERT_CASH_TRANSFER]):
            if not should_send_cash_shop_transfer(event, channel_id, now_timestamp):
                continue
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                logging.warning(
                    "Cash Shop Transfer channel %s is not accessible.", channel_id
                )
                continue
            try:
                await channel.send(
                    embed=build_cash_shop_transfer_embed(self.patch_events)
                )
            except discord.HTTPException:
                logging.exception(
                    "Failed to send Cash Shop Transfer alert to %s.", channel_id
                )
                continue
            event.setdefault("notified_channel_ids", []).append(channel_id)
            state_changed = True

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

    @check_miracle_time.error
    async def check_miracle_time_error(self, error: Exception) -> None:
        logging.exception("Miracle Time schedule check failed.", exc_info=error)

    @check_cash_shop_transfer.error
    async def check_cash_shop_transfer_error(self, error: Exception) -> None:
        logging.exception("Cash Shop Transfer schedule check failed.", exc_info=error)

    @check_news.before_loop
    async def before_check_news(self) -> None:
        # 디스코드 기본 연결 대기 함수를 가리지 않도록 다른 이름을 사용합니다.
        await self.wait_until_ready()

    @check_sunny_sunday.before_loop
    async def before_check_sunny_sunday(self) -> None:
        await self.wait_until_ready()

    @check_miracle_time.before_loop
    async def before_check_miracle_time(self) -> None:
        await self.wait_until_ready()

    @check_cash_shop_transfer.before_loop
    async def before_check_cash_shop_transfer(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    # .env 파일을 읽은 뒤 Discord 봇을 실행합니다.
    load_dotenv()
    MapleNewsBot(int(os.environ["DISCORD_CHANNEL_ID"])).run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
