import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from PIL import Image
import maple_bot as bot


class CharacterImageCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_good_image_survives_url_change_and_bad_download(self):
        output = io.BytesIO()
        Image.new('RGBA', (8, 8), 'red').save(output, format='PNG')
        good = output.getvalue()
        response = Mock(read=AsyncMock(return_value=good))
        session = Mock()
        session.get.return_value = Mock(__aenter__=AsyncMock(return_value=response),
                                       __aexit__=AsyncMock(return_value=False))
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, 'CHARACTER_IMAGE_CACHE_PATH', Path(folder)/'images.db'):
            client = SimpleNamespace(session=session)
            self.assertEqual(await bot.MapleNewsBot.fetch_character_image(client, 'https://example.com/old', cache_key='45:alice'), good)
            response.raise_for_status.side_effect = aiohttp.ClientConnectionError()
            # 새 객체에서도 디스크의 이전 이미지가 복원되고 다른 캐릭터에는 섞이지 않습니다.
            client = SimpleNamespace(session=session)
            self.assertEqual(await bot.MapleNewsBot.fetch_character_image(client, 'https://example.com/new', cache_key='45:alice'), good)
            self.assertIsNone(await bot.MapleNewsBot.fetch_character_image(client, None, cache_key='45:bob'))
            response.raise_for_status.side_effect = None
            response.read.return_value = b'not an image'
            self.assertEqual(await bot.MapleNewsBot.fetch_character_image(client, 'https://example.com/new', cache_key='45:alice'), good)
