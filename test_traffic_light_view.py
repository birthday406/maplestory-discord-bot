import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import maple_bot


class TrafficLightViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_gollux_hell_and_extreme_black_mage(self):
        interaction, view = await self.open_panel()
        result = await self.select(interaction, view.boss_select, '헬럭스')
        self.assertEqual(result['embed'].title, '<:ppojji_star_small:1548846409791574129> 헬럭스 5%')
        self.assertEqual([option.value for option in view.difficulty_select.options], ['헬'])
        self.assertEqual(result['embed'].thumbnail.url, 'attachment://boss-gollux.webp')
        await self.select(interaction, view.boss_select, '검마')
        self.assertIn('익스트림', [option.value for option in view.difficulty_select.options])
        result = await self.select(interaction, view.difficulty_select, '익스트림')
        self.assertEqual(result['embed'].title, '<:ppojji_star_small:1548846409791574129> 익스트림 검마 5%')
        self.assertIn('4,800,000,000,000K', result['embed'].description)
        self.assertIn('240,000,000,000K', result['embed'].description)

    async def open_panel(self):
        self.assertEqual(maple_bot.traffic_light_command.parameters, [])
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
            original_response=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        )
        await maple_bot.traffic_light_command.callback(interaction)
        view = interaction.response.send_message.await_args.kwargs['view']
        self.addCleanup(view.stop)
        return interaction, view

    async def select(self, interaction, select, value):
        select._values = [value]
        await select.callback(interaction)
        result = interaction.response.edit_message.await_args.kwargs
        for attachment in result.get('attachments', []):
            attachment.close()
        return result

    async def test_no_options_opens_group_and_switches_boss_and_difficulty(self):
        interaction, view = await self.open_panel()
        initial = interaction.response.send_message.await_args.kwargs
        self.assertEqual(initial['embed'].title, '<:ppojji_star_small:1548846409791574129> 검밑 보스 5%')
        self.assertTrue(view.difficulty_select.disabled)
        for boss in ('스우', '데미안', '루시드', '윌', '더스크', '진 힐라', '듄켈'):
            self.assertIn(boss, initial['embed'].description)
        self.assertEqual(len(view.boss_select.options), 12)

        result = await self.select(interaction, view.boss_select, '발드릭스')
        self.assertEqual(result['embed'].title, '<:ppojji_star_small:1548846409791574129> 노말 발드릭스 5%')
        self.assertFalse(view.difficulty_select.disabled)
        self.assertEqual([o.value for o in view.difficulty_select.options], ['노말', '하드'])
        result = await self.select(interaction, view.difficulty_select, '하드')
        self.assertIn('20,270,000,000,000K', result['embed'].description)
        self.assertIn('1,010,000,000,000K', result['embed'].description)
        self.assertEqual(result['embed'].thumbnail.url, 'attachment://' + result['attachments'][0].filename)

        result = await self.select(interaction, view.boss_select, '헬럭스')
        self.assertEqual(result['embed'].title, '<:ppojji_star_small:1548846409791574129> 헬럭스 5%')
        result = await self.select(interaction, view.boss_select, '검밑')
        self.assertEqual(result['attachments'], [])
        self.assertIsNone(result['embed'].thumbnail.url)
        self.assertTrue(view.difficulty_select.disabled)

    async def test_owner_and_expiry_guards(self):
        interaction, view = await self.open_panel()
        self.assertTrue(await view.interaction_check(interaction))
        interaction.user.id = 456
        self.assertFalse(await view.interaction_check(interaction))
        self.assertTrue(interaction.response.send_message.await_args.kwargs['ephemeral'])
        interaction.user.id = 123
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))
        self.assertIs(view.message.edit.await_args.kwargs['view'], view)
        self.assertFalse(await view.interaction_check(interaction))
        self.assertIn('/5퍼', interaction.response.send_message.await_args.args[0])
