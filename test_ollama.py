import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock

from maple_bot import MapleNewsBot


class OllamaTests(unittest.IsolatedAsyncioTestCase):
    def bot_with_response(self, content, reason="stop"):
        bot = object.__new__(MapleNewsBot)
        bot.ollama_api_key = "test-only-key"
        bot.correction_store = Mock()
        bot.correction_store.glossary.return_value = ''
        response = Mock()
        response.json = AsyncMock(return_value={
            "done": True, "done_reason": reason,
            "message": {"content": content},
        })
        context = Mock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        bot.session = Mock()
        bot.session.post.return_value = context
        return bot

    async def test_patch_chat_uses_cloud_and_returns_korean_directly(self):
        bot = self.bot_with_response("- 생명의 연마석은 최대 10개입니다.")
        result = await bot.ollama_chat("Answer in Korean.", "Up to 10 stones.")
        self.assertEqual(result, "- 생명의 연마석은 최대 10개입니다.")
        args, kwargs = bot.session.post.call_args
        self.assertEqual(args[0], "https://ollama.com/api/chat")
        self.assertEqual(kwargs["json"]["model"], "nemotron-3-ultra")
        self.assertFalse(kwargs["json"]["stream"])
        self.assertLessEqual(kwargs["timeout"].total, 120)

    async def test_saved_corrections_are_in_ollama_requests(self):
        import tempfile
        from pathlib import Path
        from translation_corrections import CorrectionStore
        with tempfile.TemporaryDirectory() as directory:
            store = CorrectionStore(Path(directory) / 'terms.db')
            store.save('Ring Polish Swap', '폴리시 스왑', '반지 연마 전수')
            for translate in [False, True]:
                bot = self.bot_with_response('{"translations":["반지 연마 전수"]}' if translate else '반지 연마 전수')
                bot.correction_store = store
                if translate:
                    await bot.ollama_chat('Translate.', 'Ring Polish Swap', json_output=True)
                else:
                    await bot.ollama_chat('Answer.', 'Ring Polish Swap')
                messages = bot.session.post.call_args.kwargs['json']['messages']
                self.assertIn('반지 연마 전수', messages[0]['content'])

    async def test_glossary_file_is_sent_in_ollama_requests(self):
        import tempfile
        from pathlib import Path
        from translation_corrections import CorrectionStore
        with tempfile.TemporaryDirectory() as directory:
            for translate in (False, True):
                bot = self.bot_with_response('{"translations":["퍼밀리어"]}' if translate else '퍼밀리어')
                bot.correction_store = CorrectionStore(Path(directory) / 'terms.db')
                if translate:
                    await bot.ollama_chat('Translate.', 'Familiar in Geardock', json_output=True)
                else:
                    await bot.ollama_chat('Answer.', 'Familiar in Geardock')
                request = bot.session.post.call_args.kwargs['json']
                self.assertIn('퍼밀리어', request['messages'][0]['content'])
                self.assertIn('기어드락', request['messages'][0]['content'])
                self.assertNotIn('카링', request['messages'][0]['content'])
                self.assertEqual(bot.session.post.call_count, 1)

    async def test_empty_input_does_not_call_api(self):
        bot = self.bot_with_response("")
        self.assertEqual(await bot.translate_texts([]), [])
        bot.session.post.assert_not_called()

    async def test_empty_or_truncated_generation_is_rejected(self):
        for content, reason in [("", "stop"), ("partial", "length")]:
            bot = self.bot_with_response(content, reason)
            with self.assertRaises(ValueError):
                await bot.ollama_chat('Answer.', 'Text')

    async def test_timeout_is_not_swallowed(self):
        bot = self.bot_with_response("")
        bot.session.post.return_value.__aenter__.side_effect = asyncio.TimeoutError
        with self.assertRaises(asyncio.TimeoutError):
            await bot.ollama_chat('Answer.', 'Text')

    async def test_http_failure_is_not_swallowed(self):
        bot = self.bot_with_response("")
        response = await bot.session.post.return_value.__aenter__()
        response.raise_for_status.side_effect = RuntimeError("HTTP failure")
        with self.assertRaises(RuntimeError):
            await bot.ollama_chat('Answer.', 'Text')
