import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import maple_bot as bot


class CommandHelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_buttons_show_shared_documents_privately(self):
        import json
        from pathlib import Path
        data = json.loads((Path(bot.__file__).parent / 'website/dist/policies.json').read_text(encoding='utf-8'))
        view = bot.HelpView(1)
        self.addCleanup(view.stop)
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        for key in ('terms', 'privacy'):
            button = next(child for child in view.children if getattr(child, 'custom_id', '') == 'policy:' + key)
            await button.callback(interaction)
            result = interaction.response.send_message.call_args.kwargs
            self.assertTrue(result['ephemeral'])
            self.assertIn(data[key]['title'], result['embed'].title)
            for section in data[key]['sections']:
                self.assertIn(section['body'], result['embed'].description)
            self.assertLessEqual(len(result['embed'].description), 4096)

    async def test_help_opens_privately_and_all_categories_return_home(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()))
        await bot.help_command.callback(interaction)
        sent = interaction.response.send_message.call_args.kwargs
        self.assertTrue(sent['ephemeral'])
        view = sent['view']
        self.addCleanup(view.stop)
        self.assertNotIn('/심볼계산기', sent['embed'].description)
        self.assertEqual(set(bot.HELP_INTROS), set(bot.HELP_CATEGORIES))
        self.assertTrue(await view.interaction_check(interaction))
        stranger = SimpleNamespace(user=SimpleNamespace(id=2), response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(stranger))
        self.assertTrue(stranger.response.send_message.call_args.kwargs['ephemeral'])
        for category, rows in bot.HELP_CATEGORIES.items():
            view.category._values = [category]
            await view.category.callback(interaction)
            result = interaction.response.edit_message.call_args.kwargs
            text = result['embed'].description
            for name, _ in rows:
                self.assertIn(name, text)
            self.assertIn(bot.HELP_EXAMPLES[category], text)
            self.assertNotIn('/공지알림', text)
            self.assertLessEqual(len(text), 4096)
            self.assertEqual([o.value for o in view.category.options if o.default], [category])
            self.assertIs(result['view'], view)
        view.category._values = ['home']
        await view.category.callback(interaction)
        self.assertEqual(interaction.response.edit_message.call_args.kwargs['embed'].to_dict(), sent['embed'].to_dict())
        self.assertEqual(view.timeout, 900)

    async def test_admin_help_rechecks_permission_and_rejects_dm(self):
        for guild, allowed in [(None, True), (object(), False), (object(), True)]:
            interaction = SimpleNamespace(guild=guild, permissions=SimpleNamespace(administrator=allowed),
                                          response=SimpleNamespace(send_message=AsyncMock()))
            await bot.admin_help_command.callback(interaction)
            sent = interaction.response.send_message.call_args
            self.assertTrue(sent.kwargs['ephemeral'])
            if guild is not None and allowed:
                self.assertIn('/채널설정', str(sent.kwargs['embed'].to_dict()))
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
