import unittest
from PIL import Image, ImageDraw
import maple_bot as bot


class RankingTagTests(unittest.TestCase):
    def test_regions_and_alt_first(self):
        for level, name in [(260, '세르니움 입성'), (265, '아르크스 장기투숙'),
                            (270, '오디움 현장직'), (275, '도원경 산책러'),
                            (280, '아르테리아 승선객'), (285, '카르시온 주민'),
                            (290, '탈라하트 개척자'), (295, '기어드락 터줏대감')]:
            for value in (level, min(level + 4, 300)):
                tags = bot.maple_index_tag_badges(30, {'character_growth': 90, 'union_growth': 0, 'achievement': 0},
                                                  level=value, is_alt_character=True)
                self.assertEqual(tags[0], ('부캐', 0.0))
                self.assertEqual(len(tags), 3)
                self.assertIn(name, [tag for tag, _ in tags])

    def test_highest_fields_and_unique_tags(self):
        tags = bot.maple_index_tag_badges(65, {'character_growth': 40, 'union_growth': 99, 'achievement': 85}, level=280)
        self.assertEqual(tags[0], ('내가 바로 군단이다', 99))
        self.assertEqual(len(set(name for name, _ in tags)), 3)
        self.assertEqual([score for _, score in tags], sorted([score for _, score in tags], reverse=True))

    def test_every_profile_has_three_and_long_future_names_are_ellipsized(self):
        for level in (250, 260, 264, 295, 299, 300):
            for scores in ((0, 0, 0), (40, 40, 40), (80, 80, 20), (90, 20, 90), (100, 100, 100)):
                indices = dict(zip(('character_growth', 'union_growth', 'achievement'), scores))
                for alt in (False, True):
                    tags = bot.maple_index_tag_badges(sum(scores)/3, indices, level=level, is_alt_character=alt)
                    self.assertEqual(len(tags), 3)
                    self.assertEqual(len(set(name for name, _ in tags)), 3)
        draw = ImageDraw.Draw(Image.new('RGB', (1800, 1528)))
        _, labels, widths = bot.fit_ranking_tags(draw, ['아주긴이름' * 20] * 3, 772, 2)
        self.assertEqual(len(labels), 3)
        self.assertTrue(all(label.endswith('…') for label in labels))
        self.assertLessEqual(sum(widths) + 20, 772)

    def test_long_tags_fit_all_three(self):
        draw = ImageDraw.Draw(Image.new('RGB', (1800, 1528)))
        names = ['이것이 나의 최종 형태다', '기어드락 터줏대감', '본캐 자리 넘보는 중']
        font, labels, widths = bot.fit_ranking_tags(draw, names, 772, 2)
        self.assertEqual(labels, names)
        self.assertEqual(len(widths), 3)
        self.assertLessEqual(sum(widths) + 20, 772)
