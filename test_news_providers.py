import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from translation_corrections import protect_google_terms, restore_google_terms

from maple_bot import MapleNewsBot, format_news_summary


class NewsProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_summary_bullets_have_one_blank_line(self):
        source = '- **첫 항목**\n* 둘째\n\n\n• 셋째\n  이어지는 내용'
        expected = '- **첫 항목**\n\n- 둘째\n\n- 셋째\n  이어지는 내용'
        self.assertEqual(format_news_summary(source), expected)
        self.assertEqual(format_news_summary(expected), expected)

    async def test_google_request_restores_actual_glossary_terms(self):
        response = Mock()
        bot = SimpleNamespace(session=Mock(), google_api_key='test-only')
        async def translated_response():
            sent = bot.session.post.call_args.kwargs['json']['q']
            return {'data': {'translations': [{'translatedText': text} for text in sent]}}
        response.json = AsyncMock(side_effect=translated_response)
        bot.session.post.return_value = Mock(
            __aenter__=AsyncMock(return_value=response), __aexit__=AsyncMock(return_value=False))
        result = await MapleNewsBot.translate_texts(bot, ['Night Troupe: Omega Sector', 'PSSB'])
        self.assertEqual(result, ['나이트 트루프: 지구방위본부', '스스비'])
        self.assertNotIn('Night Troupe', bot.session.post.call_args.kwargs['json']['q'][0])

    def test_glossary_protects_long_names_plurals_and_context(self):
        texts = ['Geardock Familiars; Night Troupe: Omega Sector; PSSB; '
                 'Grand Sacred Symbols; Nodes; Premium Surprise Style Boxes',
                 'Pets unfamiliar timezone; Arcane Symbol; at dawn']
        protected, mappings = protect_google_terms(texts)
        self.assertNotIn('Geardock', protected[0])
        self.assertEqual(restore_google_terms(protected[0], mappings[0]),
                         '기어드락 퍼밀리어; 나이트 트루프: 지구방위본부; 스스비; '
                         '그랜드 어센틱심볼; 코어; 스스비')
        self.assertEqual(restore_google_terms(protected[1], mappings[1]),
                         'Pets unfamiliar timezone; 아케인심볼; at dawn')
        token = next(iter(mappings[0]))
        with self.assertRaisesRegex(ValueError, '누락되거나 변경'):
            restore_google_terms(protected[0].replace(token, ''), mappings[0])
        with self.assertRaisesRegex(ValueError, '중복'):
            restore_google_terms(protected[0] + token, mappings[0])

    def test_dm_correction_overrides_file_and_does_not_retranslate_replacement(self):
        protected, mappings = protect_google_terms(['night troupe Night Troupe'],
                                                   [('Night Troupe', '나이트 트룹', 'Night Troupe 행사')])
        self.assertEqual(restore_google_terms(protected[0], mappings[0]),
                         'Night Troupe 행사 Night Troupe 행사')

    async def test_summary_uses_previous_gpt_english_summary(self):
        bot = SimpleNamespace(openai=SimpleNamespace(responses=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(output_text='- English summary')))))
        result = await MapleNewsBot.summarize(bot, {'name': 'Patch', 'body': '<p>Source</p>'})
        self.assertEqual(result, '- English summary')
        request = bot.openai.responses.create.call_args.kwargs
        self.assertEqual(request['model'], 'gpt-5.6-luna')
        self.assertIn('English', request['instructions'])
        self.assertIn('Source', request['input'])

    async def test_google_translation_preserves_order_and_validates_response(self):
        response = Mock()
        response.json = AsyncMock()
        context = Mock(__aenter__=AsyncMock(return_value=response), __aexit__=AsyncMock(return_value=False))
        bot = SimpleNamespace(session=Mock(), google_api_key='test-only')
        bot.session.post.return_value = context
        response.json.return_value = {'data': {'translations': [
            {'translatedText': '첫째 &amp; 둘째'}, {'translatedText': '셋째'}]}}
        self.assertEqual(await MapleNewsBot.translate_texts(bot, ['one', 'two']), ['첫째 & 둘째', '셋째'])
        request = bot.session.post.call_args
        self.assertEqual(request.args[0], 'https://translation.googleapis.com/language/translate/v2')
        self.assertEqual(request.kwargs['json']['q'], ['one', 'two'])
        for values in ([], [{'translatedText': ''}], [{'translatedText': None}]):
            response.json.return_value = {'data': {'translations': values}}
            with self.assertRaises(ValueError):
                await MapleNewsBot.translate_texts(bot, ['one'])
        self.assertEqual(await MapleNewsBot.translate_texts(bot, []), [])
