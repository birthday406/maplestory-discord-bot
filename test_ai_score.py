import unittest

from ai_score import (
    achievement_index,
    calculate_ai_score,
    character_growth_index,
    rank_index,
    union_growth_index,
)
from maple_data import LEVEL_EXP


LEVEL_POPULATION = 786_171
UNION_POPULATION = 250_000
ACHIEVEMENT_POPULATION = 1_750_000


def profile(
    level: int,
    *,
    progress: float = 0.5,
    rank: int = 100_000,
    union: int = 8_000,
    union_rank: int = 50_000,
    achievement: int = 20_000,
    achievement_rank: int = 350_000,
    activity_ratio: float | None = 1.0,
) -> dict:
    return {
        "level": level,
        "exp": 0 if level >= 300 else round(LEVEL_EXP[level - 200] * progress),
        "ranking": rank,
        "level_population": LEVEL_POPULATION,
        "legion_level": union,
        "legion_rank": union_rank,
        "legion_population": UNION_POPULATION,
        "achievement_score": achievement,
        "achievement_rank": achievement_rank,
        "achievement_population": ACHIEVEMENT_POPULATION,
        "recent_7d_exp": (
            None if activity_ratio is None else round(2_000_000_000_000 * activity_ratio)
        ),
        "activity_reference_exp": 2_000_000_000_000,
        "activity_sample_days": 14,
    }


class AIScoreTests(unittest.TestCase):
    def test_configured_anchor_values(self) -> None:
        self.assertEqual(union_growth_index(8_000), 50)
        self.assertEqual(union_growth_index(10_500), 90)
        self.assertEqual(achievement_index(30_000), 82)
        self.assertEqual(rank_index(1, LEVEL_POPULATION), 100)
        self.assertAlmostEqual(
            rank_index(LEVEL_POPULATION // 100, LEVEL_POPULATION), 88, places=2
        )

    def test_character_growth_uses_real_level_exp_table(self) -> None:
        result = character_growth_index(
            295,
            LEVEL_EXP[95] // 2,
            LEVEL_EXP,
        )

        self.assertAlmostEqual(result, 57.81, places=2)

    def test_score_waits_for_required_population_data(self) -> None:
        incomplete = profile(290)
        incomplete["level_population"] = None

        result = calculate_ai_score(incomplete, LEVEL_EXP)

        self.assertIsNone(result["ai_score"])
        self.assertIn("level_rank", result["missing"])

    def test_activity_waits_for_fourteen_days_of_reference_data(self) -> None:
        data = profile(290, activity_ratio=3)
        data["activity_sample_days"] = 13

        result = calculate_ai_score(data, LEVEL_EXP)

        self.assertIsNone(result["indices"]["activity"])
        self.assertTrue(result["provisional"])

    def test_a_to_j_profiles_follow_expected_order(self) -> None:
        cases = {
            "A": profile(265, rank=400_000, union=2_000, union_rank=220_000,
                         achievement=8_000, achievement_rank=1_600_000, activity_ratio=0),
            "B": profile(280, rank=100_000, union=8_000, union_rank=50_000,
                         achievement=20_000, achievement_rank=350_000, activity_ratio=1),
            "C": profile(280, rank=100_000, union=11_000, union_rank=2_500,
                         achievement=20_000, achievement_rank=350_000, activity_ratio=1),
            "D": profile(285, rank=50_000, union=8_000, union_rank=50_000,
                         achievement=38_000, achievement_rank=1_750, activity_ratio=1),
            "E": profile(290, rank=15_000, union=10_000, union_rank=12_500,
                         achievement=32_000, achievement_rank=17_500, activity_ratio=1.3),
            "F": profile(295, rank=5_000, union=10_000, union_rank=12_500,
                         achievement=32_000, achievement_rank=17_500, activity_ratio=0),
            "G": profile(275, rank=180_000, union=8_000, union_rank=50_000,
                         achievement=20_000, achievement_rank=350_000, activity_ratio=3),
            "H": profile(299, rank=500, union=2_000, union_rank=220_000,
                         achievement=10_000, achievement_rank=1_200_000, activity_ratio=1),
            "I": profile(299, rank=10, union=11_500, union_rank=100,
                         achievement=39_000, achievement_rank=500, activity_ratio=2.5),
            "J": profile(300, rank=1, union=12_000, union_rank=1,
                         achievement=40_000, achievement_rank=1, activity_ratio=3),
        }
        scores = {
            name: calculate_ai_score(data, LEVEL_EXP)["ai_score"]
            for name, data in cases.items()
        }

        self.assertLess(scores["A"], scores["B"])
        self.assertGreater(scores["C"], scores["B"])
        self.assertGreater(scores["E"], scores["B"])
        self.assertGreater(scores["F"], scores["B"])
        self.assertLess(scores["G"], scores["F"])
        self.assertLess(scores["H"], 95)
        self.assertGreater(scores["I"], 95)
        self.assertEqual(scores["J"], 99.9)


if __name__ == "__main__":
    unittest.main()
