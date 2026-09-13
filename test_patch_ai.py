import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from patch_ai import PatchHistory, patch_text, patch_diff, validated_answer
import maple_bot


class PatchTests(unittest.TestCase):
    def test_revision_saves_heading_and_table_context_at_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PatchHistory(Path(folder) / 'patch.db')
            prefix = '<h2>Frieren Event</h2><h3>Grimoire Collection Missions</h3>' + '<p>Other mission</p>' * 30
            old = prefix + '<table><tr><td>Star Force Grimoire</td><td>Star Catching success then 30 enhancements</td></tr></table>'
            new = old.replace('Star Catching success then 30 enhancements', '30 enhancements')
            store.observe(1, 'v.271', 'link', old, [10])
            store.observe(1, 'v.271', 'link', new, [10])
            item = store.pending()[0]
            self.assertIn('Frieren Event', item['context'])
            self.assertIn('Grimoire Collection Missions', item['context'])
            self.assertIn('Star Force Grimoire', item['context'])
            store.observe(1, 'v.271', 'link', '<p>Unrelated later revision</p>', [10])
            self.assertEqual(store.pending()[0]['context'], item['context'])

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
    async def test_known_issues_still_runs_when_patch_news_fetch_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            known_store = PatchHistory(Path(folder) / 'known-issues.db')
            client = SimpleNamespace(
                fetch_posts=AsyncMock(side_effect=maple_bot.aiohttp.ClientError()),
                fetch_known_issues_article=AsyncMock(return_value={
                    'id': 2,
                    'title': 'Known Issues – v.271 - Frieren',
                    'html_url': 'https://support-maplestory.nexon.com/hc/en-us/articles/2',
                    'body': '<h2>Current Known Issues</h2><p>Issue</p>',
                }),
                alert_text_channels=lambda kind: [],
                saved_categories={'update'},
                patch_history=PatchHistory(Path(folder) / 'patch.db'),
                known_issues_history=known_store,
                openai=SimpleNamespace(),
                correction_store=None,
            )

            await maple_bot.MapleNewsBot.poll_patch_revisions(client)

            client.fetch_known_issues_article.assert_awaited_once_with()
            database = sqlite3.connect(known_store.path)
            try:
                snapshot = database.execute(
                    'SELECT body FROM snapshots WHERE post_id=?', (2,)
                ).fetchone()
            finally:
                database.close()
            self.assertEqual(snapshot, ('Current Known Issues\nIssue',))

    async def test_known_issues_revision_is_sent_to_news_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            store = PatchHistory(Path(folder) / 'known-issues.db')
            article = {
                'id': 2,
                'title': 'Known Issues – v.271 - Frieren',
                'html_url': 'https://support-maplestory.nexon.com/hc/en-us/articles/2',
                'body': '<h2>Current Known Issues</h2><p>Old issue</p>',
            }
            channel = SimpleNamespace(id=10, send=AsyncMock())
            client = SimpleNamespace(
                fetch_posts=AsyncMock(return_value=[]),
                fetch_known_issues_article=AsyncMock(return_value=article),
                alert_text_channels=lambda kind: [channel],
                saved_categories={'general', 'update'},
                patch_history=PatchHistory(Path(folder) / 'patch.db'),
                known_issues_history=store,
                openai=SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(
                    return_value=SimpleNamespace(output_text='추가:\n새 문제')))),
                correction_store=None,
            )

            await maple_bot.MapleNewsBot.poll_patch_revisions(client)
            channel.send.assert_not_awaited()
            article['body'] = '<h2>Current Known Issues</h2><p>Old issue</p><p>New issue</p>'
            await maple_bot.MapleNewsBot.poll_patch_revisions(client)

            channel.send.assert_awaited_once()
            request = client.openai.responses.create.await_args.kwargs
            self.assertIn('Known Issues', request['instructions'])
            self.assertIn('marked resolved', request['instructions'])
            embed = channel.send.await_args.kwargs['embed']
            self.assertEqual(embed.author.name, 'MapleStory | KNOWN ISSUES UPDATE')
            self.assertEqual(embed.title, '⚠️ v.271 알려진 문제 추가 수정')
            self.assertIn('[공식 Known Issues 확인]', embed.description)
            self.assertEqual(store.pending(), [])

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
                    return_value=SimpleNamespace(output_text='수정: 10% → 20%')))),
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
            client.translate_texts.assert_not_awaited()
            self.assertIn('Korean', client.openai.responses.create.call_args.kwargs['instructions'])
            request = client.openai.responses.create.call_args.kwargs
            self.assertIn('이전 주변 문맥:', request['input'])
            self.assertIn('Rate 10%', request['input'])
            self.assertIn('Rate 20%', request['input'])
            self.assertIn('Do not include a 핵심', request['instructions'])
            embed = ok.send.call_args.kwargs['embed']
            self.assertEqual(embed.author.name, 'MapleStory | PATCH UPDATE')
            self.assertEqual(embed.title, '📝 v.271 패치노트 추가 수정')
            self.assertIn('bold 변경 전:', request['instructions'])
            self.assertIn('bold 변경 후:', request['instructions'])
            self.assertIn('then one blank line', request['instructions'])
            self.assertIn('bold 추가:', request['instructions'])
            self.assertNotIn('⚪', request['instructions'])
            self.assertEqual(list(embed.fields), [])
            expected = '변경: 10% → 20%\n\n[공식 패치노트 확인](' + embed.url + ')'
            self.assertEqual(embed.description, expected)
            self.assertEqual(failed.send.call_args.kwargs['embed'].description, expected)
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

    async def test_watch_loop_continues_after_openai_error(self):
        from discord.ext import tasks
        # 실제 반복 작업에서 첫 GPT 요청 실패 뒤 다음 회차가 실행되는지 확인합니다.
        client = SimpleNamespace(poll_patch_revisions=AsyncMock(
            side_effect=[maple_bot.OpenAIError('synthetic API failure'), None]))
        loop = tasks.loop(seconds=0, count=2)(maple_bot.MapleNewsBot.check_patch_revisions.coro)
        await loop.start(client)
        self.assertFalse(loop.failed())
        self.assertEqual(client.poll_patch_revisions.await_count, 2)
