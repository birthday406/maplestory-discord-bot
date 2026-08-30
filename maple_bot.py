import asyncio
import csv
import html
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import zipfile
from datetime import datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from openai import AsyncOpenAI
from PIL import Image, ImageDraw, ImageFont

from familiar_store import FamiliarExpectationStore
from ranking_store import MIN_TRACKED_LEVEL, RankingStore, scan_rankings

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
    BOSS_TRAFFIC_LIGHTS,
    ELANOS_SYMBOL_BONUS_END,
    EPIC_DUNGEON_BONUSES,
    EPIC_DUNGEONS,
    EXP_COUPON_BURNING_OPTIONS,
    EXP_COUPONS,
    FAMILIAR_DOUBLE_PRIME_CHANCE,
    FAMILIAR_EPIC_POTENTIALS,
    FAMILIAR_UNIQUE_POTENTIALS,
    GROWTH_POTIONS,
    HEXA_CORE_COSTS,
    LEVEL_EXP,
    SYMBOL_REGIONS,
    SYMBOL_TYPES,
)


BOT_VERSION = "1.4.3"
NEWS_URL = "https://g.nexonstatic.com/maplestory/cms/v1/news"
NEWS_DETAIL_URL = "https://g.nexonstatic.com/maplestory/cms/v1/news/{post_id}"
SERVER_STATUS_API_URL = "https://www.nexon.com/api/maplestory/no-auth/v1/server-status/na"
RANKING_API_URL = "https://www.nexon.com/api/maplestory/no-auth/ranking/v2/{region}"
USD_EXCHANGE_RATE_URL = "https://finance.naver.com/marketindex/exchangeList.naver"
SERVER_STATUS_PAGE_URL = (
    "https://www.nexon.com/maplestory/support/server-status/north-america/scania"
)
MAIN_WORLDS = ("Scania", "Bera", "Kronos", "Hyperion")
RANKING_WORLDS = {
    1: "Bera",
    19: "Scania",
    30: "Luna",
    45: "Kronos",
    46: "Solis",
    70: "Hyperion",
}
TRACKED_RANKING_WORLD_IDS = tuple(
    next(world_id for world_id, name in RANKING_WORLDS.items() if name == world_name)
    for world_name in MAIN_WORLDS
)
PSSB_RATES_API_URL = "https://g.nexonstatic.com/maplestory/cms/v1/general-posts/5797"
PSSB_RATES_PAGE_URL = "https://www.nexon.com/maplestory/general-post/5797"
CASH_SHOP_MINING_URL = "https://masonym.dev/cash-shop"
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
RANKING_DB_PATH = Path("ranking.db")
FAMILIAR_DB_PATH = Path("familiar.db")
RANKING_BACKUP_PATH = Path.home() / "maplestory-discord-bot-backups" / "ranking.db"
# 12.5명/초도 장시간 실행하면 공식 API가 403을 반환하므로
# 요청을 몰아서 보내지 않고 1초마다 한 페이지(최대 10명)씩 고르게 수집합니다.
RANKING_SCAN_INTERVAL_SECONDS = 1
RANKING_PAGES_PER_BATCH = 1
RANKING_BACKUP_INTERVAL = timedelta(hours=1)
RANKING_FORBIDDEN_BACKOFF_STEPS = (5 * 60, 15 * 60, 60 * 60, 6 * 60 * 60)
RANKING_RATE_LIMIT_BACKOFF_SECONDS = 60
RANKING_MAX_RATE_LIMIT_BACKOFF_SECONDS = 60 * 60
SEED_RING_LEVELS = {
    4: {"stone": "생명의 연마석", "rate_per_stone": 10},
    5: {"stone": "신념의 연마석", "rate_per_stone": 5},
}


class RankingRateLimited(Exception):
    def __init__(self, target: int | str, status: int, retry_after: int | None) -> None:
        super().__init__(f"Ranking target {target} returned {status}.")
        self.target = target
        self.status = status
        self.retry_after = retry_after


def ranking_backoff_seconds(
    status: int,
    retry_after: int | None,
    consecutive_failures: int,
) -> int:
    """403은 장기 회로 차단, 429는 짧은 단계적 대기로 처리합니다."""
    failures = max(consecutive_failures, 1)
    if status == 403:
        calculated = RANKING_FORBIDDEN_BACKOFF_STEPS[
            min(failures - 1, len(RANKING_FORBIDDEN_BACKOFF_STEPS) - 1)
        ]
    else:
        calculated = min(
            RANKING_RATE_LIMIT_BACKOFF_SECONDS * 2 ** (failures - 1),
            RANKING_MAX_RATE_LIMIT_BACKOFF_SECONDS,
        )
    return max(calculated, retry_after or 0)


def allocate_ranking_pages(
    world_ids: list[int] | tuple[int, ...], offset: int
) -> tuple[dict[int, int], int]:
    """한 페이지를 아직 수집 중인 월드에 순서대로 나눕니다."""
    if not world_ids:
        return {}, 0
    allocation: dict[int, int] = {}
    for step in range(RANKING_PAGES_PER_BATCH):
        world_id = world_ids[(offset + step) % len(world_ids)]
        allocation[world_id] = allocation.get(world_id, 0) + 1
    return allocation, (offset + RANKING_PAGES_PER_BATCH) % len(world_ids)


class KoreanCommandTranslator(app_commands.Translator):
    """지정된 명령어만 한국어 Discord에서 현지화해 표시합니다."""

    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> str | None:
        if locale is discord.Locale.korean:
            return string.extras.get("ko")
        return None


SUNNY_SUNDAY_IMAGE_PATH = Path(__file__).parent / "assets" / "title-sunny-sunday.webp"
CASH_SHOP_TRANSFER_IMAGE_PATH = Path(__file__).parent / "assets" / "cash-shop-transfer.png"
CASH_SHOP_UPDATE_IMAGE_PATH = Path(__file__).parent / "assets" / "cash-shop-update.png"
URSUS_ACTIVE_IMAGE_PATH = Path(__file__).parent / "assets" / "ursus-golden-time.jpg"
URSUS_INACTIVE_IMAGE_PATH = Path(__file__).parent / "assets" / "ursus-golden-time-inactive.jpg"
BOSS_THUMBNAIL_PATHS = {
    "헬럭스": Path(__file__).parent / "assets" / "boss-gollux.webp",
    "스우": Path(__file__).parent / "assets" / "boss-lotus.webp",
    "데미안": Path(__file__).parent / "assets" / "boss-damien.webp",
    "가엔슬": Path(__file__).parent / "assets" / "boss-guardian-angel-slime.webp",
    "루시드": Path(__file__).parent / "assets" / "boss-lucid.webp",
    "윌": Path(__file__).parent / "assets" / "boss-will.webp",
    "더스크": Path(__file__).parent / "assets" / "boss-dusk.webp",
    "듄켈": Path(__file__).parent / "assets" / "boss-dunkel.webp",
    "진 힐라": Path(__file__).parent / "assets" / "boss-verus-hilla.webp",
    "세렌": Path(__file__).parent / "assets" / "boss-seren.webp",
    "검마": Path(__file__).parent / "assets" / "boss-black-mage.webp",
    "칼로스": Path(__file__).parent / "assets" / "boss-kalos.webp",
    "발드릭스": Path(__file__).parent / "assets" / "boss-baldrix.webp",
    "카링": Path(__file__).parent / "assets" / "boss-kaling.webp",
    "최초의 대적자": Path(__file__).parent / "assets" / "boss-first-adversary.webp",
    "찬란한 흉성": Path(__file__).parent / "assets" / "boss-baleful-star.webp",
    "유피테르": Path(__file__).parent / "assets" / "boss-jupiter.webp",
    "림보": Path(__file__).parent / "assets" / "boss-limbo.webp",
}
# 흔히 "검밑"으로 묶어 부르는 보스와 대표 난이도입니다.
BLACK_MAGE_BELOW_BOSSES = (
    ("스우", "하드"),
    ("데미안", "하드"),
    ("루시드", "하드"),
    ("윌", "하드"),
    ("더스크", "카오스"),
    ("진 힐라", "하드"),
    ("듄켈", "하드"),
)
ITEM_DATA_PATH = Path(__file__).parent / "data" / "cash-items.tsv"
ITEM_ICON_ARCHIVE_PATH = Path(__file__).parent / "data" / "cash-item-icons.zip"
PSSB_BACK_EFFECT_PATH = Path(__file__).parent / "assets" / "pssb-backeffect.png"
PSSB_COMMON_SLOT_PATH = Path(__file__).parent / "assets" / "pssb-slot-common.png"
PSSB_ADVANCED_SLOT_PATH = Path(__file__).parent / "assets" / "pssb-slot-advanced.png"
PSSB_ADVANCED_RATE_THRESHOLD = 2.0
PSSB_SINGLE_PRICE = 3_600
PSSB_SET_SIZE = 11
PSSB_SET_PRICE = 36_000
FAMILIAR_ASSET_PATHS = {
    "back": Path(__file__).parent / "assets" / "familiar-card-back.png",
    "scene": Path(__file__).parent / "assets" / "familiar-card-scene.png",
    "spec": Path(__file__).parent / "assets" / "familiar-card-spec.png",
    "name": Path(__file__).parent / "assets" / "familiar-card-unique-name.png",
    "lock": Path(__file__).parent / "assets" / "familiar-card-lock.png",
    "edit": Path(__file__).parent / "assets" / "familiar-card-edit.png",
    "sherbet": Path(__file__).parent / "assets" / "familiar-sherbet.png",
    "font": Path(__file__).parent / "assets" / "NanumGothic.ttf",
}
URSUS_TIMEZONE = ZoneInfo("America/Los_Angeles")
INFO_CHANNEL_TIMEZONE = ZoneInfo("Asia/Seoul")
POLL_INTERVAL_MINUTES = 5
SUNNY_SUNDAY_DURATION_SECONDS = 24 * 60 * 60
MODEL = "gpt-5.6-luna"
ALERT_NEWS = "news"
ALERT_SUNNY_DAY = "sunny_day"
ALERT_SUNNY_LIST = "sunny_list"
ALERT_MIRACLE_TIME = "miracle_time"
ALERT_CASH_TRANSFER = "cash_transfer"
ALERT_CUBE_SALE = "cube_sale"
ALERT_URSUS = "ursus"
ALERT_SERVER = "server"
ALERT_EXCHANGE_LOG = "exchange_log"
INFO_TIME = "info_time"
INFO_EXCHANGE = "info_exchange"
ALERT_TYPES = (
    ALERT_NEWS,
    ALERT_SUNNY_DAY,
    ALERT_SUNNY_LIST,
    ALERT_MIRACLE_TIME,
    ALERT_CASH_TRANSFER,
    ALERT_CUBE_SALE,
    ALERT_URSUS,
    ALERT_SERVER,
    ALERT_EXCHANGE_LOG,
    INFO_TIME,
    INFO_EXCHANGE,
)

ITEM_CATEGORY_NAMES = {
    "Accessory": "장신구",
    "Cap": "모자",
    "Cape": "망토",
    "Coat": "상의",
    "Face": "성형",
    "Glove": "장갑",
    "Hair": "헤어",
    "Longcoat": "한벌옷",
    "Pants": "하의",
    "PetEquip": "펫장비",
    "Ring": "반지",
    "Shield": "방패·보조무기",
    "Shoes": "신발",
    "Taming": "라이딩",
    "Weapon": "무기",
}
APPEARANCE_CATEGORIES = ("Hair", "Face")


