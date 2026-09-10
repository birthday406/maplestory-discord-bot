import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
import maple_bot as bot
import polisher_ui


class AnimationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_returns_to_static_result_on_fixed_message(self):
        view = bot.SeedRingSimulatorView(1, 4, 5)
        self.addCleanup(view.stop)
        uploads = []

        old = SimpleNamespace(id=1, delete=AsyncMock(), edit=AsyncMock())
        sent = []
        view.message = old

        async def record(**payload):
            self.assertEqual(set(payload), {'content', 'view', 'attachments'})
            uploads.append((payload['attachments'][0].filename, view.attempts))
            message = SimpleNamespace(id=len(sent) + 2, delete=AsyncMock(), edit=AsyncMock())
            sent.append(message)
            return message

        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(side_effect=record))
        with patch('polisher_ui.render_polisher_animation', return_value=(b'GIF89a', 0)), patch('maple_bot.random.randint', return_value=1):
            await view.retry.callback(interaction)
            await view.retry.callback(interaction)
        self.assertEqual(uploads, [('polisher.gif', 1), ('polisher.png', 1), ('polisher.gif', 2), ('polisher.png', 2)])
        old.delete.assert_not_awaited()
        self.assertIs(view.message, old)
        interaction.followup.send.assert_not_awaited()
        old.edit.assert_not_awaited()
        self.assertEqual((view.attempts, view.successes, view.stones_used), (2, 2, 10))

    async def test_effect_end_shows_static_result_and_enables_buttons(self):
        view = bot.SeedRingSimulatorView(1, 4, 5)
        self.addCleanup(view.stop)
        messages = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def edit_image(**payload):
            self.assertEqual(set(payload), {'content', 'view', 'attachments'})
            filename = payload['attachments'][0].filename
            messages.append((filename, all(c.disabled for c in view.children)))
            if filename == 'polisher.gif':
                entered.set()
                await release.wait()
            return SimpleNamespace(id=2, delete=AsyncMock(), edit=AsyncMock())

        interaction = SimpleNamespace(user=SimpleNamespace(id=1),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(side_effect=edit_image))
        with patch('polisher_ui.render_polisher_animation', return_value=(b'GIF89a', 1.44), create=True), patch('maple_bot.random.randint', return_value=1), patch('maple_bot.asyncio.sleep', new_callable=AsyncMock) as sleep:
            task = asyncio.create_task(view.retry.callback(interaction))
            try:
                # 애니메이션 전송이 없으면 실패하며 무한정 기다리지 않습니다.
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(await view.interaction_check(interaction))
                view.level_select._values = ['5']
                await view.level_select.callback(interaction)
                self.assertEqual(view.level, 4)
                await view.retry.callback(interaction)
                self.assertEqual(view.attempts, 1)
            finally:
                release.set()
                await task
            sleep.assert_awaited_once_with(1.44)
        self.assertEqual(messages, [('polisher.gif', True), ('polisher.png', False)])
        self.assertEqual(interaction.edit_original_response.await_count, 2)
        interaction.followup.send.assert_not_awaited()
        self.assertFalse(any(c.disabled for c in view.children))
        self.assertFalse(view.busy)
        self.assertEqual(view.stones_used, 5)

    async def test_render_failure_falls_back_to_same_result(self):
        view = bot.SeedRingSimulatorView(1, 5, 2)
        self.addCleanup(view.stop)
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock())
        with patch('polisher_ui.render_polisher_animation', side_effect=OSError('missing frame'), create=True), patch('maple_bot.random.randint', return_value=100):
            await view.retry.callback(interaction)
        self.assertEqual((view.attempts, view.successes, view.stones_used), (1, 0, 2))
        payload = interaction.edit_original_response.await_args.kwargs
        self.assertEqual(payload['attachments'][0].filename, 'polisher.png')
        self.assertIn('강화 실패', payload['content'])
        interaction.followup.send.assert_not_awaited()
        self.assertFalse(any(c.disabled for c in view.children))

    async def test_failed_send_keeps_previous_message(self):
        view = bot.SeedRingSimulatorView(1)
        self.addCleanup(view.stop)
        old = SimpleNamespace(id=1, delete=AsyncMock())
        view.message = old
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(side_effect=OSError('offline')))
        with patch('polisher_ui.render_polisher_animation', return_value=(b'GIF89a', 0)):
            with self.assertRaises(OSError):
                await view.retry.callback(interaction)
        self.assertIs(view.message, old)
        old.delete.assert_not_awaited()
        self.assertFalse(view.busy)

    def test_real_gif_has_timing_and_static_result_last(self):
        self.assertTrue(hasattr(polisher_ui, 'render_polisher_animation'))
        for level, count, success in [(4, 1, True), (5, 5, False), (4, 10, True), (5, 20, True)]:
            data, duration = polisher_ui.render_polisher_animation(level, count, 10 if level == 4 else 25, success)
            with Image.open(io.BytesIO(data)) as gif:
                self.assertTrue(gif.is_animated)
                self.assertEqual(gif.size, (418, 342))
                self.assertNotIn('loop', gif.info)  # 실제 봇에서는 무한 반복하지 않습니다.
                self.assertGreaterEqual(gif.info['duration'], 500)
                total = 0
                for n in range(gif.n_frames):
                    gif.seek(n); total += gif.info['duration']
                self.assertAlmostEqual(total / 1000, duration + 2)
                self.assertGreaterEqual(gif.info['duration'], 2000)
                self.assertGreater(gif.n_frames, 12)
