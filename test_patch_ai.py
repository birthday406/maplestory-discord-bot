import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from patch_ai import PatchHistory, patch_text, patch_diff, validated_answer
import maple_bot


class PatchTests(unittest.TestCase):
    def test_revision_labels_are_fixed_without_changing_body(self):
        from patch_ai import normalize_revision_labels
        source = '- **추가됨:** 피해량 900% → 2,430%\n* **추가사항**: 신규 스킬\n추가 사항: 보상\n- 변경 사항: 수치\n- 수정됨: 조건\n- 제거됨: 아이템\n- 삭제됨: 목록\n추가 보상을 지급합니다.\n스킬 설명의 변경 사항: 유지'
        expected = '- **추가:** 피해량 900% → 2,430%\n* **추가**: 신규 스킬\n추가: 보상\n- 변경: 수치\n- 변경: 조건\n- 삭제: 아이템\n- 삭제: 목록\n추가 보상을 지급합니다.\n스킬 설명의 변경 사항: 유지'
        self.assertEqual(normalize_revision_labels(source), expected)
        self.assertEqual(normalize_revision_labels(expected), expected)

    def test_html_cosmetic_changes_do_not_trigger_diff(self):
        self.assertEqual(patch_diff(patch_text('<p>A  &amp; B</p>'), patch_text('<div>A &amp; B</div>')), '')
        self.assertIn('20%', patch_diff('Rate 10%', 'Rate 20%'))

    def test_baseline_pending_and_delivery_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'patch.db'
            store = PatchHistory(path)
            store.observe(1, 'Title', 'link', '<p>Old text</p>', [10, 20])
            self.assertEqual(store.pending(), [])
            store.observe(1, '[Updated] Title', 'link', '<p>New text</p>', [10, 20])
            item = store.pending()[0]
            store.set_summary(item['id'], '수정: New text')
            store.mark_sent(item['id'], 10)
            store = PatchHistory(path)
            store.observe(1, '[Updated] Title', 'link', '<p>New text</p>', [10, 20])
            self.assertEqual(len(store.pending()), 1)
            self.assertEqual(store.pending()[0]['sent'], [10])
            self.assertEqual(store.pending()[0]['summary'], '수정: New text')
            store.mark_sent(item['id'], 20)
            self.assertEqual(store.pending(), [])

    def test_empty_fetch_does_not_destroy_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PatchHistory(Path(folder) / 'patch.db')
            store.observe(1, 'Title', 'link', '<p>Old</p>', [1])
            with self.assertRaises(ValueError):
                store.observe(1, 'Title', 'link', '', [1])
            store.observe(1, 'Title', 'link', '<p>New</p>', [1])
            self.assertIn('Old', store.pending()[0]['changes'])

    def test_answer_requires_real_source_quote(self):
        result = json.dumps({'answer': '20% 증가', 'evidence': ['Damage increased by 20%.']})
        self.assertEqual(validated_answer(result, 'Damage increased by 20%.')[0], '20% 증가')
        self.assertIn('확인되지', validated_answer(result, 'Other text')[0])

    def test_sunny_removes_only_requested_lines(self):
        text = '- 몬스터 파크 클리어 경험치 250% 증가 (익몬 제외)\n- 몬스터 파크 익스트림은 제외됩니다.\n- 최상급 장비는 제외되며, 보호용으로 사용되는 메조는 30% 할인 대상에서 제외됩니다.\n- 메조 2배'
        self.assertEqual(maple_bot.localize_sunny_sunday_text(text), '- 몬스터 파크 클리어 경험치 250% 증가 (익몬 제외)\n- 메소 2배')
        self.assertEqual(maple_bot.known_sunny_sunday_translation('Monster Park Extreme is excluded.'), '')


class PatchFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision_retries_only_failed_channel_without_resummarizing(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PatchHistory(Path(folder) / 'patch.db')
            post = {'id': 1, 'name': 'v.271 Test Patch Notes', 'category': 'update'}
            ok = SimpleNamespace(id=10, send=AsyncMock())
            failed = SimpleNamespace(id=20, send=AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason='failure'), 'failed')))
            client = SimpleNamespace(fetch_posts=AsyncMock(return_value=[post]),
                fetch_post_detail=AsyncMock(return_value={'body': '<p>Rate 10%</p>'}),
                alert_text_channels=lambda kind: [ok, failed], saved_categories={'update'},
                patch_history=store,
                openai=SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(
                    return_value=SimpleNamespace(output_text='Changed: 10% → 20%')))),
                translate_texts=AsyncMock(return_value=['수정: 10% → 20%']))
            await maple_bot.MapleNewsBot.poll_patch_revisions(client)
            ok.send.assert_not_awaited()
            client.fetch_post_detail.return_value = {'body': '<p>Rate 20%</p>'}
            await maple_bot.MapleNewsBot.poll_patch_revisions(client)
            failed.send.side_effect = None
            client.patch_history = PatchHistory(Path(folder) / 'patch.db')
            await maple_bot.MapleNewsBot.poll_patch_revisions(client)
            ok.send.assert_awaited_once()
            self.assertEqual(failed.send.await_count, 2)
            client.openai.responses.create.assert_awaited_once()
            client.translate_texts.assert_awaited_once_with(['Changed: 10% → 20%'])
            self.assertEqual(ok.send.call_args.kwargs['embed'].description, '변경: 10% → 20%')
            self.assertEqual(failed.send.call_args.kwargs['embed'].description, '변경: 10% → 20%')
            self.assertEqual(store.pending(), [])

    async def test_question_uses_latest_source_and_releases_busy_flag(self):
        post = {'id': 1, 'name': 'v.271 Test Patch Notes', 'category': 'update'}
        client = SimpleNamespace(_patch_question_busy=False,
            fetch_posts=AsyncMock(return_value=[post]),
            fetch_post_detail=AsyncMock(return_value={'body': '<h2>Night Walker</h2><p>Damage increased by 20%.</p>'}),
            ollama_chat=AsyncMock(return_value=json.dumps({'answer': '20% 증가', 'evidence': ['Damage increased by 20%.']})))
        interaction = SimpleNamespace(client=client, response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        modal = maple_bot.PatchQuestionModal()
        modal.question._value = '나이트워커 변경점?'
        await modal.on_submit(interaction)
        embed = interaction.followup.send.call_args.kwargs['embed']
        self.assertEqual(embed.description, '20% 증가')
        self.assertIn('Night Walker', client.ollama_chat.call_args.args[1])
        self.assertFalse(client._patch_question_busy)
        client.ollama_chat.side_effect = ValueError('invalid response')
        await modal.on_submit(interaction)
        self.assertFalse(client._patch_question_busy)
        self.assertIn('실패', interaction.followup.send.call_args.args[0])

    async def test_watch_loop_catches_timeout(self):
        import asyncio
        client = SimpleNamespace(poll_patch_revisions=AsyncMock(side_effect=asyncio.TimeoutError))
        await maple_bot.MapleNewsBot.check_patch_revisions.coro(client)