def load_cash_items(path: Path = ITEM_DATA_PATH) -> list[dict[str, str]]:
    """WZ 파일에서 미리 추출한 영문·한글 캐시 아이템 목록을 읽습니다."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as item_file:
        return list(csv.DictReader(item_file, delimiter="\t"))


CASH_ITEMS = load_cash_items()
CASH_ITEMS_BY_ID = {item["id"]: item for item in CASH_ITEMS}
CASH_ITEMS_BY_GMS_NAME = {}
for item in CASH_ITEMS:
    if item["category"] in APPEARANCE_CATEGORIES:
        continue
    key = item["gms_name"].casefold()
    current = CASH_ITEMS_BY_GMS_NAME.get(key)
    if current is None or (item["icon"] and not current["icon"]):
        CASH_ITEMS_BY_GMS_NAME[key] = item


def pssb_cash_item(name: str) -> dict[str, str] | None:
    """공식 PSSB 이름과 같은 GMS 아이템을 찾습니다."""
    for part in name.split(" / "):
        # 공식 표의 성별 표시만 제거합니다. 비슷한 다른 이름을 억지로 연결하지 않습니다.
        normalized_name = re.sub(r"\s+\([MF]\)$", "", part.strip(), flags=re.IGNORECASE)
        item = CASH_ITEMS_BY_GMS_NAME.get(normalized_name.casefold())
        if item:
            return item
    return None


def pssb_slot_path(rate: float) -> Path:
    """PSSB 결과 확률에 맞는 실제 GMS 슬롯 이미지를 반환합니다."""
    return (
        PSSB_ADVANCED_SLOT_PATH
        if rate <= PSSB_ADVANCED_RATE_THRESHOLD
        else PSSB_COMMON_SLOT_PATH
    )


def format_pssb_result(index: int, name: str, rate: float) -> str:
    """Discord가 부분 글자색을 지원하지 않아 희귀 보상은 반짝이로 구분합니다."""
    rarity = "✨ " if rate <= PSSB_ADVANCED_RATE_THRESHOLD else ""
    return f"**{index}.** {rarity}**{name}**　`{rate:.2f}%`"


def create_pssb_result_image(results: list[tuple[str, float]]) -> io.BytesIO:
    """실제 GMS SSB 화면 리소스에 추첨 아이콘을 합쳐 PNG 한 장을 만듭니다."""
    # 1회는 아이템을 크게, 5회는 실제 게임처럼 다섯 슬롯을 가로로 배치합니다.
    width, height = (664, 591) if len(results) == 1 else (664, 336)
    slot_size = 150 if len(results) == 1 else 98
    gap = 14
    canvas = Image.new("RGBA", (width, height), (24, 40, 48, 255))

    # GMS 클라이언트에서 추출한 흰 광원 효과를 어두운 배경 위에 먼저 올립니다.
    with Image.open(PSSB_BACK_EFFECT_PATH) as source:
        effect = source.convert("RGBA").resize(
            (width, height), Image.Resampling.LANCZOS
        )
    canvas.alpha_composite(effect)

    # 압축 파일은 한 번만 열어 다섯 결과의 아이콘을 모두 읽습니다.
    icon_data_by_name: dict[str, bytes] = {}
    if ITEM_ICON_ARCHIVE_PATH.exists():
        with zipfile.ZipFile(ITEM_ICON_ARCHIVE_PATH) as archive:
            for name, _ in results:
                item = pssb_cash_item(name)
                icon_name = item.get("icon") if item else None
                if not icon_name:
                    continue
                try:
                    icon_data_by_name[name] = archive.read(icon_name)
                except KeyError:
                    logging.warning("PSSB item icon is missing from archive: %s", icon_name)

    total_width = len(results) * slot_size + (len(results) - 1) * gap
    start_x = (width - total_width) // 2
    slot_y = (height - slot_size) // 2
    for index, (name, rate) in enumerate(results):
        # 실제 화면을 기준으로 2% 이하는 보라색, 2% 초과는 회색 슬롯으로 표시합니다.
        slot_path = pssb_slot_path(rate)
        with Image.open(slot_path) as source:
            slot = source.convert("RGBA").resize(
                (slot_size, slot_size), Image.Resampling.LANCZOS
            )
        slot_x = start_x + index * (slot_size + gap)
        canvas.alpha_composite(slot, (slot_x, slot_y))

        icon_data = icon_data_by_name.get(name)
        if not icon_data:
            continue
        with Image.open(io.BytesIO(icon_data)) as source:
            icon = source.convert("RGBA")
        max_icon_size = int(slot_size * 0.62)
        scale = min(max_icon_size / icon.width, max_icon_size / icon.height)
        icon = icon.resize(
            (max(1, round(icon.width * scale)), max(1, round(icon.height * scale))),
            Image.Resampling.NEAREST if scale >= 1 else Image.Resampling.LANCZOS,
        )
        icon_x = slot_x + (slot_size - icon.width) // 2
        icon_y = slot_y + (slot_size - icon.height) // 2
        canvas.alpha_composite(icon, (icon_x, icon_y))

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def create_familiar_result_image(first_line: str, second_line: str) -> io.BytesIO:
    """실제 GMS 퍼밀리어 UI에 Sherbet과 잠재능력 두 줄을 합칩니다."""
    scale = 2

    def scaled_asset(name: str) -> Image.Image:
        with Image.open(FAMILIAR_ASSET_PATHS[name]) as source:
            image = source.convert("RGBA")
        return image.resize(
            (image.width * scale, image.height * scale), Image.Resampling.NEAREST
        )

    canvas = scaled_asset("back")

    # 카드의 고정 UI 조각은 GMS 클라이언트의 실제 좌표에 맞춰 배치합니다.
    for name, position in (
        ("name", (14, 15)),
        ("scene", (13, 53)),
        ("spec", (31, 229)),
        ("edit", (310, 20)),
        ("lock", (331, 18)),
    ):
        canvas.alpha_composite(
            scaled_asset(name), (position[0] * scale, position[1] * scale)
        )

    # Sherbet은 작은 픽셀 스프라이트이므로 흐려지지 않게 정수 배율로 확대합니다.
    with Image.open(FAMILIAR_ASSET_PATHS["sherbet"]) as source:
        sherbet = source.convert("RGBA")
    sherbet = sherbet.resize(
        (sherbet.width * 5 * scale, sherbet.height * 5 * scale),
        Image.Resampling.NEAREST,
    )
    canvas.alpha_composite(
        sherbet,
        ((183 * scale - sherbet.width // 2), (219 * scale - sherbet.height)),
    )

    draw = ImageDraw.Draw(canvas)

    # 유니크 카드의 바깥 프레임은 상단 이름표와 같은 실제 UI 색상을 사용합니다.
    draw.rounded_rectangle(
        (2 * scale, 2 * scale, canvas.width - 3 * scale, canvas.height - 3 * scale),
        radius=12 * scale,
        outline=(149, 69, 6, 255),
        width=5 * scale,
    )

    def font(size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(FAMILIAR_ASSET_PATHS["font"]), size * scale)

    def fitting_font(text: str, size: int = 11) -> ImageFont.FreeTypeFont:
        # 긴 회복 옵션도 잠재능력 칸 한 줄 안에 들어가도록 필요한 경우에만 줄입니다.
        while size > 8:
            selected = font(size)
            if draw.textbbox((0, 0), text, font=selected)[2] <= 320 * scale:
                return selected
            size -= 1
        return font(size)

    pale_text = (239, 222, 189, 255)
    white_text = (245, 245, 245, 255)
    draw.text(
        (28 * scale, 20 * scale),
        "Sherbet",
        font=font(13),
        fill=white_text,
    )
    draw.text(
        (23 * scale, 63 * scale),
        "Breakthrough Available",
        font=font(10),
        fill=(180, 160, 134, 255),
    )
    draw.text((52 * scale, 233 * scale), "1", font=font(10), fill=pale_text)
    draw.text((23 * scale, 280 * scale), first_line, font=fitting_font(first_line), fill=white_text)
    draw.text((23 * scale, 299 * scale), second_line, font=fitting_font(second_line), fill=white_text)
    draw.text(
        (23 * scale, 320 * scale),
        "Switch Potential Info with Interact/Harvest",
        font=font(8),
        fill=white_text,
    )

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def search_cash_items(
    query: str, limit: int = 25, category: str | None = None
) -> list[dict[str, str]]:
    """영문명·한글명·아이템 ID에서 검색어와 가까운 항목부터 찾습니다."""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return []

    def match_rank(item: dict[str, str]) -> tuple[int, str]:
        values = (item["id"].casefold(), item["gms_name"].casefold(), item["kms_name"].casefold())
        if normalized_query in values:
            rank = 0
        elif any(value.startswith(normalized_query) for value in values):
            rank = 1
        else:
            rank = 2
        return rank, item["gms_name"].casefold()

    matches = [
        item
        for item in CASH_ITEMS
        if (category is None or item["category"] == category)
        and (
            normalized_query in item["id"].casefold()
            or normalized_query in item["gms_name"].casefold()
            or normalized_query in item["kms_name"].casefold()
        )
    ]
    return sorted(matches, key=match_rank)[:limit]

# Discord 애플리케이션에 등록한 HEXA 계산기용 일반 이모지입니다.
HEXA_EMOJI = "<:HEXA:1534436226751529031>"
SOL_ERDA_EMOJI = "<:SolErda:1534436216139944108>"
FRAGMENT_EMOJI = "<:Fragment:1534436205796790324>"
ANIMATED_TWINKLE_EMOJI = "<a:Animated_Twinkle:1534436193276792873>"
EGP_EMOJI = "<:EGP:1536685490789679104>"
LADY_BLAIR_EMOJI = "<:ladyblair:1536691685017518110>"
BONUS_CUBE_EMOJI = "<:BC:1536697424251330630>"
PSSB_EMOJI = "<:SSB:1536697384011178015>"
GROWTH_POTION_EMOJIS = {
    "익성비 · 익스트림 성장의 비약": EGP_EMOJI,
    "궁성비 · 궁극의 유니온 성장의 비약": "<:UGP:1536686894434488471>",
    "극성비 · 극한 성장의 비약": "<:MGP:1536686939049168967>",
    "초성비 · 초월 성장의 비약": "<:TGP:1536686905238749245>",
    "전성비 · 전설 성장의 비약": "<:LGP:1536686920707342407>",
}
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
    "Emblem, Mechanical Heart, Ring, Accessory, Shoulder Accessory": "엠블렘, 기계 심장, 반지, 장신구, 어깨장식",
    "Weapon, Secondary Weapon, Shield": "무기, 보조무기, 방패",
    "Top, Bottom, Outfit, Cape": "상의, 하의, 한벌옷, 망토",
    "Hat": "모자",
    "Gloves": "장갑",
    "Shoes": "신발",
}

def watched_posts(posts: list[dict]) -> list[dict]:
    # 메이플 공식 API가 준 모든 글에서, 봇이 알릴 카테고리만 남깁니다.
    return [post for post in posts if post.get("category") in WATCHED_CATEGORIES]


def format_time_channel_name(now: datetime) -> str:
    """한국 현재 시각을 직전 5분 단위의 채널 이름으로 만듭니다."""
    local_now = now.astimezone(INFO_CHANNEL_TIMEZONE)
    display_minute = local_now.minute - local_now.minute % 5
    return (
        f"{local_now.month:02d}월 {local_now.day:02d}일 "
        f"{local_now.hour:02d}시 {display_minute:02d}분"
    )


def parse_usd_exchange_rate(source: str) -> Decimal:
    """네이버 금융 환율표에서 미국 USD의 매매기준율을 꺼냅니다."""
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", source, re.IGNORECASE | re.DOTALL):
        text = html.unescape(re.sub(r"<[^>]+>", " ", row))
        if not re.search(r"미국\s*USD", text):
            continue
        match = re.search(
            r'<td\b[^>]*class=["\'][^"\']*\bsale\b[^"\']*["\'][^>]*>'
            r"\s*([\d,.]+)",
            row,
            re.IGNORECASE,
        )
        if match:
            return Decimal(match.group(1).replace(",", ""))
    raise ValueError("Naver USD/KRW exchange rate is missing")


def format_exchange_channel_name(rate: Decimal) -> str:
    """USD/KRW 환율을 음성 채널 이름 형식으로 만듭니다."""
    return f"USD-{rate:,.2f}"


def record_exchange_rate(
    exchange_log: dict | None,
    rate: Decimal,
    now: datetime,
) -> tuple[dict, bool]:
    """한국시간 날짜별 환율 변동을 최근 5개까지만 기록합니다."""
    local_now = now.astimezone(INFO_CHANNEL_TIMEZONE)
    date_key = local_now.date().isoformat()
    time_text = f"{local_now.hour:02d}:{local_now.minute - local_now.minute % 10:02d}"
    rate_text = f"{rate:.2f}"
    if exchange_log is None or exchange_log.get("date") != date_key:
        return {
            "date": date_key,
            "opening_rate": rate_text,
            "current_rate": rate_text,
            "entries": [{"time": time_text, "rate": rate_text, "change": None}],
            "message_ids": {},
        }, True

    previous_rate = Decimal(exchange_log["current_rate"])
    if rate == previous_rate:
        return exchange_log, False

    exchange_log["current_rate"] = rate_text
    exchange_log["entries"].append(
        {
            "time": time_text,
            "rate": rate_text,
            "change": f"{rate - previous_rate:.2f}",
        }
    )
    exchange_log["entries"] = exchange_log["entries"][-5:]
    return exchange_log, True


def exchange_change_text(change: Decimal) -> str:
    """Discord에서 안정적으로 보이는 이모지로 상승·하락을 구분합니다."""
    if change > 0:
        return f"🔴 ▲ {change:,.2f}원"
    if change < 0:
        return f"🔵 ▼ {abs(change):,.2f}원"
    return "⚪ ─ 0.00원"


def build_exchange_rate_log_embed(exchange_log: dict) -> discord.Embed:
    """하루 환율의 최근 변동 5개와 시가 대비 변동을 표시합니다."""
    date_value = datetime.strptime(exchange_log["date"], "%Y-%m-%d")
    lines = []
    for entry in exchange_log["entries"]:
        suffix = (
            "(시작)"
            if entry["change"] is None
            else exchange_change_text(Decimal(entry["change"]))
        )
        lines.append(
            f"`{entry['time']}`  **{Decimal(entry['rate']):,.2f}원**  {suffix}"
        )

    current_rate = Decimal(exchange_log["current_rate"])
    daily_change = current_rate - Decimal(exchange_log["opening_rate"])
    description = "\n".join(lines)
    description += (
        f"\n\n**현재 환율:** {current_rate:,.2f}원"
        f"\n**오늘 변동:** {exchange_change_text(daily_change)}"
    )
    return discord.Embed(
        title=(
            f"[ {date_value.year}년 {date_value.month}월 {date_value.day}일 "
            "환율 변동 기록 ]"
        ),
        description=description,
        color=0x5865F2,
    )


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
            ALERT_URSUS: set(),
            ALERT_SERVER: set(),
            ALERT_EXCHANGE_LOG: set(),
            INFO_TIME: set(),
            INFO_EXCHANGE: set(),
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


def parse_server_status(payload: dict) -> dict[str, bool]:
    """넥슨 서버 상태 응답에서 주요 4개 월드의 접속 가능 여부만 꺼냅니다."""
    servers = {
        server.get("worldName"): server for server in payload.get("servers", [])
    }
    statuses = {}
    for world in MAIN_WORLDS:
        server = servers.get(world)
        if server is None:
            # 점검 중에는 공식 API가 월드 목록을 비워서 보냅니다.
            # 목록에 없는 월드는 접속 불가로 처리해야 서버 오픈 전환을 감지할 수 있습니다.
            statuses[world] = False
            continue
        login_servers = [
            value
            for key, value in server.items()
            if key.startswith("Login") and value not in {None, -1}
        ]
        game_channels = [
            value
            for key, value in server.items()
            if key.startswith("Game") and value not in {None, -1}
        ]
        if not login_servers or not game_channels:
            # 점검 화면처럼 월드 정보가 불완전한 경우도 접속 불가로 봅니다.
            statuses[world] = False
            continue
        statuses[world] = all(value == 1 for value in login_servers) and any(
            value == 1 for value in game_channels
        )
    return statuses


def is_server_maintenance_post(post: dict) -> bool:
    """일반 공지 중 실제 GMS 게임 서버 점검 글만 고릅니다."""
    title = post.get("name", "").lower()
    return (
        post.get("category") == "maintenance"
        and not post.get("isMSCW", False)
        and any(
            keyword in title
            for keyword in ("maintenance", "scheduled game update", "scheduled minor patch")
        )
        and "channel maintenance" not in title
        and "cash shop maintenance" not in title
    )


def _maintenance_datetime(date_text: str, time_text: str, utc_offset: int) -> datetime:
    """공식 공지의 영문 날짜와 시각을 UTC 시각으로 바꿉니다."""
    normalized_date = re.sub(r"^[A-Z][a-z]+,\s*", "", date_text.strip())
    normalized_date = re.sub(r"(\d{1,2}),?\s+(\d{4})$", r"\1, \2", normalized_date)
    local_time = datetime.strptime(
        f"{normalized_date} {time_text.strip()}", "%B %d, %Y %I:%M %p"
    )
    return local_time.replace(tzinfo=timezone(timedelta(hours=utc_offset))).astimezone(
        timezone.utc
    )


def extract_maintenance_watch(post: dict, source: str) -> dict | None:
    """점검 본문에서 자동 서버 확인에 필요한 시작·종료 시각을 꺼냅니다."""
    if not is_server_maintenance_post(post):
        return None

    text = html_to_text(source)
    title = post.get("name", "")
    # 봇이 처음 본 시점에 이미 완료된 공지는 새 감시 대상으로 등록하지 않습니다.
    if "[completed]" in title.lower() or "maintenance has been completed" in text.lower():
        return None

    row = re.search(
        r"(?P<date>(?:[A-Z][a-z]+,\s*)?[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})"
        r".*?P(?:D|S)T\s*\(UTC\s*(?P<offset>[+-]\s*\d{1,2})\)\s*:"
        r"\s*(?P<start>\d{1,2}:\d{2}\s+[AP]M)\s*-\s*"
        r"(?P<end>\d{1,2}:\d{2}\s+[AP]M)",
        text,
        flags=re.IGNORECASE,
    )
    start_timestamp = None
    end_timestamp = None
    if row is not None:
        offset = int(row.group("offset").replace(" ", ""))
        start = _maintenance_datetime(row.group("date"), row.group("start"), offset)
        end = _maintenance_datetime(row.group("date"), row.group("end"), offset)
        if end <= start:
            end += timedelta(days=1)
        start_timestamp = int(start.timestamp())
        end_timestamp = int(end.timestamp())
    else:
        # 종료 시각이 없는 긴급점검도 명시된 시작 시각이 있으면 함께 저장합니다.
        start_match = re.search(
            r"(?:today,?\s*)?(?P<date>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})"
            r"\s+at\s+(?P<start>\d{1,2}:\d{2}\s+[AP]M)\s+"
            r"(?P<zone>PDT|PST)",
            text,
            flags=re.IGNORECASE,
        )
        if start_match is not None:
            offset = -7 if start_match.group("zone").upper() == "PDT" else -8
            start_timestamp = int(
                _maintenance_datetime(
                    start_match.group("date"), start_match.group("start"), offset
                ).timestamp()
            )

    if start_timestamp is None:
        start_timestamp = int(
            datetime.fromisoformat(post["liveDate"].replace("Z", "+00:00")).timestamp()
        )
    monitor_from = (
        max(start_timestamp, end_timestamp - 3_600)
        if end_timestamp is not None
        else int(datetime.fromisoformat(post["liveDate"].replace("Z", "+00:00")).timestamp())
    )
    return {
        "post_id": post["id"],
        "title": title,
        "url": post_url(post),
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "monitor_from_timestamp": monitor_from,
        "saw_down": False,
        "completed": False,
    }


def merge_maintenance_watch(current: dict | None, updated: dict) -> dict:
    """같은 점검 공지가 수정되면 새 시간과 기존 확인 기록을 합칩니다."""
    if current is None or current.get("post_id") != updated["post_id"]:
        return updated
    updated["saw_down"] = current.get("saw_down", False)
    updated["completed"] = current.get("completed", False)
    return updated


def should_check_server_status(watch: dict | None, now_timestamp: int) -> bool:
    """점검 감시 시간이 되었고 아직 오픈 확인을 마치지 않았는지 반환합니다."""
    return bool(
        watch
        and not watch.get("completed", False)
        and now_timestamp >= watch["monitor_from_timestamp"]
    )


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


def is_cash_shop_update(post: dict) -> bool:
    # sale 카테고리의 일반 판매 글이 최신 캐시샵 링크를 덮어쓰지 않게 제목도 함께 확인합니다.
    return (
        post.get("category") == "sale"
        and "cash shop update" in post.get("name", "").lower()
    )


def extract_cash_shop_sections(source: str) -> list[str]:
    """최신 캐시샵 공지에서 이번에 새로 추가된 상위 항목만 꺼냅니다."""
    sections = []
    for heading in re.findall(r"<h1\b[^>]*>(.*?)</h1>", source, re.IGNORECASE | re.DOTALL):
        name = html.unescape(html_to_text(heading))
        normalized_name = name.upper()
        if normalized_name == "ONGOING SALES":
            break
        if normalized_name not in {"DAILY DEALS", "SALES ENDING THIS WEEK"}:
            sections.append(name)
    return sections[:5]


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


def ursus_daily_windows(
    now: datetime | None = None,
) -> list[tuple[datetime, datetime]]:
    """미국 서부의 서머타임 여부에 맞는 오늘의 우르스 골든타임을 반환합니다."""
    if now is None:
        now = datetime.now(timezone.utc)
    local_now = now.astimezone(URSUS_TIMEZONE)
    # 서머타임 전환일 새벽에도 그날 낮의 실제 시간표를 보여 주도록 정오를 기준으로 판별합니다.
    local_noon = datetime(
        local_now.year, local_now.month, local_now.day, 12, tzinfo=URSUS_TIMEZONE
    )
    # 공식 골든타임은 UTC 18:00~22:00, 다음 날 01:00~05:00로 고정되어 있습니다.
    hours = (
        ((11, 15), (18, 22))
        if local_noon.dst() != timedelta(0)
        else ((10, 14), (17, 21))
    )
    return [
        (
            local_now.replace(hour=start_hour, minute=0, second=0, microsecond=0),
            local_now.replace(hour=end_hour, minute=0, second=0, microsecond=0),
        )
        for start_hour, end_hour in hours
    ]


def current_ursus_window(
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """현재 우르스 골든타임이면 해당 시작·종료 시각을 반환합니다."""
    if now is None:
        now = datetime.now(timezone.utc)
    local_now = now.astimezone(URSUS_TIMEZONE)
    return next(
        (
            (start, end)
            for start, end in ursus_daily_windows(now)
            if start <= local_now < end
        ),
        None,
    )


def ursus_boundary_event(
    now: datetime | None = None,
) -> tuple[str, datetime, datetime] | None:
    """골든타임 시작·종료 첫 1분에만 알림 종류와 해당 시간대를 반환합니다."""
    if now is None:
        now = datetime.now(timezone.utc)
    local_now = now.astimezone(URSUS_TIMEZONE)
    if local_now.minute != 0:
        return None
    for start, end in ursus_daily_windows(now):
        if local_now.hour == start.hour:
            return "start", start, end
        if local_now.hour == end.hour:
            return "end", start, end
    return None


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
    embed.set_image(url=f"attachment://{CASH_SHOP_TRANSFER_IMAGE_PATH.name}")
    return embed


def build_ursus_embed(
    status: str,
    window: tuple[datetime, datetime] | None = None,
    now: datetime | None = None,
) -> tuple[discord.Embed, Path]:
    """명령어와 시작·종료 알림이 함께 사용하는 우르스 임베드를 만듭니다."""
    if status == "active":
        message = "**우르스 골든타임이 진행 중입니다.**"
        color = 0x5865F2
        image_path = URSUS_ACTIVE_IMAGE_PATH
    elif status == "ended":
        message = "**우르스 골든타임이 끝났습니다.**"
        color = 0xED4245
        image_path = URSUS_INACTIVE_IMAGE_PATH
    else:
        message = "**현재 우르스 골든타임이 진행 중이지 않습니다.**"
        color = 0x747F8D
        image_path = URSUS_INACTIVE_IMAGE_PATH

    windows = [window] if window is not None else ursus_daily_windows(now)
    schedule = "\n".join(
        f"• __<t:{int(start.timestamp())}:T> ~ <t:{int(end.timestamp())}:T>__"
        for start, end in windows
    )
    embed = discord.Embed(
        title="우르스 골든타임",
        description=f"{message}\n{schedule}",
        color=color,
    )
    embed.set_author(name="MapleStory | URSUS")
    embed.set_image(url=f"attachment://{image_path.name}")
    return embed, image_path


def build_server_status_embed(
    statuses: dict[str, bool], opened: bool = False
) -> discord.Embed:
    """명령어와 점검 종료 알림에서 같은 주요 월드 상태를 보여줍니다."""
    all_open = all(statuses.values())
    embed = discord.Embed(
        title="메이플스토리 서버 오픈" if opened else "메이플스토리 서버 상태",
        url=SERVER_STATUS_PAGE_URL,
        description=(
            "**주요 월드가 모두 열렸습니다.**"
            if opened
            else "넥슨 공식 서버 상태를 기준으로 확인했습니다."
        ),
        color=0x57F287 if all_open else 0xED4245,
    )
    embed.set_author(name="MapleStory | SERVER STATUS")
    for world in MAIN_WORLDS:
        embed.add_field(
            name=world,
            value="🟢 정상" if statuses[world] else "🔴 점검 중",
            inline=True,
        )
    embed.set_footer(text="Scania · Bera · Kronos · Hyperion 기준")
    return embed


def find_ranking_character(payload: dict, nickname: str) -> dict | None:
    """공식 랭킹 응답에서 입력한 닉네임과 정확히 같은 캐릭터만 찾습니다."""
    for character in payload.get("ranks", []):
        if character.get("characterName", "").casefold() == nickname.casefold():
            result = dict(character)
            result["totalCount"] = payload.get("totalCount")
            return result
    return None


def compact_exp(value: int) -> str:
    """그래프 수치를 게임에서 익숙한 K·M·B·T·Q 단위로 줄입니다."""
    for unit, size in (("Q", 10**15), ("T", 10**12), ("B", 10**9), ("M", 10**6), ("K", 10**3)):
        if value >= size:
            return f"{value / size:.2f}".rstrip("0").rstrip(".") + unit
    return str(value)


def ranking_axis_scale(maximum: int) -> tuple[int, int]:
    """경험치 그래프에 약 8칸이 보이도록 보기 좋은 눈금과 상한을 계산합니다."""
    raw_step = max(maximum, 1) / 8
    magnitude = 10 ** math.floor(math.log10(raw_step))
    candidates = [max(1, round(unit * magnitude)) for unit in (1, 2, 5, 10)]
    step = min(candidates, key=lambda value: abs(math.ceil(maximum / value) - 8))
    return step, math.ceil(maximum / step) * step


def summarize_exp_gains(gains: list[dict], period: int) -> tuple[int, int]:
    """최근 기간의 일평균과 누적 획득 경험치를 반환합니다."""
    recent = gains[-period:]
    total = sum(item["exp"] for item in recent)
    return (round(total / len(recent)) if recent else 0), total


def create_ranking_history_image(
    character: dict,
    gains: list[dict],
    world_rank: int | None = None,
    legion: dict | None = None,
    achievement: dict | None = None,
    world_total_count: int | None = None,
    character_image: bytes | None = None,
) -> io.BytesIO:
    """캐릭터 랭킹 정보와 최근 경험치 변화량을 한 장의 PNG로 만듭니다."""
    scale = 2
    width, height = 900, 740
    image = Image.new("RGB", (width * scale, height * scale), "#202830")
    draw = ImageDraw.Draw(image, "RGBA")
    font_path = str(FAMILIAR_ASSET_PATHS["font"])
    title_font = ImageFont.truetype(font_path, 28 * scale)
    score_font = ImageFont.truetype(font_path, 20 * scale)
    body_font = ImageFont.truetype(font_path, 15 * scale)
    value_font = ImageFont.truetype(font_path, 13 * scale)
    small_font = ImageFont.truetype(font_path, 12 * scale)

    draw.rounded_rectangle(
        (16 * scale, 16 * scale, (width - 16) * scale, (height - 16) * scale),
        radius=20 * scale,
        fill="#29333E",
        outline="#405361",
        width=2 * scale,
    )

    character_name = character["characterName"]
    level = character["level"]
    current_exp = character.get("exp", 0)
    world = RANKING_WORLDS.get(character["worldID"], f"월드 ID {character['worldID']}")
    level_progress = 0.0
    if 200 <= level < 300:
        level_progress = min(
            1.0,
            float(Decimal(current_exp) / Decimal(LEVEL_EXP[level - 200])),
        )
    level_text = f"Lv. {level}"
    score, title = maple_addict_power(
        {
            "level": level,
            "exp": current_exp,
            "ranking": character["rank"],
            "legion_level": legion.get("legionLevel") if legion else None,
            "legion_rank": legion.get("rank") if legion else None,
            "achievement_score": achievement.get("score") if achievement else None,
            "achievement_rank": achievement.get("rank") if achievement else None,
        }
    )

    draw.rounded_rectangle(
        (42 * scale, 42 * scale, 166 * scale, 176 * scale),
        radius=14 * scale,
        fill="#202830",
        outline="#405361",
        width=2 * scale,
    )
    if character_image:
        try:
            with Image.open(io.BytesIO(character_image)) as source:
                avatar = source.convert("RGBA")
                visible_area = avatar.getchannel("A").point(
                    lambda alpha: 255 if alpha >= 64 else 0
                ).getbbox()
                if visible_area:
                    avatar = avatar.crop(visible_area)
                avatar_scale = min(
                    (116 * scale) / avatar.width,
                    (126 * scale) / avatar.height,
                )
                avatar = avatar.resize(
                    (
                        max(1, round(avatar.width * avatar_scale)),
                        max(1, round(avatar.height * avatar_scale)),
                    ),
                    Image.Resampling.NEAREST
                    if avatar_scale >= 1
                    else Image.Resampling.LANCZOS,
                )
                avatar_x = (104 * scale) - avatar.width // 2
                avatar_y = (109 * scale) - avatar.height // 2
                image.paste(avatar, (avatar_x, avatar_y), avatar)
        except (OSError, ValueError):
            logging.warning("Failed to render ranking character image for %s.", character_name)

    draw.text((190 * scale, 42 * scale), character_name, font=title_font, fill="#E6FF00")
    draw.text(
        (190 * scale, 84 * scale),
        f"{level_text}  ·  {character['jobName']}  ·  {world}",
        font=body_font,
        fill="#EEF4F8",
    )
    draw.rounded_rectangle(
        (190 * scale, 108 * scale, 820 * scale, 136 * scale),
        radius=8 * scale,
        fill="#1A222B",
    )
    draw.rounded_rectangle(
        (190 * scale, 108 * scale, (190 + 630 * level_progress) * scale, 136 * scale),
        radius=8 * scale,
        fill="#6EB6D9",
    )
    if 200 <= level < 300:
        progress_text = (
            f"{level_progress * 100:.3f}%  "
            f"({compact_exp(current_exp)} / {compact_exp(LEVEL_EXP[level - 200])})"
        )
        draw.text(
            (505 * scale, 122 * scale),
            progress_text,
            font=value_font,
            fill="#F8FBFD",
            anchor="mm",
            stroke_width=2 * scale,
            stroke_fill="#202830",
        )
    draw.rounded_rectangle(
        (190 * scale, 148 * scale, 548 * scale, 184 * scale),
        radius=10 * scale,
        fill="#34404D",
        outline="#526878",
        width=1 * scale,
    )
    draw.text(
        (207 * scale, 159 * scale),
        "메창력",
        font=value_font,
        fill="#9FB0BE",
    )
    draw.text(
        (267 * scale, 154 * scale),
        f"{score:.2f}",
        font=score_font,
        fill="#E6FF00",
    )
    draw.line(
        (345 * scale, 157 * scale, 345 * scale, 176 * scale),
        fill="#526878",
        width=1 * scale,
    )
    draw.text(
        (362 * scale, 159 * scale),
        "칭호",
        font=value_font,
        fill="#9FB0BE",
    )
    draw.text(
        (407 * scale, 159 * scale),
        title,
        font=value_font,
        fill="#EEF4F8",
    )

    world_rank_text = f"{world_rank:,}위" if world_rank is not None else "확인 불가"
    if (
        world_rank is not None
        and world_total_count
        and world_total_count >= world_rank
    ):
        top_percent = Decimal(world_rank) * 100 / Decimal(world_total_count)
        world_rank_text += f" · 상위 {top_percent:.4f}%"
    stats = (
        ("전체 랭킹", f"{character['rank']:,}위", f"{world} {world_rank_text}"),
        (
            "유니온",
            f"Lv. {legion['legionLevel']:,}" if legion else "확인 불가",
            f"{legion['rank']:,}위" if legion else "순위 확인 불가",
        ),
        (
            "업적",
            f"{achievement['score']:,}점" if achievement else "확인 불가",
            f"{achievement['rank']:,}위" if achievement else "순위 확인 불가",
        ),
    )
    for index, (heading, main_value, detail) in enumerate(stats):
        left = 42 + index * 262
        draw.rounded_rectangle(
            (left * scale, 210 * scale, (left + 246) * scale, 304 * scale),
            radius=12 * scale,
            fill="#34404D",
        )
        draw.text(
            ((left + 16) * scale, 224 * scale),
            heading,
            font=small_font,
            fill="#7FA3BD",
        )
        draw.text(
            ((left + 16) * scale, 247 * scale),
            main_value,
            font=score_font,
            fill="#EEF4F8",
        )
        draw.text(
            ((left + 16) * scale, 279 * scale),
            detail,
            font=small_font,
            fill="#A8B5C0",
        )

    draw.rounded_rectangle(
        (42 * scale, 324 * scale, 858 * scale, 420 * scale),
        radius=12 * scale,
        fill="#34404D",
    )
    draw.line(
        (450 * scale, 340 * scale, 450 * scale, 404 * scale),
        fill="#526878",
        width=1 * scale,
    )
    summary_colors = ("#E6FF00", "#6EB6D9", "#EEF4F8")
    for section, heading in enumerate(("일평균 획득 경험치", "누적 획득 경험치")):
        section_left = 58 + section * 408
        draw.text(
            (section_left * scale, 338 * scale),
            heading,
            font=body_font,
            fill="#EEF4F8",
        )
        for index, period in enumerate((7, 14, 30)):
            average, total = summarize_exp_gains(gains, period)
            value = average if section == 0 else total
            display_value = compact_exp(value) if gains else "-"
            value_x = section_left + index * 122
            draw.text(
                (value_x * scale, 371 * scale),
                f"{period}일",
                font=small_font,
                fill="#9FB0BE",
            )
            draw.text(
                ((value_x + 32) * scale, 367 * scale),
                display_value,
                font=score_font,
                fill=summary_colors[index],
            )

    graph_gains = gains[-14:]

    if not graph_gains:
        draw.text(
            (width * scale // 2, 545 * scale),
            "첫 기록을 저장했습니다",
            font=title_font,
            fill="#EEF4F8",
            anchor="mm",
        )
        draw.text(
            (width * scale // 2, 585 * scale),
            "다음 날짜의 수집 기록부터 경험치 변화가 표시됩니다",
            font=body_font,
            fill="#9FB0BE",
            anchor="mm",
        )
    else:
        left, top, right, bottom = 82, 470, 850, 680
        maximum = max(item["exp"] for item in graph_gains) or 1
        tick_step, axis_maximum = ranking_axis_scale(maximum)
        tick_count = axis_maximum // tick_step
        for index in range(tick_count + 1):
            value = axis_maximum - tick_step * index
            y = top + (bottom - top) * index / tick_count
            draw.line(
                (left * scale, y * scale, right * scale, y * scale),
                fill="#3A4857",
                width=1 * scale,
            )
            draw.text(
                ((left - 10) * scale, y * scale),
                compact_exp(value),
                font=small_font,
                fill="#A8B5C0",
                anchor="rm",
            )

        plot_left, plot_right = left + 24, right - 24
        if len(graph_gains) == 1:
            x_positions = [(plot_left + plot_right) / 2]
        else:
            x_positions = [
                plot_left + (plot_right - plot_left) * index / (len(graph_gains) - 1)
                for index in range(len(graph_gains))
            ]
        points = [
            (x, bottom - (bottom - top) * item["exp"] / axis_maximum)
            for x, item in zip(x_positions, graph_gains)
        ]
        area = [(plot_left, bottom), *points, (plot_right, bottom)]
        draw.polygon(
            [(x * scale, y * scale) for x, y in area],
            fill=(92, 156, 189, 78),
        )
        if len(points) > 1:
            draw.line(
                [(x * scale, y * scale) for x, y in points],
                fill="#6EB6D9",
                width=4 * scale,
                joint="curve",
            )
        for (x, y), item in zip(points, graph_gains):
            draw.ellipse(
                (
                    (x - 6) * scale,
                    (y - 6) * scale,
                    (x + 6) * scale,
                    (y + 6) * scale,
                ),
                fill="#DDFE38",
                outline="#F6FFC7",
                width=2 * scale,
            )
            exp_text = compact_exp(item["exp"])
            exp_position = (x * scale, (y - 16) * scale)
            exp_box = draw.textbbox(
                exp_position,
                exp_text,
                font=value_font,
                anchor="ms",
            )
            draw.rounded_rectangle(
                (
                    exp_box[0] - 5 * scale,
                    exp_box[1] - 3 * scale,
                    exp_box[2] + 5 * scale,
                    exp_box[3] + 3 * scale,
                ),
                radius=5 * scale,
                fill="#1A222B",
                outline="#526878",
                width=1 * scale,
            )
            draw.text(
                exp_position,
                exp_text,
                font=value_font,
                fill="#F8FBFD",
                anchor="ms",
            )
            draw.text(
                (x * scale, (bottom + 14) * scale),
                item["date"][5:].replace("-", "/"),
                font=small_font,
                fill="#A8B5C0",
                anchor="ma",
            )

    image = image.resize((width, height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


def build_ranking_embed(
    character: dict,
    world_rank: int | None,
    legion: dict | None,
    achievement: dict | None = None,
    world_total_count: int | None = None,
) -> discord.Embed:
    """공식 랭킹에서 확인한 캐릭터 정보를 Discord 한 화면으로 정리합니다."""
    level = character["level"]
    current_exp = character.get("exp", 0)
    world = RANKING_WORLDS.get(character["worldID"], f"월드 ID {character['worldID']}")
    level_text = f"Lv. {level}"
    if 200 <= level < 300:
        required_exp = LEVEL_EXP[level - 200]
        progress = Decimal(current_exp) * 100 / Decimal(required_exp)
        level_text += f" ({progress:.3f}%)"

    embed = discord.Embed(
        title=character["characterName"],
        description=f"**{level_text}** · {character['jobName']} · {world}",
        color=0x5865F2,
    )
    embed.set_author(name="MapleStory | CHARACTER RANKING")
    embed.add_field(name="전체 순위", value=f"{character['rank']:,}위")
    world_rank_text = f"{world_rank:,}위" if world_rank is not None else "확인 불가"
    if (
        world_rank is not None
        and world_total_count
        and world_total_count >= world_rank
    ):
        top_percent = Decimal(world_rank) * 100 / Decimal(world_total_count)
        world_rank_text += f" (상위 {top_percent:.4f}%)"
    embed.add_field(name="월드 랭킹", value=world_rank_text)
    if legion is not None:
        embed.add_field(name="유니온 레벨", value=f"{legion['legionLevel']:,}")
        embed.add_field(name="유니온 순위", value=f"{legion['rank']:,}위")
    if achievement is not None:
        embed.add_field(name="업적 점수", value=f"{achievement['score']:,}")
        embed.add_field(name="업적 순위", value=f"{achievement['rank']:,}위")
    if character.get("characterImgURL"):
        embed.set_thumbnail(url=character["characterImgURL"])
    embed.set_footer(text="Nexon 공식 GMS 랭킹 기준")
    return embed


def ranking_progress_percent(level: int, exp: int) -> float:
    """레벨과 현재 경험치를 200~300 구간의 0~100 성장률로 바꿉니다."""
    if level >= 300:
        return 100.0
    if level < 200:
        return 0.0
    required_exp = LEVEL_EXP[level - 200]
    return min(100.0, (level - 200) + float(Decimal(exp) / Decimal(required_exp)))


def ranking_position_score(rank: int | None) -> float:
    """랭킹 1위는 100점, 뒤로 갈수록 완만히 낮아지는 순위 점수입니다."""
    if rank is None or rank < 1:
        return 0.0
    return 100 / (1 + math.log10(rank) / 4)


def maple_addict_power(entry: dict) -> tuple[float, str]:
    """레벨·유니온·업적의 수치와 순위를 합쳐 재미용 메창력과 칭호를 만듭니다."""
    # 레벨·경험치와 전체 순위 40점, 유니온 30점, 업적 30점입니다.
    character_score = (
        ranking_progress_percent(entry["level"], entry["exp"]) * 0.20
        + ranking_position_score(entry["ranking"]) * 0.20
    )
    union_score = (
        min(entry.get("legion_level") or 0, 12_000) / 12_000 * 15
        + ranking_position_score(entry.get("legion_rank")) * 0.15
    )
    achievement_score = (
        min(entry.get("achievement_score") or 0, 30_000) / 30_000 * 15
        + ranking_position_score(entry.get("achievement_rank")) * 0.15
    )
    all_first = all(
        entry.get(key) == 1
        for key in ("ranking", "legion_rank", "achievement_rank")
    )
    score = 99.9 if all_first else min(99.8, character_score + union_score + achievement_score)

    highest = max(
        (character_score, "레벨 장인"),
        (union_score, "유니온 장인"),
        (achievement_score, "업적 사냥꾼"),
    )
    if score >= 95:
        title = "초월 메창"
    elif score >= 80:
        title = "메창"
    elif max(character_score, union_score, achievement_score) - min(
        character_score, union_score, achievement_score
    ) <= 3:
        title = "밸런스 메창"
    else:
        title = highest[1]
    return round(score, 2), title


def build_guild_ranking_embed(
    entries: list[dict], target_nickname: str | None = None
) -> discord.Embed:
    """한 Discord 서버에 직접 등록한 캐릭터만 메창력 순으로 보여줍니다."""
    ranked = []
    for entry in entries:
        if entry["level"] is not None:
            score, title = maple_addict_power(entry)
            ranked.append((score, title, entry))
    ranked.sort(key=lambda item: (-item[0], -item[2]["level"], -item[2]["exp"], item[2]["ranking"]))

    embed = discord.Embed(
        title="서버 메창력 랭킹",
        description="등록한 캐릭터의 마지막 `/랭킹` 조회 기준입니다.",
        color=0xF1C40F,
    )
    start = 0
    visible_ranked = ranked[:20]
    target_key = target_nickname.strip().casefold() if target_nickname else None
    if target_key:
        target_index = next(
            (
                index
                for index, (_, _, entry) in enumerate(ranked)
                if entry["character_name"].casefold() == target_key
            ),
            None,
        )
        if target_index is None:
            embed.description = (
                f"**{discord.utils.escape_markdown(target_nickname.strip())}** 캐릭터가 "
                "이 서버 랭킹에 없거나 랭킹 정보를 다시 조회해야 합니다."
            )
            return embed
        # 찾은 캐릭터를 가운데에 두고 위아래 순위를 최대 5명씩 보여줍니다.
        start = max(0, target_index - 5)
        visible_ranked = ranked[start : target_index + 6]
        embed.description = (
            f"**{discord.utils.escape_markdown(ranked[target_index][2]['character_name'])}** 기준 "
            "위·아래 최대 5명입니다."
        )

    for position, (score, title, entry) in enumerate(visible_ranked, start=start + 1):
        exp_text = ""
        if 200 <= entry["level"] < 300:
            exp_text = f" ({ranking_progress_percent(entry['level'], entry['exp']) % 1 * 100:.3f}%)"
        union_text = f"{entry['legion_level']:,}" if entry["legion_level"] else "확인 불가"
        achievement_text = (
            f"{entry['achievement_score']:,}" if entry["achievement_score"] else "확인 불가"
        )
        marker = "👉 " if target_key == entry["character_name"].casefold() else ""
        embed.add_field(
            name=f"{marker}{position}. {entry['discord_display_name']} ({entry['character_name']})",
            value=(
                f"Lv. {entry['level']}{exp_text} · 전체 {entry['ranking']:,}위\n"
                f"유니온 {union_text} · 업적 {achievement_text}\n"
                f"**메창력 {score:.2f} · {title}**"
            ),
            inline=False,
        )
    if not ranked:
        embed.description = "등록된 캐릭터의 랭킹 정보가 없습니다. 먼저 `/랭킹` 후 `/랭킹등록`을 실행해주세요."
    elif len(entries) > len(ranked):
        embed.set_footer(text="일부 등록 캐릭터는 랭킹 정보를 다시 조회해야 합니다.")
    return embed


def simulate_seed_ring(level: int, stone_count: int, roll: int | None = None) -> dict:
    """리스트레인트 링을 선택한 연마석 개수로 한 번 강화합니다."""
    if level not in SEED_RING_LEVELS:
        raise ValueError("현재 레벨은 4 또는 5여야 합니다.")
    if not 1 <= stone_count <= 5:
        raise ValueError("연마석은 1~5개를 넣어야 합니다.")
    setting = SEED_RING_LEVELS[level]
    success_rate = setting["rate_per_stone"] * stone_count
    rolled_number = roll if roll is not None else random.randint(1, 100)
    if not 1 <= rolled_number <= 100:
        raise ValueError("추첨값은 1~100이어야 합니다.")
    return {
        "level": level,
        "target_level": level + 1,
        "stone": setting["stone"],
        "stone_count": stone_count,
        "success_rate": success_rate,
        "success": rolled_number <= success_rate,
    }


def build_seed_ring_embed(result: dict, attempts: int, successes: int) -> discord.Embed:
    """한 번의 강화 결과와 현재 버튼 세션 누계를 보여줍니다."""
    success = result["success"]
    outcome = (
        f"✅ **강화에 성공했습니다!**\n리스트레인트 링 Lv.{result['target_level']} 달성"
        if success
        else f"❌ **강화에 실패했습니다.**\n리스트레인트 링 Lv.{result['level']} 유지"
    )
    embed = discord.Embed(
        title="💍 리스트레인트 링 강화 시뮬레이터",
        description=(
            f"**Lv.{result['level']} → Lv.{result['target_level']}**\n"
            f"{result['stone']}: **{result['stone_count']}개**\n"
            f"성공 확률: **{result['success_rate']}%**\n\n{outcome}"
        ),
        color=0x57F287 if success else 0xED4245,
    )
    embed.set_author(name="MapleStory | SEED RING")
    embed.add_field(name="시도 횟수", value=f"{attempts}회")
    embed.add_field(name="성공 / 실패", value=f"{successes}회 / {attempts - successes}회")
    embed.add_field(
        name="누적 사용 연마석",
        value=f"{attempts * result['stone_count']}개",
    )
    return embed


class UserOwnedView(discord.ui.View):
    """명령어를 실행한 사용자만 버튼을 누를 수 있는 공통 View입니다."""

    def __init__(self, user_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "이 버튼은 명령어를 실행한 사용자만 누를 수 있습니다.", ephemeral=True
        )
        return False


class SeedRingSimulatorView(UserOwnedView):
    """처음 선택한 조건으로 같은 메시지에서 계속 독립 추첨합니다."""

    def __init__(self, user_id: int, level: int, stone_count: int) -> None:
        super().__init__(user_id)
        self.level = level
        self.stone_count = stone_count
        self.attempts = 0
        self.successes = 0

    def draw(self) -> discord.Embed:
        result = simulate_seed_ring(self.level, self.stone_count)
        self.attempts += 1
        self.successes += int(result["success"])
        return build_seed_ring_embed(result, self.attempts, self.successes)

    @discord.ui.button(
        label="같은 조건으로 다시 시도",
        style=discord.ButtonStyle.primary,
        emoji="🎲",
    )
    async def retry(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(embed=self.draw(), view=self)


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
            "사용 가능: Glowing Cube (레드 큐브)·Bright Cube (블랙 큐브)"
        ),
        color=0x9B59B6,
    )
    embed.set_author(name="MapleStory | MIRACLE TIME")
    for entry in entries:
        start = entry["start_timestamp"]
        embed.add_field(
            name=f"· __<t:{start}:F> (<t:{start}:R>)__",
            value=f"대상 장비　{entry['equipment']}",
            inline=False,
        )
    return embed


@app_commands.command(name="시드링", description="리스트레인트 링 강화를 무작위로 추첨합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(current_level="현재레벨", stone_count="연마석개수")
@app_commands.describe(
    current_level="강화할 리스트레인트 링의 현재 레벨",
    stone_count="한 번의 강화에 넣을 연마석 개수",
)
@app_commands.choices(
    current_level=[
        app_commands.Choice(name="Lv.4 → Lv.5", value=4),
        app_commands.Choice(name="Lv.5 → Lv.6", value=5),
    ],
    stone_count=[
        app_commands.Choice(name=f"{count}개", value=count) for count in range(1, 6)
    ],
)
async def seed_ring_command(
    interaction: discord.Interaction,
    current_level: app_commands.Choice[int],
    stone_count: app_commands.Choice[int],
) -> None:
    view = SeedRingSimulatorView(
        interaction.user.id, current_level.value, stone_count.value
    )
    await interaction.response.send_message(embed=view.draw(), view=view)


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
    hyper_burning="생략하면 미적용",
    beyond_burning="생략하면 미적용",
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
    count: app_commands.Range[int, 1, 100],
    hyper_burning: app_commands.Choice[str] | None = None,
    beyond_burning: app_commands.Choice[str] | None = None,
) -> None:
    # 버닝 선택을 생략하면 미적용으로 표시하고 계산 함수에는 False를 전달합니다.
    hyper_burning_name = hyper_burning.name if hyper_burning is not None else "미적용"
    beyond_burning_name = beyond_burning.name if beyond_burning is not None else "미적용"
    hyper_burning_enabled = hyper_burning is not None and hyper_burning.value == "적용"
    beyond_burning_enabled = beyond_burning is not None and beyond_burning.value == "적용"
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
        title=f"{GROWTH_POTION_EMOJIS[potion.value]} 성장의 비약 계산기",
        description=(
            f"**비약**　{potion.name}\n"
            f"**사용 전**　Lv.{current_level} ({current_exp_percent:.3f}%)\n"
            f"**하이퍼 버닝**　{hyper_burning_name}\n"
            f"**비욘드 버닝**　{beyond_burning_name}\n"
            f"**사용 개수**　{count_text}\n\n"
            f"◆ **사용 후**　{result_text}\n"
            f"◆ **지급 경험치**　{gained_exp:,}"
        ),
        color=0x57F287,
    )
    embed.set_footer(text="입력한 경험치 퍼센트를 실제 경험치로 환산한 근사 결과입니다.")
    await interaction.response.send_message(embed=embed)


async def exp_coupon_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """입력한 시작 레벨에서 실제 사용할 수 있는 EXP 교환권만 보여줍니다."""
    level = getattr(interaction.namespace, "current_level", None)
    if not isinstance(level, int):
        return []
    available = ["EXP 교환권"] if level < 260 else EXP_COUPONS
    return [
        app_commands.Choice(name=name, value=name)
        for name in available
        if current.casefold() in name.casefold()
    ]


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
    burning=[
        app_commands.Choice(name=burning_name, value=burning_name)
        for burning_name in EXP_COUPON_BURNING_OPTIONS
    ],
)
@app_commands.autocomplete(coupon=exp_coupon_autocomplete)
async def exp_coupon_command(
    interaction: discord.Interaction,
    current_level: app_commands.Range[int, 200, 299],
    coupon: str,
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
            coupon, current_level, current_exp_percent, count, burning_name
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
        title=f"{EXP_COUPON_EMOJIS[coupon]} {coupon} 계산기",
        description=(
            f"**교환권**　{coupon}\n"
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


async def item_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """사용자가 입력한 영문·한글 이름과 가까운 아이템을 선택 목록에 보여줍니다."""
    del interaction
    choices = []
    for item in search_cash_items(current):
        label = item["gms_name"]
        if item["kms_name"]:
            label += f' / {item["kms_name"]}'
        if len(label) > 84:
            label = label[:81] + "..."
        choices.append(
            app_commands.Choice(name=f'{label} ({item["id"]})', value=item["id"])
        )
    return choices


async def appearance_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """먼저 선택한 헤어 또는 성형 안에서 영문명·한글명·ID를 검색합니다."""
    appearance_type = vars(interaction.namespace).get(
        "종류", getattr(interaction.namespace, "appearance_type", None)
    )
    # Discord가 Choice 객체를 넘겨도 실제 분류 문자열만 꺼내 사용합니다.
    category = getattr(appearance_type, "value", appearance_type)
    if category not in APPEARANCE_CATEGORIES:
        return []

    choices = []
    for item in search_cash_items(current, category=category):
        label = item["gms_name"]
        if item["kms_name"]:
            label += f' / {item["kms_name"]}'
        if len(label) > 84:
            label = label[:81] + "..."
        choices.append(
            app_commands.Choice(name=f'{label} ({item["id"]})', value=item["id"])
        )
    return choices


def korean_vocative_suffix(name: str) -> str:
    """닉네임의 마지막 한글 음절에 받침이 있으면 '아', 없으면 '야'를 반환합니다."""
    for character in reversed(name.strip()):
        code = ord(character)
        if 0xAC00 <= code <= 0xD7A3:
            return "아" if (code - 0xAC00) % 28 else "야"
    return "야"


CHANNEL_RECOMMEND_MESSAGES = (
    "헐 **{display_name}**{vocative} 오늘도 많이 힘들었구나 어떡해 ㅠㅠ\n"
    "불쌍하니까 **광휘나 칠흑 잘 뜨는 채널** 점지해줄게 ✨\n\n"
    "오늘의 추천 채널은 바로\n**[ {channel_number}채널 ]** 이야\n\n"
    "광휘나 칠흑 꼭 먹고 나 보스 캐리해줘야 돼 ㅋㅋ",
    "어이구 **{display_name}**{vocative} 오늘도 보스한테 탈탈 털렸구나 ㅠㅠ\n"
    "내가 특별히 **대박 터지는 채널** 하나 골라줄게 🍀\n\n"
    "오늘은\n**[ {channel_number}채널 ]** 로 가봐\n\n"
    "여기서 칠흑 먹으면 내 덕인 거 알지? ㅋㅋ",
    "**{display_name}**{vocative} 잠깐만... 지금 신호가 왔어 🔮\n"
    "오늘 광휘 먹을 수 있는 채널이 딱 하나 보인다\n\n"
    "바로\n**[ {channel_number}채널 ]** 이야\n\n"
    "의심하지 말고 들어가서 보스부터 잡아봐 ㅋㅋ",
    "헉 **{display_name}**{vocative} 오늘 운이 심상치 않은데? ✨\n"
    "느낌 좋은 채널을 내가 직접 점지해줄게\n\n"
    "행운의 채널은\n**[ {channel_number}채널 ]** 이야\n\n"
    "오늘 칠흑 뜨면 자랑하러 와야 돼 ㅋㅋ",
    "**{display_name}**{vocative} 요즘 보상이 너무 짜지 ㅠㅠ\n"
    "불쌍해서 오늘만 특별히 **축복받은 채널** 알려줄게 🙏\n\n"
    "**[ {channel_number}채널 ]** 로 가봐\n\n"
    "광휘 하나 먹고 인생 좀 펴보자 ㅋㅋ",
    "잠깐 **{display_name}**{vocative}, 채널 아무 데나 들어가면 안 돼\n"
    "오늘은 내가 계산까지 다 해봤거든 🤓\n\n"
    "정답은\n**[ {channel_number}채널 ]** 이야\n\n"
    "여기서 보스 잡으면 뭔가 하나는 뜰 거 같은데? ㅋㅋ",
    "우우우... **{display_name}**, 많이 힘들었구나 ㅠㅠ\n"
    "간절한 마음을 담아 행운의 채널을 점지해줄게 ✨\n\n"
    "오늘의 추천 채널은\n**[ {channel_number}채널 ]** 이야\n\n"
    "여기서 꼭 광휘나 칠흑 먹고 행복해져야 돼 ㅋㅋ",
    "**{display_name}**{vocative} 오늘 보스 갈 거지?\n"
    "그냥 가지 말고 내가 골라준 채널에서 잡아봐 😎\n\n"
    "오늘의 대박 채널은\n**[ {channel_number}채널 ]** 이야\n\n"
    "칠흑 뜨면 수수료로 보스 캐리 한 번만 부탁해 ㅋㅋ",
    "헐 **{display_name}**{vocative} 방금 메이플의 기운이 느껴졌어 ⚡\n"
    "오늘 유난히 보상이 잘 뜨는 채널이 있대\n\n"
    "그 채널은 바로\n**[ {channel_number}채널 ]** 이야\n\n"
    "늦기 전에 들어가서 광휘부터 챙겨 ㅋㅋ",
    "**{display_name}**{vocative} 오늘도 빈손으로 나오면 너무 슬프잖아 ㅠㅠ\n"
    "그래서 내가 진짜 열심히 골라봤어\n\n"
    "오늘의 행운 채널은\n**[ {channel_number}채널 ]** 이야 🍀\n\n"
    "제발 뭐라도 하나 먹고 웃으면서 돌아와 ㅋㅋ",
    "쉿 **{display_name}**{vocative}, 이건 너한테만 알려주는 비밀인데 🤫\n"
    "오늘 보상이 몰려 있는 채널을 찾았어\n\n"
    "바로\n**[ {channel_number}채널 ]** 이야\n\n"
    "사람들 몰리기 전에 빨리 가서 칠흑 챙겨 ㅋㅋ",
    "**{display_name}**{vocative} 오늘은 왠지 될 것 같아\n"
    "내가 보기엔 광휘가 너 기다리고 있거든 ✨\n\n"
    "광휘가 숨어 있는 곳은\n**[ {channel_number}채널 ]** 이야\n\n"
    "잡고 나서 아무것도 안 뜨면... 한 번만 더 믿어줘 ㅋㅋ",
    "아이고 **{display_name}**{vocative} 그동안 고생 많았다 ㅠㅠ\n"
    "오늘은 보상 하나쯤 먹을 때도 됐잖아\n\n"
    "내가 골라준 채널은\n**[ {channel_number}채널 ]** 이야\n\n"
    "오늘 여기서 칠흑 먹고 졸업하자 ㅋㅋ",
    "**{display_name}**{vocative} 채널 선택부터가 보스 공략인 거 몰랐어?\n"
    "아무 데나 들어가지 말고 내 말을 믿어봐 😏\n\n"
    "오늘의 정답은\n**[ {channel_number}채널 ]** 이야\n\n"
    "광휘 뜨면 역시 내 선택이었다고 인정해줘 ㅋㅋ",
    "두구두구두구... 🥁\n"
    "**{display_name}**를 위한 오늘의 행운 채널을 발표합니다\n\n"
    "결과는 바로\n**[ {channel_number}채널 ]** 입니다 ✨\n\n"
    "오늘은 진짜 칠흑 하나 먹을 수 있을 것 같은데? ㅋㅋ",
    "**{display_name}**{vocative} 오늘 운세 확인해봤는데 대박이래 🔮\n"
    "특히 이 채널에서 보스를 잡으면 뭔가 뜬다는데?\n\n"
    "추천 채널은\n**[ {channel_number}채널 ]** 이야\n\n"
    "광휘 먹고 나한테 큰절 한 번 하면 돼 ㅋㅋ",
    "헐 **{display_name}**{vocative} 아직도 채널 못 정했어?\n"
    "그런 건 고민할 필요 없이 나한테 맡기면 되지 😌\n\n"
    "오늘은\n**[ {channel_number}채널 ]** 로 가\n\n"
    "칠흑 먹을 준비하고 보스부터 잡아버려 ㅋㅋ",
    "**{display_name}**{vocative} 오늘은 내가 느낌이 진짜 좋아\n"
    "이 채널에서 보스 잡으면 빈손으로 나오진 않을 것 같아 ✨\n\n"
    "그곳은 바로\n**[ {channel_number}채널 ]** 이야\n\n"
    "광휘든 칠흑이든 하나만 딱 먹고 오자 ㅋㅋ",
    "어라 **{display_name}**{vocative}? 네 이름 옆에 행운의 숫자가 보이는데? 👀\n"
    "아무래도 오늘 갈 채널이 정해진 것 같아\n\n"
    "행운의 숫자는\n**[ {channel_number}채널 ]** 이야\n\n"
    "여기서 대박 터뜨리고 자랑하러 와 ㅋㅋ",
    "**{display_name}**{vocative} 오늘의 메이플 신탁이 내려왔어 🙏\n"
    "광휘와 칠흑의 기운이 한 채널에 모이고 있대\n\n"
    "신탁이 가리킨 곳은\n**[ {channel_number}채널 ]** 이야\n\n"
    "오늘 꼭 득템하고 나 보스 캐리해줘야 돼 ㅋㅋ",
)


@app_commands.command(
    name="아이템검색", description="캐시 아이템의 GMS·KMS 이름과 아이콘을 검색합니다."
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(item_id="아이템")
@app_commands.describe(item_id="영문명·한글명 또는 아이템 ID를 입력한 뒤 목록에서 선택")
@app_commands.autocomplete(item_id=item_search_autocomplete)
async def item_search_command(
    interaction: discord.Interaction, item_id: str
) -> None:
    """선택한 캐시 아이템의 양쪽 서버 이름과 아이콘을 보여줍니다."""
    item = CASH_ITEMS_BY_ID.get(item_id)
    if item is None:
        exact_matches = [
            match
            for match in search_cash_items(item_id)
            if item_id.casefold()
            in (match["gms_name"].casefold(), match["kms_name"].casefold())
        ]
        if len(exact_matches) == 1:
            item = exact_matches[0]
        else:
            await interaction.response.send_message(
                "검색 목록에서 아이템을 하나 선택해주세요.", ephemeral=True
            )
            return

    category_name = ITEM_CATEGORY_NAMES.get(item["category"], item["category"])
    kms_name = item["kms_name"] or "KMS 동일 ID 없음"
    embed = discord.Embed(
        title="캐시 아이템 검색",
        description=(
            f'**GMS 이름**　{item["gms_name"]}\n'
            f"**KMS 이름**　{kms_name}\n"
            f'**분류**　{category_name}\n'
            f'**아이템 ID**　`{item["id"]}`'
        ),
        color=0x9B59B6,
    )

    icon_name = item.get("icon")
    if icon_name and ITEM_ICON_ARCHIVE_PATH.exists():
        try:
            with zipfile.ZipFile(ITEM_ICON_ARCHIVE_PATH) as archive:
                icon_data = archive.read(icon_name)
            filename = f'cash-item-{item["id"]}.png'
            file = discord.File(io.BytesIO(icon_data), filename=filename)
            embed.set_thumbnail(url=f"attachment://{filename}")
            await interaction.response.send_message(embed=embed, file=file)
            return
        except (KeyError, OSError, zipfile.BadZipFile):
            logging.exception("Cash item icon could not be read: %s", icon_name)

    embed.set_footer(text="헤어·성형 등 독립 아이콘이 없는 항목은 이름만 표시됩니다.")
    await interaction.response.send_message(embed=embed)


@app_commands.command(
    name="외형검색", description="헤어·성형의 GMS 이름과 KMS 이름을 검색합니다."
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(appearance_type="종류", appearance_id="이름")
@app_commands.describe(
    appearance_type="검색할 외형 종류",
    appearance_id="영문명·한글명 또는 외형 ID를 입력한 뒤 목록에서 선택",
)
@app_commands.choices(
    appearance_type=[
        app_commands.Choice(name=ITEM_CATEGORY_NAMES[category], value=category)
        for category in APPEARANCE_CATEGORIES
    ]
)
@app_commands.autocomplete(appearance_id=appearance_search_autocomplete)
async def appearance_search_command(
    interaction: discord.Interaction,
    appearance_type: app_commands.Choice[str],
    appearance_id: str,
) -> None:
    """선택한 헤어·성형의 GMS 이름과 같은 ID의 KMS 이름을 보여줍니다."""
    item = CASH_ITEMS_BY_ID.get(appearance_id)
    if item is not None and item["category"] != appearance_type.value:
        item = None
    if item is None:
        exact_matches = [
            match
            for match in search_cash_items(
                appearance_id, category=appearance_type.value
            )
            if appearance_id.casefold()
            in (match["gms_name"].casefold(), match["kms_name"].casefold())
        ]
        if len(exact_matches) != 1:
            await interaction.response.send_message(
                "검색 목록에서 외형을 하나 선택해주세요.", ephemeral=True
            )
            return
        item = exact_matches[0]

    kms_name = item["kms_name"] or "KMS 동일 ID 없음"
    embed = discord.Embed(
        title="외형 검색",
        description=(
            f"**종류**　{appearance_type.name}\n"
            f'**GMS 이름**　{item["gms_name"]}\n'
            f"**KMS 이름**　{kms_name}\n"
            f'**외형 ID**　`{item["id"]}`'
        ),
        color=0x9B59B6,
    )
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
            "`/에픽던전` `/심볼계산기` `/5퍼`"
        ),
        inline=False,
    )
    embed.add_field(
        name="시뮬레이터",
        value="`/익성비` `/시드링` `/스스비` `/퍼밀리어` `/채널추천`",
        inline=False,
    )
    embed.add_field(
        name="일정 확인",
        value=(
            "`/썬데이` `/썬데이목록` `/캐시이동` `/우르스` `/서버`\n"
            "`/캐샵` `/미라클큐브` `/핫위크` `/큐브세일`"
        ),
        inline=False,
    )
    embed.add_field(
        name="아이템",
        value="`/아이템검색` `/외형검색`",
        inline=False,
    )
    embed.add_field(
        name="편의",
        value="`/랭킹` `/랭킹등록` `/랭킹해제` `/서버랭킹` `/ㅁ`",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="ㅁ", description="자주 쓰는 메이플 문구를 복사하기 쉽게 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def quick_copy_command(interaction: discord.Interaction) -> None:
    """각 문구에 Discord의 코드 블록 복사 버튼이 따로 생기게 표시합니다."""
    await interaction.response.send_message(
        "```text\nSacred Symbol/claim\n```\n"
        "```text\nArcane Symbol/claim\n```\n"
        "```text\nSol Erda Fragment\n```\n"
        "```text\n/partyleave\n```",
        ephemeral=True,
    )


def record_command_usage(
    command_stats: dict,
    command_name: str,
    user_id: int,
    display_name: str,
) -> None:
    """명령어별·사용자별 실행 횟수를 재시작 후에도 남길 형태로 기록합니다."""
    command_stats["total"] = command_stats.get("total", 0) + 1
    commands_used = command_stats.setdefault("commands", {})
    commands_used[command_name] = commands_used.get(command_name, 0) + 1
    users = command_stats.setdefault("users", {})
    user = users.setdefault(str(user_id), {"name": display_name, "count": 0})
    user["name"] = display_name
    user["count"] += 1


def build_command_stats_embed(command_stats: dict) -> discord.Embed:
    """소유자가 한 화면에서 전체·명령어별·사용자별 횟수를 확인하게 만듭니다."""
    commands_used = sorted(
        command_stats.get("commands", {}).items(), key=lambda item: (-item[1], item[0])
    )
    users = sorted(
        command_stats.get("users", {}).items(),
        key=lambda item: (-item[1]["count"], item[0]),
    )[:10]
    embed = discord.Embed(
        title="명령어 사용 통계",
        description=(
            f"**전체 사용:** {command_stats.get('total', 0):,}회\n"
            f"**사용자 수:** {len(command_stats.get('users', {})):,}명"
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="명령어별",
        value=(
            "\n".join(f"`/{name}`　{count:,}회" for name, count in commands_used)
            or "기록 없음"
        ),
        inline=False,
    )
    embed.add_field(
        name="사용자별 상위 10명",
        value=(
            "\n".join(
                f"{discord.utils.escape_markdown(data['name'])} (`{user_id}`)　"
                f"{data['count']:,}회"
                for user_id, data in users
            )
            or "기록 없음"
        ),
        inline=False,
    )
    return embed


@app_commands.command(name="명령어통계", description="봇 명령어 사용 통계를 확인합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def command_stats_command(interaction: discord.Interaction) -> None:
    """Discord 애플리케이션 소유자에게만 저장된 통계를 보여줍니다."""
    if not await interaction.client.is_owner(interaction.user):
        await interaction.response.send_message(
            "봇 소유자만 확인할 수 있습니다.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=build_command_stats_embed(interaction.client.command_stats),
        ephemeral=True,
    )


def format_boss_hp_as_k(value: str) -> str:
    """B·T·Q 단위 체력을 인게임 전투분석에서 쓰는 K 단위로 바꿉니다."""
    # K는 1,000이므로 B·T·Q를 각각 아래 배수만큼 곱하면 됩니다.
    multiplier = {"B": 1_000_000, "T": 1_000_000_000, "Q": 1_000_000_000_000}
    return f"{int(Decimal(value[:-1]) * multiplier[value[-1]]):,}K"


async def traffic_light_difficulty_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """먼저 선택한 보스에 실제로 존재하는 난이도만 보여줍니다."""
    boss = vars(interaction.namespace).get(
        "보스", getattr(interaction.namespace, "boss", None)
    )
    # Discord가 선택값을 Choice 객체로 넘기는 경우에도 실제 문자열로 조회합니다.
    boss = getattr(boss, "value", boss)
    if boss == "검밑":
        return []
    difficulties = BOSS_TRAFFIC_LIGHTS.get(boss, {})
    return [
        app_commands.Choice(name=name, value=name)
        for name in difficulties
        if current.casefold() in name.casefold()
    ]


@app_commands.command(name="5퍼", description="글로벌 리부트 보스의 5% 최소 피해량을 확인합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(boss="보스", difficulty="난이도")
@app_commands.describe(
    boss="보상 획득 기준을 확인할 보스",
    difficulty="확인할 보스 난이도",
)
@app_commands.choices(
    boss=[
        app_commands.Choice(name=name, value=name)
        for name in (
            "검밑",
            *(
                name
                for name in BOSS_TRAFFIC_LIGHTS
                if name not in {boss for boss, _ in BLACK_MAGE_BELOW_BOSSES}
            ),
        )
    ],
)
@app_commands.autocomplete(difficulty=traffic_light_difficulty_autocomplete)
async def traffic_light_command(
    interaction: discord.Interaction,
    boss: app_commands.Choice[str],
    difficulty: str | None = None,
) -> None:
    if boss.value == "검밑":
        lines = []
        for boss_name, boss_difficulty in BLACK_MAGE_BELOW_BOSSES:
            _, minimum_damage = BOSS_TRAFFIC_LIGHTS[boss_name][boss_difficulty]
            lines.append(
                f"**{boss_difficulty} {boss_name}**　"
                f"{format_boss_hp_as_k(minimum_damage)}"
            )
        embed = discord.Embed(
            title="🚦 검밑 보스 5%",
            description="\n".join(lines),
            color=0x57F287,
        )
        await interaction.response.send_message(embed=embed)
        return

    boss_difficulties = BOSS_TRAFFIC_LIGHTS[boss.value]
    if difficulty not in boss_difficulties:
        available = ", ".join(boss_difficulties)
        await interaction.response.send_message(
            f"{boss.value}에서 선택 가능한 난이도: **{available}**",
            ephemeral=True,
        )
        return

    total_hp, minimum_damage = boss_difficulties[difficulty]
    total_hp_k = format_boss_hp_as_k(total_hp)
    minimum_damage_k = format_boss_hp_as_k(minimum_damage)
    title_boss_name = f"{difficulty} {boss.value}"
    embed = discord.Embed(
        title=f"🚦 {title_boss_name} 5%",
        description=(
            f"**총 체력**　{total_hp_k}\n"
            f"**5% 최소 피해량**　{minimum_damage_k}"
        ),
        color=0x57F287,
    )
    thumbnail_path = BOSS_THUMBNAIL_PATHS.get(boss.value)
    if thumbnail_path is not None:
        embed.set_thumbnail(url=f"attachment://{thumbnail_path.name}")
        await interaction.response.send_message(
            embed=embed,
            file=discord.File(thumbnail_path),
        )
        return
    await interaction.response.send_message(embed=embed)


@app_commands.command(
    name="채널추천",
    description="메이플스토리 1~40채널 중 하나를 추천합니다.",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def channel_recommend_command(interaction: discord.Interaction) -> None:
    # Discord 닉네임에 마크다운 문자가 있어도 메시지 모양이 깨지지 않게 처리합니다.
    raw_display_name = interaction.user.display_name
    display_name = discord.utils.escape_markdown(raw_display_name)
    # 메이플스토리 게임 채널 1번부터 40번까지 중 하나를 같은 확률로 선택합니다.
    channel_number = random.randint(1, 40)

    # 위의 문구 20개 중 하나를 같은 확률로 고른 뒤 닉네임과 채널 번호를 채웁니다.
    message = random.choice(CHANNEL_RECOMMEND_MESSAGES).format(
        display_name=display_name,
        vocative=korean_vocative_suffix(raw_display_name),
        channel_number=channel_number,
    )
    await interaction.response.send_message(message)


def draw_unique_familiar_potential() -> tuple[str, str, bool]:
    """유니크 퍼밀리어 카드의 잠재능력 두 줄을 표본 확률대로 추첨합니다."""
    first_line = random.choices(
        FAMILIAR_UNIQUE_POTENTIALS,
        weights=[rate for _, rate in FAMILIAR_UNIQUE_POTENTIALS],
        k=1,
    )[0][0]
    double_prime = random.random() < FAMILIAR_DOUBLE_PRIME_CHANCE
    second_pool = (
        FAMILIAR_UNIQUE_POTENTIALS if double_prime else FAMILIAR_EPIC_POTENTIALS
    )
    second_line = random.choices(
        second_pool,
        weights=[rate for _, rate in second_pool],
        k=1,
    )[0][0]
    return first_line, second_line, double_prime


def expectation_line(probability: float) -> str:
    """확률을 백분율과 평균 등장 횟수로 함께 표시합니다."""
    return f"`{probability * 100:.6f}%` · 평균 약 `{1 / probability:,.0f}회`"


def cumulative_success_probability(probability: float, attempts: int) -> float:
    """독립 추첨을 여러 번 했을 때 목표가 한 번 이상 나올 확률입니다."""
    return 1 - (1 - probability) ** attempts


def familiar_expectation_text(
    result: tuple[str, str, bool], expectation: dict, draw_count: int
) -> str:
    """DB에서 읽은 현재 두 줄 조합의 확률·기대 횟수·희귀도를 표시합니다."""
    first_line, second_line, double_prime = result
    second_rank = "유니크" if double_prime else "에픽"
    return (
        f"**1번째 줄**　{first_line}\n"
        f"**2번째 줄 ({second_rank})**　{second_line}\n\n"
        f"**1회 시행 시 목표 달성 확률**\n"
        f"`{expectation['probability'] * 100:.10f}%`\n\n"
        f"**실제 희귀도**　상위 `{expectation['rarity_percentile']:.2f}%`\n"
        f"**평균 필요 횟수**　약 `{expectation['expected_attempts']:,.0f}회`\n"
        f"**내 {draw_count:,}회 이내 달성 확률**　상위 `"
        f"{cumulative_success_probability(expectation['probability'], draw_count) * 100:.2f}%`"
    )


def build_familiar_result(
    draw_count: int,
) -> tuple[discord.Embed, discord.File, tuple[str, str, bool]]:
    """퍼밀리어 잠재능력을 새로 추첨하고 카드 이미지까지 만듭니다."""
    first_line, second_line, double_prime = draw_unique_familiar_potential()
    embed = discord.Embed(
        description="✨ **더블 프라임!**" if double_prime else None,
        color=0x954506,
    )
    embed.set_footer(text=f"누적 횟수: {draw_count:,}회")
    filename = "familiar-result.png"
    embed.set_image(url=f"attachment://{filename}")
    result = (first_line, second_line, double_prime)
    return (
        embed,
        discord.File(
            create_familiar_result_image(first_line, second_line), filename=filename
        ),
        result,
    )


class FamiliarSimulatorView(UserOwnedView):
    """같은 퍼밀리어 메시지에서 잠재능력을 다시 추첨합니다."""

    def __init__(self, user_id: int, result: tuple[str, str, bool]) -> None:
        super().__init__(user_id, timeout=86_400)
        self.result = result
        self.draw_count = 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.data or {}).get("custom_id") == self.show_expectation.custom_id:
            return True
        return await super().interaction_check(interaction)

    @discord.ui.button(label="다시 뽑기", style=discord.ButtonStyle.primary)
    async def reroll(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.draw_count += 1
        embed, file, self.result = build_familiar_result(self.draw_count)
        await interaction.response.edit_message(
            embed=embed, attachments=[file], view=self
        )

    @discord.ui.button(label="기대값 계산하기", style=discord.ButtonStyle.secondary)
    async def show_expectation(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        expectation = interaction.client.familiar_expectation_store.get(self.result)
        await interaction.response.send_message(
            familiar_expectation_text(self.result, expectation, self.draw_count),
            ephemeral=True,
        )


@app_commands.command(
    name="퍼밀리어",
    description="유니크 퍼밀리어 잠재능력 두 줄을 무작위로 추첨합니다.",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def familiar_command(interaction: discord.Interaction) -> None:
    """유니크 퍼밀리어 카드 한 장의 잠재능력 결과를 보여줍니다."""
    embed, file, result = build_familiar_result(1)
    await interaction.response.send_message(
        embed=embed,
        file=file,
        view=FamiliarSimulatorView(interaction.user.id, result),
    )


def draw_pssb_results(
    rates: list[tuple[str, float]], count: int
) -> list[tuple[str, float]]:
    """현재 공식 PSSB 목록에서 요청한 횟수만큼 독립 추첨합니다."""
    return random.choices(
        rates,
        weights=[rate for _, rate in rates],
        k=count,
    )


def build_pssb_embed(
    results: list[tuple[str, float]], draw_count: int
) -> discord.Embed:
    """PSSB 추첨 결과 텍스트 임베드를 만듭니다."""
    embed = discord.Embed(
        title=f"{PSSB_EMOJI} Premium Surprise Style Box",
        url=PSSB_RATES_PAGE_URL,
        description="\n".join(
            format_pssb_result(index, name, rate)
            for index, (name, rate) in enumerate(results, start=1)
        ),
        color=0xFF69B4,
    )
    embed.set_footer(
        text=(
            f"누적 횟수: {draw_count:,}회\n"
            f"지금까지 낭비한 돈: {pssb_nx_cost(draw_count):,} NX"
        )
    )
    return embed


def pssb_expectation_text(
    results: list[tuple[str, float]], draw_count: int
) -> str:
    """현재 PSSB 결과별 공식 확률과 평균 등장 횟수를 보여줍니다."""
    lines = []
    seen = set()
    for name, rate in results:
        if name in seen:
            continue
        seen.add(name)
        probability = rate / 100
        expected_boxes = round(1 / probability)
        success = cumulative_success_probability(probability, draw_count) * 100
        lines.append(
            f"**{name}**\n{expectation_line(probability)}\n"
            f"평균 구매 비용: 약 `{pssb_nx_cost(expected_boxes):,} NX`\n"
            f"내 {draw_count:,}회 이내 달성 확률: 상위 `{success:.2f}%`"
        )
    return "\n\n".join(lines) + "\n\n*1개 3,600 NX · 11개 세트 36,000 NX 기준*"


def pssb_nx_cost(box_count: int) -> int:
    """필요한 PSSB 수량을 낱개와 11개 세트로 가장 싸게 구매한 NX입니다."""
    sets, singles = divmod(box_count, PSSB_SET_SIZE)
    return sets * PSSB_SET_PRICE + min(
        singles * PSSB_SINGLE_PRICE, PSSB_SET_PRICE
    )


def build_pssb_file(
    results: list[tuple[str, float]], count: int
) -> tuple[discord.File, str]:
    """PSSB 결과 합성 이미지를 Discord 파일로 만듭니다."""
    filename = f"pssb-{count}-results.png"
    return discord.File(create_pssb_result_image(results), filename=filename), filename


class PssbSimulatorView(UserOwnedView):
    """같은 메시지에서 최신 공식 목록으로 PSSB를 다시 추첨합니다."""

    def __init__(
        self, user_id: int, count: int, results: list[tuple[str, float]]
    ) -> None:
        super().__init__(user_id, timeout=86_400)
        self.count = count
        self.results = results
        self.draw_count = count

    @discord.ui.button(label="다시 뽑기", style=discord.ButtonStyle.primary)
    async def reroll(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # 버튼을 누른 사이 공식 품목이 바뀌었을 수 있으므로 매번 새 확률표를 읽습니다.
        await interaction.response.defer()
        try:
            rates = await interaction.client.fetch_pssb_rates()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            logging.exception("Failed to reload the official PSSB rates.")
            await interaction.followup.send(
                "공식 PSSB 확률표를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        results = draw_pssb_results(rates, self.count)
        self.results = results
        self.draw_count += self.count
        embed = build_pssb_embed(results, self.draw_count)
        try:
            file, filename = build_pssb_file(results, self.count)
            embed.set_image(url=f"attachment://{filename}")
            await interaction.edit_original_response(
                embed=embed, attachments=[file], view=self
            )
        except (OSError, ValueError, zipfile.BadZipFile):
            logging.exception("PSSB result image could not be created.")
            await interaction.edit_original_response(
                embed=embed, attachments=[], view=self
            )

    @discord.ui.button(label="기대값 계산하기", style=discord.ButtonStyle.secondary)
    async def show_expectation(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            pssb_expectation_text(self.results, self.draw_count), ephemeral=True
        )


@app_commands.command(
    name=app_commands.locale_str("ssb", ko="스스비"),
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
    results = draw_pssb_results(rates, count.value)
    embed = build_pssb_embed(results, count.value)
    view = PssbSimulatorView(interaction.user.id, count.value, results)
    try:
        file, filename = build_pssb_file(results, count.value)
        embed.set_image(url=f"attachment://{filename}")
        await interaction.followup.send(embed=embed, file=file, view=view)
    except (OSError, ValueError, zipfile.BadZipFile):
        # 리소스 파일에 문제가 생겨도 추첨 결과 이름까지 잃지는 않게 텍스트는 보냅니다.
        logging.exception("PSSB result image could not be created.")
        await interaction.followup.send(embed=embed, view=view)


@app_commands.command(name="ㅅㅅㅂ", description="/스스비의 초성 별칭입니다.")
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
async def pssb_initials_command(
    interaction: discord.Interaction,
    count: app_commands.Choice[int],
) -> None:
    """초성으로 실행해도 기존 PSSB 명령어와 같은 로직을 사용합니다."""
    await pssb_command.callback(interaction, count)


@app_commands.command(name="캐샵", description="최신 캐시샵 업데이트 링크를 보여줍니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cash_shop_command(interaction: discord.Interaction) -> None:
    """저장된 최신 공식 캐시샵 공지와 데이터 마이닝 페이지를 보여줍니다."""
    latest = getattr(interaction.client, "latest_cash_shop", None)
    if latest is None:
        await interaction.response.send_message(
            "저장된 캐시샵 업데이트가 없습니다. 다음 공지 확인 후 다시 시도해주세요.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="[ 캐시샵 업데이트 ]",
        description=(
            f"[공식 캐시샵 업데이트]({latest['url']})　"
            f"[캐시샵 데이터 마이닝]({CASH_SHOP_MINING_URL})"
            + (
                "\n\n" + "\n".join(f"· {item}" for item in latest.get("items", []))
                if latest.get("items")
                else ""
            )
        ),
        color=0x4E5058,
    )
    # 첨부 파일을 사용하면 외부 이미지 주소가 만료되어도 썸네일이 계속 표시됩니다.
    embed.set_thumbnail(url="attachment://cash-shop-update.png")
    await interaction.response.send_message(
        embed=embed,
        file=discord.File(
            CASH_SHOP_UPDATE_IMAGE_PATH,
            filename="cash-shop-update.png",
        ),
    )


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
        embed=build_cash_shop_transfer_embed(schedule),
        file=discord.File(CASH_SHOP_TRANSFER_IMAGE_PATH),
    )


@app_commands.command(name="우르스", description="현재 우르스 골든타임 여부를 확인합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ursus_command(interaction: discord.Interaction) -> None:
    now = datetime.now(timezone.utc)
    window = current_ursus_window(now)
    embed, image_path = build_ursus_embed(
        "active" if window is not None else "inactive", window, now
    )
    await interaction.response.send_message(
        embed=embed,
        file=discord.File(image_path),
    )


@app_commands.command(name="랭킹", description="GMS 캐릭터의 공식 레벨 랭킹을 확인합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="처음에는 입력하고, 이후에는 비워도 됩니다")
async def ranking_command(
    interaction: discord.Interaction,
    nickname: str | None = None,
) -> None:
    """공식 GMS 랭킹에서 캐릭터·월드·유니온 순위를 찾아 보여줍니다."""
    if nickname is None:
        nickname = interaction.client.ranking_store.get_default_character(
            interaction.user.id
        )
        if nickname is None:
            await interaction.response.send_message(
                "처음에는 `/랭킹 닉네임:캐릭터명`처럼 닉네임을 입력해주세요.",
                ephemeral=True,
            )
            return
    nickname = nickname.strip()
    if not nickname or len(nickname) > 20:
        await interaction.response.send_message(
            "닉네임을 1~20자로 입력해주세요.", ephemeral=True
        )
        return

    await interaction.response.defer()
    try:
        character = await interaction.client.fetch_ranking_character(
            "na", "overall", "weekly", nickname
        )
        if character is None:
            await interaction.followup.send(
                f"**{discord.utils.escape_markdown(nickname)}** 캐릭터를 찾지 못했습니다.",
                ephemeral=True,
            )
            return
        if character.get("level", 0) < MIN_TRACKED_LEVEL:
            await interaction.followup.send(
                f"**{discord.utils.escape_markdown(character['characterName'])}** "
                "캐릭터의 기록 데이터가 없습니다.",
                ephemeral=True,
            )
            return
        # 직접 이름을 입력해 성공한 조회만 다음 /랭킹 기본값으로 기억합니다.
        interaction.client.ranking_store.save_default_character(
            interaction.user.id, character["characterName"]
        )
        world_id = character["worldID"]
        world_character = await interaction.client.fetch_ranking_character(
            "na", "world", world_id, nickname
        )
        world_total_count = await interaction.client.fetch_ranking_total_count(
            "na", "world", world_id
        )
        legion = await interaction.client.fetch_ranking_character(
            "na", "legion", world_id, nickname
        )
        achievement = await interaction.client.fetch_ranking_character(
            "na", "achievement", world_id, nickname
        )
        if achievement is not None:
            # 업적 API는 실제 업적 점수를 score가 아니라 starSum으로 반환합니다.
            achievement["score"] = achievement.get("starSum", achievement.get("score", 0))
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
        logging.exception("Failed to load the official GMS character ranking.")
        await interaction.followup.send(
            "공식 캐릭터 랭킹을 확인하지 못했습니다. 잠시 후 다시 시도해주세요.",
            ephemeral=True,
        )
        return

    # 서버 랭킹과 메창력도 같은 최신 조회값을 쓰도록 캐릭터 기록에 함께 저장합니다.
    if legion is not None:
        character["legionLevel"] = legion["legionLevel"]
        character["legionRank"] = legion["rank"]
    if achievement is not None:
        character["achievementScore"] = achievement["score"]
        character["achievementRank"] = achievement["rank"]
    gains = interaction.client.ranking_store.save_snapshot(
        character, datetime.now(URSUS_TIMEZONE).date()
    )
    fetch_character_image = getattr(interaction.client, "fetch_character_image", None)
    character_image = (
        await fetch_character_image(character.get("characterImgURL"))
        if fetch_character_image is not None
        else None
    )
    filename = "ranking-card.png"
    await interaction.followup.send(
        file=discord.File(
            create_ranking_history_image(
                character,
                gains,
                world_character["rank"] if world_character is not None else None,
                legion,
                achievement,
                world_total_count,
                character_image,
            ),
            filename=filename,
        ),
    )


def guild_id_or_none(interaction: discord.Interaction) -> int | None:
    """서버 전용 랭킹 명령어가 DM에서 실행되지 않게 확인합니다."""
    return interaction.guild_id


@app_commands.command(name="랭킹등록", description="현재 기본 캐릭터를 이 서버 랭킹에 등록합니다.")
@app_commands.allowed_installs(guilds=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def guild_ranking_register_command(interaction: discord.Interaction) -> None:
    """다른 서버와 섞이지 않게 현재 서버의 등록 목록만 갱신합니다."""
    guild_id = guild_id_or_none(interaction)
    if guild_id is None:
        await interaction.response.send_message("이 명령어는 Discord 서버에서만 사용할 수 있습니다.", ephemeral=True)
        return
    character_name = interaction.client.ranking_store.get_default_character(interaction.user.id)
    if character_name is None:
        await interaction.response.send_message(
            "먼저 `/랭킹 닉네임:캐릭터명`으로 캐릭터를 조회해주세요.", ephemeral=True
        )
        return
    interaction.client.ranking_store.register_guild_character(
        guild_id,
        interaction.user.id,
        interaction.user.display_name,
        character_name,
    )
    await interaction.response.send_message(
        f"**{discord.utils.escape_markdown(character_name)}** 캐릭터를 이 서버 랭킹에 등록했습니다.",
        ephemeral=True,
    )


@app_commands.command(name="랭킹해제", description="현재 서버의 내 랭킹 등록을 해제합니다.")
@app_commands.allowed_installs(guilds=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def guild_ranking_unregister_command(interaction: discord.Interaction) -> None:
    """현재 서버의 사용자 한 명만 해제하며 다른 서버 등록은 건드리지 않습니다."""
    guild_id = guild_id_or_none(interaction)
    if guild_id is None:
        await interaction.response.send_message("이 명령어는 Discord 서버에서만 사용할 수 있습니다.", ephemeral=True)
        return
    removed = interaction.client.ranking_store.unregister_guild_character(
        guild_id, interaction.user.id
    )
    await interaction.response.send_message(
        "이 서버의 랭킹 등록을 해제했습니다." if removed else "이 서버에 등록된 캐릭터가 없습니다.",
        ephemeral=True,
    )


@app_commands.command(name="서버랭킹", description="이 서버에 등록된 캐릭터의 메창력 랭킹을 봅니다.")
@app_commands.allowed_installs(guilds=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="입력하면 해당 캐릭터의 위·아래 순위를 보여줍니다")
async def guild_ranking_command(
    interaction: discord.Interaction, nickname: str | None = None
) -> None:
    """등록된 사용자만 비교하며 Discord 멘션 대신 서버 표시명을 보여줍니다."""
    guild_id = guild_id_or_none(interaction)
    if guild_id is None:
        await interaction.response.send_message("이 명령어는 Discord 서버에서만 사용할 수 있습니다.", ephemeral=True)
        return
    entries = interaction.client.ranking_store.get_guild_rankings(guild_id)
    if not entries:
        await interaction.response.send_message(
            "아직 이 서버에 등록된 캐릭터가 없습니다. `/랭킹` 후 `/랭킹등록`을 실행해주세요.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        embed=build_guild_ranking_embed(entries, nickname)
    )


@app_commands.command(name="서버", description="글로벌 메이플 주요 월드의 접속 상태를 확인합니다.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def server_status_command(interaction: discord.Interaction) -> None:
    # 공식 API 응답을 기다리는 동안 Discord의 3초 응답 제한을 넘기지 않게 합니다.
    await interaction.response.defer()
    try:
        statuses = await interaction.client.fetch_server_status()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        logging.exception("Failed to load the official MapleStory server status.")
        await interaction.followup.send(
            "공식 서버 상태를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(embed=build_server_status_embed(statuses))


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
INFO_CHANNEL_TYPE_CHOICES = [
    app_commands.Choice(name="시간", value=INFO_TIME),
    app_commands.Choice(name="환율", value=INFO_EXCHANGE),
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


@app_commands.command(name="우르스알림", description="우르스 골든타임 시작·종료 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="알림을 받을 텍스트 채널", action="알림 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def ursus_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    await run_alert_setting_command(
        interaction, channel, action, ALERT_URSUS, "우르스 골든타임 알림"
    )


@app_commands.command(name="서버알림", description="점검 종료 후 서버 오픈 알림 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작", role="역할")
@app_commands.describe(
    channel="알림을 받을 텍스트 채널",
    action="알림 ON 또는 OFF",
    role="서버가 열렸을 때 멘션할 역할(ON일 때 필수)",
)
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def server_status_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
    role: discord.Role | None = None,
) -> None:
    await interaction.client.configure_alert_channel(
        interaction,
        channel,
        action.value == "on",
        ALERT_SERVER,
        "서버 오픈 알림",
        role,
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


@app_commands.command(name="환율기록알림", description="USD/KRW 환율 변동 기록 채널을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(channel="채널", action="동작")
@app_commands.describe(channel="환율 변동 기록을 표시할 텍스트 채널", action="기록 ON 또는 OFF")
@app_commands.choices(action=ALERT_ACTION_CHOICES)
async def exchange_log_alert_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    action: app_commands.Choice[str],
) -> None:
    await run_alert_setting_command(
        interaction, channel, action, ALERT_EXCHANGE_LOG, "환율 기록 알림"
    )


@app_commands.command(name="정보채널", description="시간·환율 음성 채널의 자동 갱신을 설정합니다.")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.rename(kind="종류", channel="채널", action="동작")
@app_commands.describe(
    kind="시간 또는 USD/KRW 환율",
    channel="이름을 자동으로 바꿀 음성 채널",
    action="자동 갱신 ON 또는 OFF",
)
@app_commands.choices(kind=INFO_CHANNEL_TYPE_CHOICES, action=ALERT_ACTION_CHOICES)
async def info_channel_command(
    interaction: discord.Interaction,
    kind: app_commands.Choice[str],
    channel: discord.VoiceChannel,
    action: app_commands.Choice[str],
) -> None:
    await interaction.client.configure_info_channel(
        interaction, channel, kind.value, action.value == "on"
    )


def load_state() -> tuple[
    set[int] | None,
    set[str],
    dict | None,
    dict | None,
    dict | None,
    dict[str, str],
    dict[str, dict],
    dict[str, str],
    dict | None,
    str | None,
    dict | None,
    dict[str, int],
    dict | None,
    dict,
]:
    # 이전 실행에서 이미 알린 공지 번호를 불러와 같은 글을 다시 보내지 않습니다.
    if not STATE_PATH.exists():
        return None, set(), None, None, None, {}, {}, {}, None, None, None, {}, None, {
            "total": 0,
            "commands": {},
            "users": {},
        }
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
        state.get("ursus_alert_events", {}),
        state.get("latest_cash_shop"),
        state.get("server_status"),
        state.get("exchange_log"),
        state.get("server_alert_roles", {}),
        state.get("maintenance_watch"),
        state.get("command_stats", {"total": 0, "commands": {}, "users": {}}),
    )


def save_state(
    sent_ids: set[int] | None,
    watched_categories: set[str],
    sunny_sunday: dict | None,
    alert_channels: dict[str, set[int]],
    patch_events: dict | None = None,
    exp_coupon_burning_preferences: dict[str, str] | None = None,
    symbol_calculator_preferences: dict[str, dict] | None = None,
    ursus_alert_events: dict[str, str] | None = None,
    latest_cash_shop: dict | None = None,
    server_status: str | None = None,
    exchange_log: dict | None = None,
    server_alert_roles: dict[str, int] | None = None,
    maintenance_watch: dict | None = None,
    command_stats: dict | None = None,
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
                "ursus_alert_events": ursus_alert_events or {},
                "latest_cash_shop": latest_cash_shop,
                "server_status": server_status,
                "exchange_log": exchange_log,
                "server_alert_roles": server_alert_roles or {},
                "maintenance_watch": maintenance_watch,
                "command_stats": command_stats
                or {"total": 0, "commands": {}, "users": {}},
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
            self.ursus_alert_events,
            self.latest_cash_shop,
            self.server_status,
            self.exchange_log,
            self.server_alert_roles,
            self.maintenance_watch,
            self.command_stats,
        ) = load_state()
        self.alert_channels = normalize_alert_channels(
            stored_alert_channels, channel_id, sunny_channel_id
        )
        migrate_sunny_sunday_state(self.sunny_sunday, sunny_channel_id)
        self.session: aiohttp.ClientSession | None = None
        self.ranking_store = RankingStore(RANKING_DB_PATH)
        self._last_ranking_backup_at: datetime | None = None
        self._ranking_scan_date = None
        self._ranking_world_offset = 0
        self._completed_ranking_world_ids: set[int] = set()
        (
            self._ranking_limit_failures,
            self._ranking_retry_until,
        ) = self.ranking_store.get_collector_backoff()
        self._ranking_request_lock = asyncio.Lock()
        self._next_ranking_request_at = 0.0
        self.familiar_expectation_store = FamiliarExpectationStore(FAMILIAR_DB_PATH)
        # OpenAI 키는 코드에 적지 않고 .env 파일에서만 읽습니다.
        self.openai = AsyncOpenAI()
        # Google 번역 키도 .env 파일에서 읽습니다. 키를 Discord나 GitHub에 올리면 안 됩니다.
        self.google_api_key = os.environ["GOOGLE_TRANSLATE_API_KEY"]

    async def setup_hook(self) -> None:
        # Discord 연결이 준비되면 5분마다 새 공지를 확인하는 작업을 시작합니다.
        self.session = aiohttp.ClientSession()
        await self.tree.set_translator(KoreanCommandTranslator())
        # 전역 슬래시 명령을 Discord에 등록합니다. 명령 내용이 바뀌어도 재시작 시 동기화됩니다.
        for command in (
            help_command,
            quick_copy_command,
            command_stats_command,
            seed_ring_command,
            hexa_command,
            extreme_growth_potion_command,
            growth_potion_command,
            exp_coupon_command,
            epic_dungeon_command,
            symbol_calculator_command,
            item_search_command,
            appearance_search_command,
            traffic_light_command,
            ranking_command,
            guild_ranking_register_command,
            guild_ranking_unregister_command,
            guild_ranking_command,
            channel_recommend_command,
            familiar_command,
            pssb_command,
            pssb_initials_command,
            cash_shop_command,
            sunny_sunday_command,
            sunny_sunday_list_command,
            cash_shop_transfer_command,
            ursus_command,
            hot_week_command,
            cube_sale_command,
            miracle_time_command,
            server_status_command,
            news_alert_command,
            sunny_day_alert_command,
            sunny_list_alert_command,
            miracle_time_alert_command,
            cash_shop_transfer_alert_command,
            ursus_alert_command,
            server_status_alert_command,
            cube_sale_alert_command,
            exchange_log_alert_command,
            info_channel_command,
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
            self.ursus_alert_events,
            self.latest_cash_shop,
            self.server_status,
            self.exchange_log,
            self.server_alert_roles,
            self.maintenance_watch,
            self.command_stats,
        )

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """슬래시 명령 실행만 한 번 기록하고 버튼·자동완성은 제외합니다."""
        if interaction.type is not discord.InteractionType.application_command:
            return
        command_name = (interaction.data or {}).get("name")
        if not command_name:
            return
        record_command_usage(
            self.command_stats,
            command_name,
            interaction.user.id,
            interaction.user.display_name,
        )
        self.persist_state()

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
        if not self.check_ursus.is_running():
            self.check_ursus.start()
        if not self.check_server_status.is_running():
            self.check_server_status.start()
        if not self.update_time_channels.is_running():
            self.update_time_channels.start()
        if not self.update_exchange_channels.is_running():
            self.update_exchange_channels.start()
        if not self.collect_rankings.is_running():
            self.collect_rankings.start()

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

    async def fetch_server_status(self) -> dict[str, bool]:
        # 넥슨 공식 상태 API 한 번으로 주요 4개 월드를 함께 확인합니다.
        assert self.session is not None
        async with self.session.get(
            SERVER_STATUS_API_URL,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            return parse_server_status(await response.json())

    async def fetch_ranking_character(
        self,
        region: str,
        ranking_type: str,
        ranking_id: str | int,
        nickname: str,
        rate_limit_target: int | str | None = None,
    ) -> dict | None:
        # 공식 GMS 랭킹 화면이 사용하는 공개 응답에서 닉네임 한 명만 찾습니다.
        payload = await self.fetch_ranking_payload(
            region,
            {
                "type": ranking_type,
                "id": str(ranking_id),
                "reboot_index": "0",
                "page_index": "1",
                "character_name": nickname,
            },
            rate_limit_target,
        )
        return find_ranking_character(payload, nickname)

    async def fetch_ranking_total_count(
        self,
        region: str,
        ranking_type: str,
        ranking_id: str | int,
    ) -> int | None:
        """캐릭터 검색 필터가 없는 공식 랭킹의 전체 인원수를 읽습니다."""
        payload = await self.fetch_ranking_payload(
            region,
            {
                "type": ranking_type,
                "id": str(ranking_id),
                "reboot_index": "0",
                "page_index": "1",
            },
        )
        try:
            return int(payload["totalCount"])
        except (KeyError, TypeError, ValueError):
            return None

    async def fetch_character_image(self, url: str | None) -> bytes | None:
        """공식 캐릭터 이미지를 카드에 넣되 실패해도 랭킹 조회는 유지합니다."""
        if not url or self.session is None:
            return None
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                response.raise_for_status()
                return await response.read()
        except (aiohttp.ClientError, TimeoutError):
            logging.warning("Failed to download ranking character image: %s", url)
            return None

    async def fetch_ranking_page(self, world_id: int, page_index: int) -> dict:
        """지정한 월드 랭킹 10명을 읽고 API 제한은 수집 루프에 알립니다."""
        return await self.fetch_ranking_payload(
            "na",
            {
                "type": "world",
                "id": str(world_id),
                "reboot_index": "0",
                "page_index": str(page_index),
            },
            world_id,
        )

    async def fetch_ranking_payload(
        self,
        region: str,
        params: dict[str, str],
        rate_limit_target: int | str | None = None,
    ) -> dict:
        """모든 랭킹 요청을 한 통로로 보내 초당 한 번의 시작 간격을 지킵니다."""
        assert self.session is not None
        async with self._ranking_request_lock:
            loop = asyncio.get_running_loop()
            wait_seconds = self._next_ranking_request_at - loop.time()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._next_ranking_request_at = loop.time() + RANKING_SCAN_INTERVAL_SECONDS
            async with self.session.get(
                RANKING_API_URL.format(region=region),
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if rate_limit_target is not None and response.status in {403, 429}:
                    try:
                        retry_after = int(response.headers.get("Retry-After", ""))
                    except ValueError:
                        retry_after = None
                    raise RankingRateLimited(
                        rate_limit_target, response.status, retry_after
                    )
                response.raise_for_status()
                return await response.json()

    def pause_ranking_collection(self, error: RankingRateLimited) -> int:
        """API 제한 대기를 DB에 남겨 서비스 재시작 뒤에도 같은 요청을 막습니다."""
        self._ranking_limit_failures += 1
        retry_seconds = ranking_backoff_seconds(
            error.status,
            error.retry_after,
            self._ranking_limit_failures,
        )
        self._ranking_retry_until = (
            int(datetime.now(timezone.utc).timestamp()) + retry_seconds
        )
        self.ranking_store.set_collector_backoff(
            self._ranking_limit_failures,
            self._ranking_retry_until,
        )
        logging.warning(
            "Ranking target %s returned %s; collection paused for %s seconds.",
            error.target,
            error.status,
            retry_seconds,
        )
        return retry_seconds

    def clear_ranking_backoff_after_success(self) -> None:
        if not self._ranking_limit_failures:
            return
        self._ranking_limit_failures = 0
        self._ranking_retry_until = 0
        self.ranking_store.clear_collector_backoff()

    async def fetch_usd_exchange_rate(self) -> Decimal:
        # 네이버 금융 환율표는 JSON API가 아니므로 HTML에서 미국 USD 행만 읽습니다.
        assert self.session is not None
        async with self.session.get(
            USD_EXCHANGE_RATE_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            return parse_usd_exchange_rate(await response.text())

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

    async def send_server_open_alert(self, embed: discord.Embed) -> None:
        """채널마다 관리자가 고른 역할을 멘션해 서버 오픈을 알립니다."""
        for channel in self.alert_text_channels(ALERT_SERVER):
            role_id = self.server_alert_roles.get(str(channel.id))
            try:
                await channel.send(
                    content=f"<@&{role_id}>" if role_id is not None else None,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=True
                    ),
                )
            except discord.HTTPException:
                logging.exception("Failed to send server alert to %s.", channel.id)

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

    async def configure_info_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        info_type: str,
        enabled: bool,
    ) -> None:
        """관리자가 고른 음성 채널을 시간 또는 환율 표시 채널로 설정합니다."""
        if interaction.guild is None or not interaction.permissions.administrator:
            await interaction.response.send_message(
                "이 설정은 서버 관리자만 변경할 수 있습니다.", ephemeral=True
            )
            return
        if channel.guild.id != interaction.guild.id:
            await interaction.response.send_message(
                "현재 서버의 음성 채널만 선택할 수 있습니다.", ephemeral=True
            )
            return

        label = "시간 채널" if info_type == INFO_TIME else "환율 채널"
        already_enabled = channel.id in self.alert_channels[info_type]
        if enabled == already_enabled:
            state = "이미 켜져" if enabled else "이미 꺼져"
            await interaction.response.send_message(
                f"{channel.mention}의 {label} 자동 갱신이 {state} 있습니다.",
                ephemeral=True,
            )
            return

        if enabled:
            other_type = INFO_EXCHANGE if info_type == INFO_TIME else INFO_TIME
            if channel.id in self.alert_channels[other_type]:
                await interaction.response.send_message(
                    "같은 채널에 시간과 환율을 동시에 표시할 수 없습니다.", ephemeral=True
                )
                return
            bot_member = channel.guild.me
            permissions = channel.permissions_for(bot_member) if bot_member else None
            if permissions is None or not (
                permissions.view_channel and permissions.manage_channels
            ):
                await interaction.response.send_message(
                    "선택한 채널에서 봇의 채널 보기·채널 관리 권한을 확인해주세요.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        if enabled:
            try:
                if info_type == INFO_TIME:
                    name = format_time_channel_name(datetime.now(timezone.utc))
                else:
                    name = format_exchange_channel_name(
                        await self.fetch_usd_exchange_rate()
                    )
                await channel.edit(name=name, reason=f"{label} 자동 갱신 ON")
            except (aiohttp.ClientError, discord.HTTPException, TimeoutError, ValueError):
                logging.exception("Failed to enable %s for channel %s.", label, channel.id)
                await interaction.followup.send(
                    "채널 이름을 갱신하지 못했습니다. 권한이나 환율 페이지 상태를 확인해주세요.",
                    ephemeral=True,
                )
                return

        update_alert_channel(self.alert_channels, info_type, channel.id, enabled)
        self.persist_state()
        await interaction.followup.send(
            f"{channel.mention}의 {label} 자동 갱신을 "
            f"{'켰습니다' if enabled else '껐습니다'}.",
            ephemeral=True,
        )

    async def configure_alert_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        enabled: bool,
        alert_type: str,
        alert_name: str,
        role: discord.Role | None = None,
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

        if alert_type == ALERT_SERVER and enabled:
            if role is None:
                await interaction.response.send_message(
                    "서버 오픈 알림을 켤 때는 멘션할 역할을 선택해주세요.", ephemeral=True
                )
                return
            if role.guild.id != interaction.guild.id or role.is_default():
                await interaction.response.send_message(
                    "현재 서버의 일반 역할만 선택할 수 있습니다.", ephemeral=True
                )
                return

        already_enabled = channel.id in self.alert_channels[alert_type]
        same_server_role = (
            alert_type != ALERT_SERVER
            or not enabled
            or self.server_alert_roles.get(str(channel.id)) == role.id
        )
        if enabled == already_enabled and same_server_role:
            state = "이미 켜져" if enabled else "이미 꺼져"
            await interaction.response.send_message(
                f"{channel.mention}의 {alert_name}이 {state} 있습니다.", ephemeral=True
            )
            return

        if enabled:
            bot_member = channel.guild.me
            permissions = channel.permissions_for(bot_member) if bot_member else None
            needs_attachment = alert_type in {
                ALERT_SUNNY_DAY,
                ALERT_SUNNY_LIST,
                ALERT_CASH_TRANSFER,
                ALERT_URSUS,
            }
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
        elif enabled and alert_type == ALERT_EXCHANGE_LOG:
            try:
                self.exchange_log, _ = record_exchange_rate(
                    self.exchange_log,
                    await self.fetch_usd_exchange_rate(),
                    datetime.now(timezone.utc),
                )
                message = await channel.send(
                    embed=build_exchange_rate_log_embed(self.exchange_log)
                )
                self.exchange_log["message_ids"][str(channel.id)] = message.id
            except (aiohttp.ClientError, discord.HTTPException, TimeoutError, ValueError):
                logging.exception("Failed to enable exchange log for %s.", channel.id)
                await interaction.followup.send(
                    "선택한 채널에 환율 기록을 보내지 못했습니다.", ephemeral=True
                )
                return
        elif not enabled and alert_type == ALERT_SUNNY_DAY:
            await self.delete_sunny_day_message(channel)

        update_alert_channel(self.alert_channels, alert_type, channel.id, enabled)
        if alert_type == ALERT_SERVER:
            if enabled:
                self.server_alert_roles[str(channel.id)] = role.id
            else:
                self.server_alert_roles.pop(str(channel.id), None)
        self.persist_state()
        role_text = f" ({role.mention} 멘션)" if enabled and role is not None else ""
        await interaction.followup.send(
            f"{channel.mention}의 {alert_name}을 {'켰습니다' if enabled else '껐습니다'}{role_text}.",
            ephemeral=True,
        )

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def check_news(self) -> None:
        # 이 함수는 5분마다 자동 실행되는 봇의 핵심 작업입니다.
        posts = await self.fetch_posts()
        current_ids = {post["id"] for post in posts}
        latest_patch = next((post for post in posts if is_patch_notes(post)), None)
        latest_patch_detail = None

        # 새 기능을 처음 배포해도 이미 읽은 최신 점검 공지에서 감시 일정을 한 번 복원합니다.
        latest_maintenance = next(
            (
                post
                for post in posts
                if is_server_maintenance_post(post)
                and "[completed]" not in post.get("name", "").lower()
            ),
            None,
        )
        if latest_maintenance is not None and (
            self.maintenance_watch is None
            or self.maintenance_watch.get("post_id") != latest_maintenance["id"]
        ):
            maintenance_detail = await self.fetch_post_detail(latest_maintenance["id"])
            maintenance_watch = extract_maintenance_watch(
                latest_maintenance, maintenance_detail["body"]
            )
            if maintenance_watch is not None:
                self.maintenance_watch = maintenance_watch
                if self.sent_ids is not None:
                    self.persist_state()

        # 진행 중인 점검 공지가 수정되면 연장된 종료 시각도 5분 안에 반영합니다.
        if self.maintenance_watch is not None and not self.maintenance_watch.get(
            "completed", False
        ):
            maintenance_post = next(
                (
                    post
                    for post in posts
                    if post["id"] == self.maintenance_watch.get("post_id")
                ),
                None,
            )
            if maintenance_post is not None:
                maintenance_detail = await self.fetch_post_detail(maintenance_post["id"])
                updated_watch = extract_maintenance_watch(
                    maintenance_post, maintenance_detail["body"]
                )
                if updated_watch is not None:
                    merged_watch = merge_maintenance_watch(
                        self.maintenance_watch, updated_watch
                    )
                    if merged_watch != self.maintenance_watch:
                        self.maintenance_watch = merged_watch
                        if self.sent_ids is not None:
                            self.persist_state()

        # 목록은 최신순이므로 첫 Cash Shop Update가 현재 공식 캐시샵 공지입니다.
        latest_cash_shop_post = next(
            (post for post in posts if is_cash_shop_update(post)),
            None,
        )
        if latest_cash_shop_post is not None:
            latest_cash_shop = {
                "post_id": latest_cash_shop_post["id"],
                "title": latest_cash_shop_post["name"],
                "url": post_url(latest_cash_shop_post),
            }
            if (
                self.latest_cash_shop is None
                or self.latest_cash_shop.get("post_id") != latest_cash_shop_post["id"]
                or "items" not in self.latest_cash_shop
            ):
                detail = await self.fetch_post_detail(latest_cash_shop_post["id"])
                sections = extract_cash_shop_sections(detail["body"])
                latest_cash_shop["items"] = await self.translate_texts(sections)
            else:
                latest_cash_shop["items"] = self.latest_cash_shop["items"]
            if latest_cash_shop != self.latest_cash_shop:
                self.latest_cash_shop = latest_cash_shop
                # 기존 설치의 state.json에 새 항목을 추가할 때도 바로 저장합니다.
                if self.sent_ids is not None:
                    self.persist_state()

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
            is_maintenance = is_server_maintenance_post(post)
            sends_news = bool(self.alert_channels[ALERT_NEWS])
            if not sends_news and not is_sunny_patch and not is_maintenance:
                # 알림 채널이 없으면 불필요한 요약·번역 API를 호출하지 않고 기준점만 저장합니다.
                self.sent_ids.add(post["id"])
                self.persist_state()
                continue

            detail = await self.fetch_post_detail(post["id"])
            if is_maintenance:
                maintenance_watch = extract_maintenance_watch(post, detail["body"])
                if maintenance_watch is not None:
                    self.maintenance_watch = merge_maintenance_watch(
                        self.maintenance_watch, maintenance_watch
                    )
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
                    embed=build_cash_shop_transfer_embed(self.patch_events),
                    file=discord.File(CASH_SHOP_TRANSFER_IMAGE_PATH),
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

    @tasks.loop(minutes=1)
    async def check_ursus(self) -> None:
        # 시작·종료 시각의 첫 1분에만 알리고 채널별 마지막 알림을 저장해 중복을 막습니다.
        now = datetime.now(timezone.utc)
        event = ursus_boundary_event(now)
        if event is None:
            return

        event_type, start, end = event
        boundary = start if event_type == "start" else end
        event_key = f"{event_type}:{int(boundary.timestamp())}"
        embed, image_path = build_ursus_embed(
            "active" if event_type == "start" else "ended",
            (start, end),
            now,
        )
        state_changed = False
        for channel_id in sorted(self.alert_channels[ALERT_URSUS]):
            if self.ursus_alert_events.get(str(channel_id)) == event_key:
                continue
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                logging.warning("Ursus channel %s is not accessible.", channel_id)
                continue
            try:
                await channel.send(
                    embed=embed,
                    file=discord.File(image_path),
                )
            except discord.HTTPException:
                logging.exception("Failed to send Ursus alert to %s.", channel_id)
                continue
            self.ursus_alert_events[str(channel_id)] = event_key
            state_changed = True

        if state_changed:
            self.persist_state()

    @tasks.loop(minutes=1)
    async def check_server_status(self) -> None:
        # 평상시에는 시간만 확인하고 API를 호출하지 않습니다.
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        if not should_check_server_status(self.maintenance_watch, now_timestamp):
            return

        # API 오류는 점검으로 저장하지 않고 다음 1분 확인 때 다시 시도합니다.
        try:
            statuses = await self.fetch_server_status()
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            logging.warning("MapleStory server status check failed: %s", error)
            return

        current_status = "up" if all(statuses.values()) else "down"
        state_changed = current_status != self.server_status
        self.server_status = current_status
        if current_status == "down":
            if not self.maintenance_watch.get("saw_down", False):
                self.maintenance_watch["saw_down"] = True
                state_changed = True
        elif (
            self.maintenance_watch.get("saw_down", False)
            or (
                self.maintenance_watch.get("end_timestamp") is not None
                and now_timestamp >= self.maintenance_watch["end_timestamp"]
            )
        ):
            # 점검 시작 직후 서버가 잠깐 정상으로 잡히는 경우를 종료로 오인하지 않습니다.
            # 실제 점검 중 상태를 봤거나 예정 종료 시각이 지난 뒤 정상일 때만 알립니다.
            await self.send_server_open_alert(
                build_server_status_embed(statuses, opened=True)
            )
            # 오픈을 확인한 점검은 완료 처리해 다음 점검까지 API 요청을 멈춥니다.
            self.maintenance_watch["completed"] = True
            state_changed = True

        if state_changed:
            self.persist_state()

    async def rename_info_channels(self, info_type: str, name: str) -> None:
        """등록된 음성 채널 이름이 달라졌을 때만 변경합니다."""
        for channel_id in sorted(self.alert_channels[info_type]):
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                logging.warning("Info voice channel %s is not accessible.", channel_id)
                continue
            if channel.name == name:
                continue
            try:
                await channel.edit(name=name, reason="정보 채널 자동 갱신")
            except discord.HTTPException:
                logging.exception("Failed to rename info channel %s.", channel_id)

    async def update_exchange_log_messages(self) -> None:
        """등록 채널마다 하루 한 메시지만 만들고 이후에는 그 메시지를 수정합니다."""
        assert self.exchange_log is not None
        embed = build_exchange_rate_log_embed(self.exchange_log)
        for channel in self.alert_text_channels(ALERT_EXCHANGE_LOG):
            message_id = self.exchange_log["message_ids"].get(str(channel.id))
            try:
                if message_id is None:
                    message = await channel.send(embed=embed)
                    self.exchange_log["message_ids"][str(channel.id)] = message.id
                else:
                    await channel.get_partial_message(message_id).edit(embed=embed)
            except discord.NotFound:
                message = await channel.send(embed=embed)
                self.exchange_log["message_ids"][str(channel.id)] = message.id
            except discord.HTTPException:
                logging.exception("Failed to update exchange log in %s.", channel.id)

    @tasks.loop(minutes=10)
    async def update_time_channels(self) -> None:
        # Discord 채널 이름 변경 제한에 걸리지 않도록 10분마다 갱신합니다.
        if not self.alert_channels[INFO_TIME]:
            return
        await self.rename_info_channels(
            INFO_TIME, format_time_channel_name(datetime.now(timezone.utc))
        )

    @tasks.loop(minutes=10)
    async def update_exchange_channels(self) -> None:
        # 네이버 금융의 USD/KRW 환율을 한 번 읽어 모든 등록 채널에 표시합니다.
        if not (
            self.alert_channels[INFO_EXCHANGE]
            or self.alert_channels[ALERT_EXCHANGE_LOG]
        ):
            return
        try:
            rate = await self.fetch_usd_exchange_rate()
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            logging.warning("USD/KRW exchange rate check failed: %s", error)
            return
        if self.alert_channels[INFO_EXCHANGE]:
            await self.rename_info_channels(
                INFO_EXCHANGE, format_exchange_channel_name(rate)
            )
        if self.alert_channels[ALERT_EXCHANGE_LOG]:
            self.exchange_log, changed = record_exchange_rate(
                self.exchange_log, rate, datetime.now(timezone.utc)
            )
            missing_message = any(
                str(channel.id) not in self.exchange_log["message_ids"]
                for channel in self.alert_text_channels(ALERT_EXCHANGE_LOG)
            )
            if changed or missing_message:
                await self.update_exchange_log_messages()
                self.persist_state()

    @tasks.loop(seconds=RANKING_SCAN_INTERVAL_SECONDS)
    async def collect_rankings(self) -> None:
        """북미 주요 월드의 상위 랭킹을 번갈아 초당 10명씩 수집합니다."""
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        if now_timestamp < self._ranking_retry_until:
            return

        scan_date = datetime.now(URSUS_TIMEZONE).date()
        if self._ranking_scan_date != scan_date:
            self._ranking_scan_date = scan_date
            self._ranking_world_offset = 0
            self._completed_ranking_world_ids.clear()

        priority_name = self.ranking_store.next_priority_character(scan_date)
        if priority_name is not None:
            try:
                character = await self.fetch_ranking_character(
                    "na",
                    "overall",
                    "weekly",
                    priority_name,
                    rate_limit_target=priority_name,
                )
                if character is not None:
                    self.ranking_store.save_page(
                        [character],
                        scan_date,
                        next_index=1,
                        world_id=character["worldID"],
                        update_checkpoint=False,
                    )
                self.ranking_store.mark_priority_refreshed(priority_name, scan_date)
                self.clear_ranking_backoff_after_success()
                logging.info("Priority ranking refreshed: %s.", priority_name)
            except RankingRateLimited as error:
                self.pause_ranking_collection(error)
            except (
                aiohttp.ClientError,
                TimeoutError,
                ValueError,
                KeyError,
                OSError,
                sqlite3.Error,
            ):
                logging.exception("Priority ranking collection failed: %s.", priority_name)
            return

        self.ranking_store.prepare_active_pages(scan_date)
        active_page = self.ranking_store.next_active_page(scan_date)
        if active_page is not None:
            world_id, page_index = active_page
            try:
                payload = await self.fetch_ranking_page(world_id, page_index)
                ranks = payload.get("ranks", [])
                eligible = [
                    item for item in ranks
                    if item.get("level", 0) >= MIN_TRACKED_LEVEL
                ]
                self.ranking_store.save_page(
                    eligible,
                    scan_date,
                    next_index=page_index + len(ranks),
                    world_id=world_id,
                    update_checkpoint=False,
                    source_page_index=page_index,
                )
                self.ranking_store.mark_active_page_refreshed(
                    scan_date, world_id, page_index
                )
                self.clear_ranking_backoff_after_success()
                logging.info(
                    "Active ranking page refreshed: %s %s.",
                    RANKING_WORLDS[world_id],
                    page_index,
                )
            except RankingRateLimited as error:
                self.pause_ranking_collection(error)
            except (
                aiohttp.ClientError,
                TimeoutError,
                ValueError,
                KeyError,
                OSError,
                sqlite3.Error,
            ):
                logging.exception(
                    "Active ranking page collection failed: %s %s.",
                    world_id,
                    page_index,
                )
            return

        active_world_ids = [
            world_id
            for world_id in TRACKED_RANKING_WORLD_IDS
            if world_id not in self._completed_ranking_world_ids
        ]
        if not active_world_ids:
            await asyncio.sleep(60)
            return

        allocation, self._ranking_world_offset = allocate_ranking_pages(
            active_world_ids, self._ranking_world_offset
        )
        try:
            jobs = [
                scan_rankings(
                    lambda page_index, selected_world_id=world_id: self.fetch_ranking_page(
                        selected_world_id, page_index
                    ),
                    self.ranking_store,
                    scan_date,
                    max_characters=None,
                    max_pages=page_count,
                    scan_id=world_id,
                )
                for world_id, page_count in allocation.items()
            ]
            results = await asyncio.gather(*jobs)
            self.clear_ranking_backoff_after_success()
            for world_id, result in zip(allocation, results):
                if result["reason"] in {"already_completed", "level_boundary", "end"}:
                    self._completed_ranking_world_ids.add(world_id)

            saved = sum(result["saved"] for result in results)
            reasons = ", ".join(
                f"{RANKING_WORLDS[world_id]}={result['reason']}"
                for world_id, result in zip(allocation, results)
            )
            # 기본 그래프는 14일치지만, 나중에 30일 보기에도 쓸 수 있게 한 달간 보관합니다.
            self.ranking_store.remove_old_snapshots(scan_date - timedelta(days=30))
            # 페이지마다 DB에는 즉시 저장합니다. 별도 백업 파일은 1시간에 한 번만 만듭니다.
            now = datetime.now(timezone.utc)
            backup_rows = None
            if (
                self._last_ranking_backup_at is None
                or now - self._last_ranking_backup_at >= RANKING_BACKUP_INTERVAL
            ):
                backup_rows = await asyncio.to_thread(
                    self.ranking_store.backup_to,
                    RANKING_BACKUP_PATH,
                )
                self._last_ranking_backup_at = now
            logging.info(
                "Main-world rankings saved %s characters (%s); backup rows: %s.",
                saved,
                reasons,
                backup_rows,
            )
        except RankingRateLimited as error:
            self.pause_ranking_collection(error)
            self._ranking_world_offset = (
                self._ranking_world_offset - RANKING_PAGES_PER_BATCH
            ) % len(active_world_ids)
        except (aiohttp.ClientError, TimeoutError, ValueError, OSError, sqlite3.Error):
            # 중단 지점은 페이지마다 DB에 저장되므로 다음 실행에서 이어갈 수 있습니다.
            logging.exception("Main-world ranking collection failed.")

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

    @check_ursus.error
    async def check_ursus_error(self, error: Exception) -> None:
        logging.exception("Ursus schedule check failed.", exc_info=error)

    @check_server_status.error
    async def check_server_status_error(self, error: Exception) -> None:
        logging.exception("MapleStory server status task failed.", exc_info=error)

    @update_time_channels.error
    async def update_time_channels_error(self, error: Exception) -> None:
        logging.exception("Time channel update failed.", exc_info=error)

    @update_exchange_channels.error
    async def update_exchange_channels_error(self, error: Exception) -> None:
        logging.exception("Exchange channel update failed.", exc_info=error)

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

    @check_ursus.before_loop
    async def before_check_ursus(self) -> None:
        await self.wait_until_ready()

    @check_server_status.before_loop
    async def before_check_server_status(self) -> None:
        await self.wait_until_ready()

    @update_time_channels.before_loop
    async def before_update_time_channels(self) -> None:
        await self.wait_until_ready()

    @update_exchange_channels.before_loop
    async def before_update_exchange_channels(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    # .env 파일을 읽은 뒤 Discord 봇을 실행합니다.
    load_dotenv()
    MapleNewsBot(int(os.environ["DISCORD_CHANNEL_ID"])).run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
