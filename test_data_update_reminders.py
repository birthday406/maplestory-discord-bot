import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class UpdateReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_baseline_version_dedup_new_pssb_and_retry(self):
        from data_update_reminders import check_data_updates
        owner = SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(application_info=AsyncMock(return_value=SimpleNamespace(owner=owner)),
                                 fetch_post_detail=AsyncMock(return_value={'body': '<h1>ONGOING SALES</h1><h2>Premium Surprise Style Box</h2>'}))
        def posts(version, cash):
            return [{'id': version, 'name': f'v.{version} Patch Notes', 'category': 'update'},
                    {'id': cash, 'name': 'Cash Shop Update', 'category': 'sale'}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'reminders.json'
            await check_data_updates(client, posts(271, 1), path=path, now=0)
            owner.send.assert_not_awaited()
            await check_data_updates(client, posts(272, 2), path=path, now=301)
            self.assertEqual(owner.send.await_count, 1)
            self.assertIn('아이템검색 DB', owner.send.await_args.args[0])
            client.fetch_post_detail.return_value = {'body': '<h1>Premium Surprise Style Box</h1><h1>ONGOING SALES</h1>'}
            owner.send.side_effect = RuntimeError('temporary failure')
            with self.assertRaises(RuntimeError):
                await check_data_updates(client, posts(272, 2), path=path, now=602)
            owner.send.side_effect = None
            await check_data_updates(client, posts(272, 2), path=path, now=603)
            self.assertIn('스스비', owner.send.await_args.args[0])
            self.assertEqual(owner.send.await_count, 3)
            await check_data_updates(client, posts(272, 2), path=path, now=904)
            self.assertEqual(owner.send.await_count, 3)

    def test_only_new_pssb_headings_not_ongoing_or_mentions(self):
        from data_update_reminders import has_new_pssb
        self.assertTrue(has_new_pssb('<h1>Other</h1><h2>New Premium Surprise Style Boxes</h2>'))
        self.assertFalse(has_new_pssb('<p>Premium Surprise Style Box</p>'))
        self.assertFalse(has_new_pssb('<h1>ONGOING SALES</h1><h2>Premium Surprise Style Box</h2>'))
