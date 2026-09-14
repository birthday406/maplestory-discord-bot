import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import maple_bot as bot


class CommandHelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_general_help_shows_all_categories_privately_without_menu(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(send_message=AsyncMock()))
        await bot.help_command.callback(interaction)
        sent = interaction.response.send_message.call_args.kwargs
        self.assertTrue(sent['ephemeral'])
        self.assertLessEqual(len(sent['embed'].fields), 6)
        self.assertNotIn('/공지알림', str(sent['embed'].to_dict()))
        self.assertNotIn('/패치질문', str(sent['embed'].to_dict()))
        self.assertNotIn('view', sent)
        self.assertIn('/심볼계산기', str(sent['embed'].to_dict()))
        for rows in bot.HELP_CATEGORIES.values():
            for name, _ in rows:
                self.assertIn(name, str(sent['embed'].to_dict()))
        self.assertLess(len(sent['embed']), 6000)

    async def test_admin_help_rechecks_permission_and_rejects_dm(self):
        for guild, allowed in [(None, True), (object(), False), (object(), True)]:
            interaction = SimpleNamespace(guild=guild, permissions=SimpleNamespace(administrator=allowed),
                                          response=SimpleNamespace(send_message=AsyncMock()))
            await bot.admin_help_command.callback(interaction)
            sent = interaction.response.send_message.call_args
            self.assertTrue(sent.kwargs['ephemeral'])
            if guild is not None and allowed:
                self.assertIn('/공지알림', str(sent.kwargs['embed'].to_dict()))
            else:
                self.assertNotIn('embed', sent.kwargs)


class EmbedTitleLimitTests(unittest.TestCase):
    def test_long_official_title_fits_without_duplicate_star(self):
        from embed_style import embed_title, STAR_EMOJI
        title = embed_title("가" * 300)
        self.assertLessEqual(len(title), 256)
        self.assertTrue(title.startswith(STAR_EMOJI + " "))
        self.assertEqual(embed_title(title), title)
        self.assertIsNone(embed_title(None))

    def test_existing_custom_emoji_is_preserved_without_a_star(self):
        from embed_style import embed_title
        title = "<:HEXA:1534436226751529031> HEXA 강화 계산"
        self.assertEqual(embed_title(title), title)
