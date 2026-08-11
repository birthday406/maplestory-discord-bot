"""Discord와 무관하게 사용할 수 있는 메이플스토리 계산 함수입니다."""

import random
from datetime import date, timedelta

from maple_data import (
    EPIC_DUNGEON_BONUSES,
    EPIC_DUNGEONS,
    EXP_COUPON_BURNING_OPTIONS,
    EXP_COUPONS,
    EXTREME_GROWTH_POTION_RATES,
    GROWTH_POTIONS,
    HEXA_CORE_COSTS,
    LEVEL_EXP,
    ELANOS_SYMBOL_BONUS_END,
    SYMBOL_REGIONS,
    SYMBOL_TYPES,
)


def simulate_extreme_growth_potions(
    current_level: int, count: int
) -> tuple[int, list[int]]:
    """익성비를 무작위로 사용하고 최종 레벨과 각 추첨 결과를 반환합니다."""
    if not 130 <= current_level <= 199:
        raise ValueError("시작 레벨은 130~199여야 합니다.")
    if count < 1:
        raise ValueError("비약 개수는 1개 이상이어야 합니다.")

    level_gains = []
    while len(level_gains) < count and current_level < 200:
        # 현재 레벨 표의 1~10레벨 상승 확률 중 하나를 그대로 추첨합니다.
        level_gain = random.choices(
            range(1, 11),
            weights=EXTREME_GROWTH_POTION_RATES[current_level],
            k=1,
        )[0]
        level_gains.append(level_gain)
        current_level = min(current_level + level_gain, 200)

    return current_level, level_gains


def calculate_hexa_cost(
    core_type: str, current_level: int, target_level: int
) -> tuple[int, int]:
    # 배열 인덱스가 강화 시작 레벨과 같으므로 현재 레벨부터 목표 레벨 직전까지 더합니다.
    if not 0 <= current_level < target_level <= 30:
        raise ValueError("현재 레벨은 목표 레벨보다 낮아야 하며 레벨 범위는 0~30입니다.")

    sol_erda_costs, fragment_costs = HEXA_CORE_COSTS[core_type]
    return (
        sum(sol_erda_costs[current_level:target_level]),
        sum(fragment_costs[current_level:target_level]),
    )


def calculate_growth_potions(
    potion_name: str,
    current_level: int,
    current_exp_percent: float,
    count: int,
    hyper_burning: bool = False,
    beyond_burning: bool = False,
) -> tuple[int, int, int, int]:
    """사용 후 레벨, 보유 경험치, 총 지급 경험치, 실제 사용 개수를 계산합니다."""
    if potion_name not in GROWTH_POTIONS:
        raise ValueError("지원하지 않는 성장의 비약입니다.")
    if not 200 <= current_level < 300:
        raise ValueError("시작 레벨은 200~299여야 합니다.")
    if not 0 <= current_exp_percent < 100:
        raise ValueError("현재 경험치는 0 이상 100 미만이어야 합니다.")
    if count < 1:
        raise ValueError("비약 개수는 1개 이상이어야 합니다.")

    potion_max_level, fixed_exp = GROWTH_POTIONS[potion_name]
    current_exp = int(LEVEL_EXP[current_level - 200] * current_exp_percent / 100)
    total_gained_exp = 0
    used_count = 0

    for _ in range(count):
        gained_exp = fixed_exp
        if current_level <= potion_max_level:
            gained_exp = LEVEL_EXP[current_level - 200]
        current_exp += gained_exp
        total_gained_exp += gained_exp
        used_count += 1

        while current_level < 300 and current_exp >= LEVEL_EXP[current_level - 200]:
            current_exp -= LEVEL_EXP[current_level - 200]
            if hyper_burning and current_level < 260:
                current_level = min(current_level + 5, 260)
            elif beyond_burning and 260 <= current_level < 270:
                current_level = min(current_level + 2, 270)
            else:
                current_level += 1

        if current_level == 300:
            current_exp = 0
            break

    return current_level, current_exp, total_gained_exp, used_count


def calculate_exp_coupons(
    coupon_name: str,
    current_level: int,
    current_exp_percent: float,
    count: int,
    burning: str = "X",
) -> tuple[int, int, int, int]:
    """쿠폰 사용 후 레벨, 보유 경험치, 총 지급 경험치, 실제 사용 개수를 계산합니다."""
    if coupon_name not in EXP_COUPONS:
        raise ValueError("지원하지 않는 EXP 교환권입니다.")
    if not 200 <= current_level < 300:
        raise ValueError("시작 레벨은 200~299여야 합니다.")
    if not 0 <= current_exp_percent < 100:
        raise ValueError("현재 경험치는 0 이상 100 미만이어야 합니다.")
    if count < 1:
        raise ValueError("교환권 개수는 1개 이상이어야 합니다.")
    if burning not in EXP_COUPON_BURNING_OPTIONS:
        raise ValueError("지원하지 않는 버닝 종류입니다.")

    minimum_level, coupon_exp_by_level = EXP_COUPONS[coupon_name]
    maximum_level = minimum_level + len(coupon_exp_by_level) - 1
    if not minimum_level <= current_level <= maximum_level:
        raise ValueError(
            f"{coupon_name}은 Lv.{minimum_level}~{maximum_level}에서 사용할 수 있습니다."
        )

    current_exp = int(LEVEL_EXP[current_level - 200] * current_exp_percent / 100)
    total_gained_exp = 0
    used_count = 0

    # 레벨마다 필요한 쿠폰을 한 번에 계산하므로 수백만 개를 입력해도 최대 100번만 반복합니다.
    while used_count < count and current_level <= maximum_level:
        coupon_exp = coupon_exp_by_level[current_level - minimum_level]
        required_exp = LEVEL_EXP[current_level - 200] - current_exp
        coupons_to_level_up = (required_exp + coupon_exp - 1) // coupon_exp
        applied_count = min(count - used_count, coupons_to_level_up)
        gained_exp = applied_count * coupon_exp

        current_exp += gained_exp
        total_gained_exp += gained_exp
        used_count += applied_count

        if current_exp < LEVEL_EXP[current_level - 200]:
            break

        current_exp -= LEVEL_EXP[current_level - 200]
        if burning == "하이퍼버닝" and current_level < 260:
            current_level = min(current_level + 5, 260)
        elif burning == "비욘드버닝" and 260 <= current_level < 270:
            current_level = min(current_level + 2, 270)
        else:
            current_level += 1
        if current_level == 300:
            current_exp = 0
            break

    return current_level, current_exp, total_gained_exp, used_count


