import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from ai_score import calculate_ai_score
from maple_data import LEVEL_EXP


def profile(
    level,
    *,
    rank=100_000,
    union=8_000,
    union_rank=50_000,
    achievement=20_000,
    achievement_rank=350_000,
    activity_ratio=1.0,
):
    return {
        "level": level,
        "exp": 0 if level >= 300 else LEVEL_EXP[level - 200] // 2,
        "ranking": rank,
        "level_population": 786_171,
        "legion_level": union,
        "legion_rank": union_rank,
        "legion_population": 250_000,
        "achievement_score": achievement,
        "achievement_rank": achievement_rank,
        "achievement_population": 1_750_000,
        "recent_7d_exp": round(2_000_000_000_000 * activity_ratio),
        "activity_reference_exp": 2_000_000_000_000,
        "activity_sample_days": 14,
    }


CASES = {
    "A 초기": profile(265, rank=400_000, union=2_000, union_rank=220_000,
                    achievement=8_000, achievement_rank=1_600_000, activity_ratio=0),
    "B 일반": profile(280),
    "C 유니온 특화": profile(280, union=11_000, union_rank=2_500),
    "D 업적 특화": profile(285, rank=50_000, achievement=38_000,
                         achievement_rank=1_750),
    "E 균형 성장": profile(290, rank=15_000, union=10_000, union_rank=12_500,
                         achievement=32_000, achievement_rank=17_500,
                         activity_ratio=1.3),
    "F 비활동 고레벨": profile(295, rank=5_000, union=10_000, union_rank=12_500,
                            achievement=32_000, achievement_rank=17_500,
                            activity_ratio=0),
    "G 활동 특화": profile(275, rank=180_000, activity_ratio=3),
    "H 레벨 몰빵": profile(299, rank=500, union=2_000, union_rank=220_000,
                         achievement=10_000, achievement_rank=1_200_000),
    "I 극상위": profile(299, rank=10, union=11_500, union_rank=100,
                      achievement=39_000, achievement_rank=500,
                      activity_ratio=2.5),
    "J 최정상": profile(300, rank=1, union=12_000, union_rank=1,
                      achievement=40_000, achievement_rank=1,
                      activity_ratio=3),
}


for name, data in CASES.items():
    result = calculate_ai_score(data, LEVEL_EXP)
    print(f"{name:<12} {result['ai_score']:>5.2f}  coverage {result['coverage']:>5.1f}%")
