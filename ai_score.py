import math
from collections.abc import Sequence


RANK_CURVE = (
    (0.0001, 99.0),
    (0.001, 97.0),
    (0.005, 92.0),
    (0.01, 88.0),
    (0.05, 75.0),
    (0.10, 65.0),
    (0.25, 45.0),
    (0.50, 25.0),
    (1.00, 0.0),
)
UNION_CURVE = (
    (0, 0.0),
    (4_000, 10.0),
    (6_000, 25.0),
    (8_000, 50.0),
    (9_000, 65.0),
    (10_000, 82.0),
    (10_500, 90.0),
    (11_000, 96.0),
    (12_000, 100.0),
)
ACHIEVEMENT_CURVE = (
    (0, 0.0),
    (10_000, 10.0),
    (15_000, 25.0),
    (20_000, 45.0),
    (25_000, 65.0),
    (30_000, 82.0),
    (35_000, 94.0),
    (40_000, 100.0),
)
ACTIVITY_CURVE = (
    (0.00, 0.0),
    (0.25, 10.0),
    (0.50, 25.0),
    (0.75, 40.0),
    (1.00, 50.0),
    (1.25, 60.0),
    (1.50, 70.0),
    (2.00, 85.0),
    (2.50, 93.0),
    (3.00, 100.0),
)
WEIGHTS = {
    "character_growth": 20.0,
    "level_rank": 15.0,
    "union_growth": 12.5,
    "union_rank": 10.0,
    "achievement": 10.0,
    "achievement_rank": 7.5,
    "activity": 10.0,
    "completeness": 10.0,
    "rarity": 4.9,
}
CORE_INDICES = {
    "character_growth",
    "level_rank",
    "union_growth",
    "union_rank",
    "achievement",
    "achievement_rank",
    "completeness",
}


def interpolate(value: float, anchors: Sequence[tuple[float, float]]) -> float:
    value = max(anchors[0][0], min(value, anchors[-1][0]))
    for (left_x, left_y), (right_x, right_y) in zip(anchors, anchors[1:]):
        if value <= right_x:
            ratio = (value - left_x) / (right_x - left_x)
            return left_y + (right_y - left_y) * ratio
    return anchors[-1][1]


def rank_index(rank: int | None, population: int | None) -> float | None:
    if not rank or not population or rank < 1 or population < rank:
        return None
    if rank == 1:
        return 100.0
    fraction = rank / population
    if fraction <= RANK_CURVE[0][0]:
        return 99.0
    if fraction >= 0.5:
        return interpolate(fraction, RANK_CURVE)
    log_anchors = tuple((math.log10(x), y) for x, y in RANK_CURVE[:-1])
    return interpolate(math.log10(fraction), log_anchors)


def character_growth_index(
    level: int,
    exp: int,
    level_exp: Sequence[int],
) -> float:
    if level < 260:
        return 0.0
    if level >= 300:
        return 100.0
    current_required = level_exp[level - 200]
    exp_fraction = min(1.0, max(0.0, exp / current_required))
    level_progress = ((level - 260) + exp_fraction) / 40 * 100
    earned_exp = sum(level_exp[60 : level - 200]) + current_required * exp_fraction
    total_exp = sum(level_exp[60:100])
    exp_progress = earned_exp / total_exp * 100
    return level_progress * 0.4 + exp_progress * 0.6


def union_growth_index(level: int | None) -> float | None:
    return None if level is None else interpolate(level, UNION_CURVE)


def achievement_index(score: int | None) -> float | None:
    return None if score is None else interpolate(score, ACHIEVEMENT_CURVE)


def activity_index(recent_exp: int | None, reference_exp: int | None) -> float | None:
    if recent_exp is None or not reference_exp or reference_exp < 0:
        return None
    return interpolate(recent_exp / reference_exp, ACTIVITY_CURVE)


def completeness_index(character: float, union: float, achievement: float) -> float:
    values = (character, union, achievement)
    geometric_mean = math.prod(values) ** (1 / 3)
    balance = max(0.0, 100 - (max(values) - min(values)))
    return geometric_mean * 0.5 + min(values) * 0.3 + balance * 0.2


def calculate_ai_score(
    profile: dict,
    level_exp: Sequence[int],
    *,
    rarity_index: float | None = None,
) -> dict:
    """AI Score v0.1과 조정 가능한 0~100 지표별 내역을 반환합니다."""
    character = character_growth_index(profile["level"], profile.get("exp", 0), level_exp)
    union = union_growth_index(profile.get("legion_level"))
    achievement = achievement_index(profile.get("achievement_score"))
    indices = {
        "character_growth": character,
        "level_rank": rank_index(profile.get("ranking"), profile.get("level_population")),
        "union_growth": union,
        "union_rank": rank_index(
            profile.get("legion_rank"), profile.get("legion_population")
        ),
        "achievement": achievement,
        "achievement_rank": rank_index(
            profile.get("achievement_rank"), profile.get("achievement_population")
        ),
        "activity": (
            activity_index(
                profile.get("recent_7d_exp"), profile.get("activity_reference_exp")
            )
            if profile.get("activity_sample_days", 0) >= 14
            else None
        ),
        "completeness": (
            completeness_index(character, union, achievement)
            if union is not None and achievement is not None
            else None
        ),
        "rarity": rarity_index,
    }
    missing_core = sorted(name for name in CORE_INDICES if indices[name] is None)
    available = {name: value for name, value in indices.items() if value is not None}
    available_weight = sum(WEIGHTS[name] for name in available)
    weighted_points = sum(
        value * WEIGHTS[name] / 100 for name, value in available.items()
    )
    points = {
        name: None if value is None else round(value * WEIGHTS[name] / 100, 3)
        for name, value in indices.items()
    }
    score = None
    if not missing_core and available_weight:
        score = min(99.9, weighted_points / available_weight * 99.9)
    return {
        "ai_score": None if score is None else round(score, 2),
        "provisional": score is not None and available_weight < 99.9,
        "coverage": round(available_weight / 99.9 * 100, 1),
        "indices": {
            name: None if value is None else round(value, 2)
            for name, value in indices.items()
        },
        "points": points,
        "missing": missing_core,
    }