def calculate_epic_dungeon(
    dungeon_name: str,
    current_level: int,
    current_exp_percent: float,
    experience_bonus: float,
) -> tuple[int, int, int, int]:
    """던전 완료 후 레벨, 보유 경험치, 기본 경험치, 적용 경험치를 반환합니다."""
    if dungeon_name not in EPIC_DUNGEONS:
        raise ValueError("지원하지 않는 에픽 던전입니다.")
    if not 260 <= current_level < 300:
        raise ValueError("시작 레벨은 260~299여야 합니다.")
    if not 0 <= current_exp_percent < 100:
        raise ValueError("현재 경험치는 0 이상 100 미만이어야 합니다.")
    if experience_bonus not in EPIC_DUNGEON_BONUSES:
        raise ValueError("경험치 보너스는 1.5배, 2배, 2.5배만 선택할 수 있습니다.")

    dungeon = EPIC_DUNGEONS[dungeon_name]
    minimum_level = dungeon["minimum_level"]
    if current_level < minimum_level:
        raise ValueError(f"{dungeon_name}은 Lv.{minimum_level}부터 입장할 수 있습니다.")

    # Lv.294 이상은 표의 마지막 고정 경험치를 사용합니다.
    table_level = min(current_level, 294)
    base_exp = dungeon["experience"][table_level - minimum_level]
    gained_exp = int(base_exp * experience_bonus)
    current_exp = int(LEVEL_EXP[current_level - 200] * current_exp_percent / 100)
    current_exp += gained_exp

    while current_level < 300 and current_exp >= LEVEL_EXP[current_level - 200]:
        current_exp -= LEVEL_EXP[current_level - 200]
        current_level += 1
    if current_level == 300:
        current_exp = 0

    return current_level, current_exp, base_exp, gained_exp


def calculate_symbol(
    region: str,
    current_level: int,
    current_growth: int,
    target_level: int,
    potion_level: int,
    elanos_applied: bool,
    start_date: date | None = None,
) -> tuple[int, int, int, int, int, date]:
    """필요 심볼, 메소, 평소·선택 조건 획득량, 소요일과 완료일을 반환합니다."""
    if region not in SYMBOL_REGIONS:
        raise ValueError("지원하지 않는 심볼 지역입니다.")

    region_info = SYMBOL_REGIONS[region]
    symbol = SYMBOL_TYPES[region_info["symbol_type"]]

    maximum_level = symbol["maximum_level"]
    if not 1 <= current_level < target_level <= maximum_level:
        raise ValueError(
            f"현재 레벨은 목표 레벨보다 낮아야 하며 레벨 범위는 1~{maximum_level}입니다."
        )
    if not 0 <= potion_level <= 6:
        raise ValueError("보약 레벨은 0~6이어야 합니다.")

    growth = symbol["growth"]
    maximum_storable_growth = sum(growth[current_level - 1 :])
    if not 0 <= current_growth <= maximum_storable_growth:
        raise ValueError(
            f"현재 성장치는 0~{maximum_storable_growth:,} 사이여야 합니다."
        )

    required_symbols = max(
        0,
        sum(growth[current_level - 1 : target_level - 1]) - current_growth,
    )
    meso_cost = sum(
        region_info["meso_costs"][current_level - 1 : target_level - 1]
    )

    base_daily_symbols = symbol["daily_symbols"]
    potion_bonus = symbol["potion_bonus"][potion_level]
    selected_daily_symbols = base_daily_symbols + potion_bonus
    if elanos_applied:
        # 게임 보상은 정수이므로 (기본 지급량 + 보약) × 120%의 소수점은 버립니다.
        selected_daily_symbols = selected_daily_symbols * 120 // 100

    current_date = start_date or date.today()
    completion_date = current_date
    remaining_symbols = required_symbols
    required_days = 0
    while remaining_symbols > 0:
        daily_symbols = base_daily_symbols
        if completion_date <= ELANOS_SYMBOL_BONUS_END.date():
            daily_symbols = selected_daily_symbols
        remaining_symbols -= daily_symbols
        required_days += 1
        if remaining_symbols > 0:
            completion_date += timedelta(days=1)

    return (
        required_symbols,
        meso_cost,
        base_daily_symbols,
        selected_daily_symbols,
        required_days,
        completion_date,
    )
