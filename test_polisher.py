import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from PIL import Image
import maple_bot as bot


class PolisherTests(unittest.IsolatedAsyncioTestCase):
    def test_all_stone_counts_match_new_probability_limits(self):
        for level, limit, step in [(4, 10, 10), (5, 20, 5)]:
            for count in range(1, limit + 1):
                rate = count * step
                self.assertTrue(bot.simulate_seed_ring(level, count, roll=rate)['success'])
                self.assertEqual(bot.simulate_seed_ring(level, count, roll=100)['success_rate'], rate)
                if rate < 100:
                    self.assertFalse(bot.simulate_seed_ring(level, count, roll=rate + 1)['success'])
            self.assertTrue(bot.simulate_seed_ring(level, limit, roll=100)['success'])
            for invalid in (0, limit + 1):
                with self.assertRaises(ValueError):
                    bot.simulate_seed_ring(level, invalid)

    async def test_level_switch_caps_count_and_preserves_totals(self):
        view = bot.SeedRingSimulatorView(1, 5, 20)
        self.addCleanup(view.stop)
        self.assertEqual(len(view.stone_select.options), 20)
        view.draw()
        view.level_select._values = ['4']
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.level_select.callback(interaction)
        self.assertEqual((view.level, view.stone_count, view.stones_used), (4, 10, 20))
        self.assertEqual(len(view.stone_select.options), 10)

    def test_outer_edges_have_no_transparent_notch_at_panel_join(self):
        from polisher_ui import render_polisher
        with Image.open(render_polisher(4, 5, 50, None)) as result:
            # 모서리가 중간에 다시 나타나면 좌우 안쪽 테두리에 투명한 틈이 생깁니다.
            for x in (3, 414):
                for y in range(218, 242):
                    self.assertGreaterEqual(result.getpixel((x, y))[3], 230, (x, y))

    async def test_command_opens_without_drawing(self):
        self.assertEqual(bot.seed_ring_command.name, 'polisher')
        self.assertEqual(bot.seed_ring_command.parameters, [])
        interaction = SimpleNamespace(user=SimpleNamespace(id=1),
            response=SimpleNamespace(send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            original_response=AsyncMock())
        with patch('maple_bot.random.randint') as roll:
            await bot.seed_ring_command.callback(interaction)
            roll.assert_not_called()
        data = interaction.response.send_message.await_args.kwargs
        interaction.followup.send.assert_not_awaited()
        self.assertEqual(data['view'].attempts, 0)
        self.assertEqual(data['view'].stone_count, 1)
        self.assertEqual([option.value for option in data['view'].stone_select.options if option.default], ['1'])
        self.assertIn('생명의 연마석 1개', data['content'])
        self.assertIn('강화 전', data['content'])
        self.assertEqual(data['file'].filename, 'polisher.png')
        data['view'].stop()

    async def test_changed_counts_are_added_not_multiplied(self):
        view = bot.SeedRingSimulatorView(1, 4, 1)
        self.addCleanup(view.stop)
        with patch('maple_bot.random.randint', side_effect=[100, 1]):
            view.draw()
            view.stone_count = 5
            view.draw()
        self.assertEqual((view.attempts, view.successes, view.stones_used), (2, 1, 6))
        self.assertEqual(view.level, 4)  # 같은 조건의 독립 추첨을 유지합니다.
        with Image.open(view.image()) as result:
            self.assertEqual(result.size, (418, 342))
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())
        view.level_select._values = ['5']
        await view.level_select.callback(interaction)
        self.assertIsNone(view.result)
        self.assertEqual(view.stones_used, 6)
        self.assertIn('신념의 연마석', interaction.response.edit_message.await_args.kwargs['content'])
        interaction.followup.send.assert_not_awaited()

    async def test_expired_controls_reject_owner(self):
        view = bot.SeedRingSimulatorView(1, 4, 5)
        self.addCleanup(view.stop)
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        interaction = SimpleNamespace(user=SimpleNamespace(id=1),
            response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))
        view.message.edit.assert_awaited_once()
        self.assertTrue(all(c.disabled for c in view.children))

    async def test_owner_can_control_fixed_screen(self):
        view = bot.SeedRingSimulatorView(1)
        self.addCleanup(view.stop)
        view.message = SimpleNamespace(id=2)
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), message=SimpleNamespace(id=1),
            response=SimpleNamespace(send_message=AsyncMock()))
        self.assertTrue(await view.interaction_check(interaction))
        self.assertEqual(view.attempts, 0)

    async def test_controls_only_draw_when_enhance_is_pressed(self):
        view = bot.SeedRingSimulatorView(1, 5, 5)
        self.addCleanup(view.stop)
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock())
        view.stone_select._values = ['2']
        await view.stone_select.callback(interaction)
        self.assertEqual(view.attempts, 0)
        with patch('maple_bot.random.randint', return_value=10), patch('polisher_ui.render_polisher_animation', return_value=(b'GIF89a', 0)):
            await view.retry.callback(interaction)
        self.assertTrue(view.result['success'])
        self.assertEqual(view.result['success_rate'], 10)
        payload = interaction.edit_original_response.await_args.kwargs
        self.assertEqual(payload['attachments'][0].filename, 'polisher.png')
        self.assertIn('강화 성공', payload['content'])
        interaction.followup.send.assert_not_awaited()
        with patch('maple_bot.random.randint', return_value=11), patch('polisher_ui.render_polisher_animation', return_value=(b'GIF89a', 0)):
            await view.retry.callback(interaction)
        self.assertFalse(view.result['success'])
        self.assertEqual((view.attempts, view.successes, view.stones_used), (2, 1, 4))
