import unittest
import discord
from news_embed_repair import corrected_embed


class NewsRepairTests(unittest.TestCase):
    def test_overview_names_with_korean_particles(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/update/44597/v-271-test',
                              description='시아 아스텔과 에렐 라이트에 스킬 추가. 럭스 사우나가 포함됩니다. 럭스사우나도 포함됩니다. 노드북은 유지.')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '시아 아스텔과 에릴 라이트에 스킬 추가. VIP 사우나가 포함됩니다. VIP 사우나도 포함됩니다. 노드북은 유지.')

    def test_patch_overview_glossary(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/update/44597/v-271-maplestory-x-frieren-beyond-journey-s-end-patch-notes',
            description='210~259 레벨 / 아케인/신성 심볼 / 기어독 패밀리어 / 보너스 스탯 / HEXA 공통 노드 / 에렐 라이트 / 초즌 세렌 / 칼링 / 새로운 이벤트 및 협업 / 오메가 섹터 / 챌린저 월드 도약 / 럭스 사우나')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '210~259 레벨 / 아케인/어센틱심볼 / 기어드락 퍼밀리어 / 추가옵션 / HEXA 공통 코어 / 에릴 라이트 / 선택받은 세렌 / 카링 / 새로운 이벤트 및 콜라보레이션 / 지구방위본부 / 챌린저 월드 리프 / VIP 사우나')

    def test_signature_face_terms(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/sale/44291/signature-style-collection',
                              description='헤어, 페이스, 의상 / 헤어 및 페이스 쿠폰 / 7,900 NX')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '헤어, 얼굴, 의상 / 헤어 및 얼굴 쿠폰 / 7,900 NX')

    def test_patch_skill_names_preserve_damage_and_fields(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/update/44597/v-271-patch-notes',
                              description='모현의 진정한 거미 반사 990% → 2,228%; 진거미 반사 385% → 859%')
        embed.add_field(name='태양 문장', value='화염 문장 / 에르다 샤워/분수 / 에르다 샤워/샘 / 태양 야누스 / 순환 고리 / 원시 수정')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '묵현의 스파이더 인 미러 990% → 2,228%; 스파이더 인 미러 385% → 859%')
        self.assertEqual(result.fields[0].name, '크레스트 오브 더 솔라')
        self.assertEqual(result.fields[0].value, '불꽃의 문양 / 에르다 샤워/파운틴 / 에르다 샤워/파운틴 / 솔 야누스 / 순환의 고리 / 태초의 결정')
        self.assertIsNone(corrected_embed(result))

    def test_companions_burning_field(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/events/44415/adventure-with-frieren-s-companions',
                              description='펀의 공격 / 스타크 / 불타는 들판 스테이지 +1 쿠폰')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '페른의 공격 / 슈타르크 / 버닝 필드 단계 +1 쿠폰')

    def test_spell_collection_boss_names_only(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/events/44414/frieren-s-spell-collection',
                              description='카오스 벨럼(스우), 수호천사 슬라임, 루시드 / 1,000마리')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '카오스 벨룸(스우), 가디언 엔젤 슬라임, 루시드 / 1,000마리')
        self.assertIsNone(corrected_embed(result))

    def test_signature_names_with_particles(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/sale/44291/signature-style-collection',
                              description='프리에렌, 펀, 스타크, 뤼그너, 리니에 / 프리에렌의 지팡이 또는 펀의 지팡이 / 7,900 NX')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '프리렌, 페른, 슈타르크, 류그너, 리니에 / 프리렌의 지팡이 또는 페른의 지팡이 / 7,900 NX')
        self.assertIsNone(corrected_embed(result))

    def test_philosophers_book_corrections_preserve_stats(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/sale/44314/philosopher-s-book',
                              description='철학자의 책: 2,600 NX / 프리에렌 토템 +10 / 프리렌, 펀, 위벨')
        result = corrected_embed(embed)
        self.assertIsNotNone(result)
        self.assertEqual(result.description, '필로소퍼 북: 2,600 NX / 프리렌 토템 +10 / 프리렌, 페른, 위벨')
        self.assertIsNone(corrected_embed(result))

    def test_only_target_names_change_and_repeat_is_noop(self):
        embed = discord.Embed(url='https://www.nexon.com/maplestory/news/sale/44348/special-petite-luna-pets',
                              description='릴 프리렌, 릴 펀, 릴 스타크, 릴 위벨 · 4,000 NX')
        embed.set_image(url='https://example.com/image.png')
        result = corrected_embed(embed)
        self.assertEqual(result.description, '릴 프리렌, 릴 페른, 릴 슈타르크, 릴 위벨 · 4,000 NX')
        self.assertEqual(result.image.url, embed.image.url)
        self.assertIsNone(corrected_embed(result))
        embed.url = 'https://www.nexon.com/maplestory/news/sale/123/other'
        self.assertIsNone(corrected_embed(embed))
