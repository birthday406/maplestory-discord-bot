import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import date, datetime, timezone
from pathlib import Path

from ranking_audit import WORLDS, acknowledge_report, check_collection, record_audit
from ranking_store import RankingStore
from ranking_worker import RankingBatchWriter
from maple_bot import MapleNewsBot, RANKING_WORLDS


class RankingAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = RankingStore(self.root / "ranking.db")
        self.day = date(2026, 9, 7)
        self.now = datetime(2026, 9, 8, 6, tzinfo=timezone.utc)

    def populate(self, kind="world", day=None, omit=None, duplicate=False, count=1):
        day = day or self.day
        writer = RankingBatchWriter(self.root / "outbox", "test", 10000)
        for world in WORLDS:
            for shard in range(4):
                page = 1 + shard * 10
                chars = [{"characterName": f"c{world}s{0 if duplicate else shard}n{n}",
                          "worldID": world, "jobName": "Hero", "level": 280,
                          "rank": page + n, "exp": 0, "legionLevel": 9000,
                          "legionRank": page + n, "achievementScore": 10000,
                          "achievementRank": page + n} for n in range(count)]
                if omit != (world, shard):
                    if kind != "world":
                        self.store.save_page(chars, day, 1, update_checkpoint=False)
                    writer.write(day, world, page, chars, kind)
                writer.complete(day, world, page + 40, kind, shard)
        for batch in (self.root / "outbox").glob("*.jsonl"):
            self.store.import_batch(batch)

    def test_complete_and_replayed_pages_are_clean_and_acknowledged(self):
        self.populate()
        self.populate()
        reports = check_collection(self.store, self.now)
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0]["issues"]), [])
        acknowledge_report(self.store, self.day.isoformat(), "world")
        self.assertEqual(check_collection(RankingStore(self.store.path), self.now), [])

    def test_missing_final_data_page_is_detected(self):
        self.populate(omit=(45, 2))
        report = check_collection(self.store, self.now)[0]
        self.assertIn("시작 순위 21", report["issues"])

    def test_duplicate_names_on_different_pages_are_detected(self):
        self.populate(duplicate=True)
        self.assertIn("겹치는 캐릭터", check_collection(self.store, self.now)[0]["issues"])

    def test_representative_waits_for_completion_then_warns_on_drop(self):
        self.populate(kind="legion", count=10)
        report = check_collection(self.store, self.now)[0]
        self.assertEqual(json.loads(report["issues"]), [])
        acknowledge_report(self.store, self.day.isoformat(), "legion")
        self.populate(kind="legion", day=date(2026, 9, 8), count=9)
        reports = check_collection(self.store, datetime(2026, 9, 9, 6, tzinfo=timezone.utc))
        self.assertTrue(any("10% 이상 감소" in r["issues"] for r in reports))

    def test_incomplete_waits_until_deadline(self):
        record = {"scan_date": self.day.isoformat(), "world_id": 45,
                  "page_index": 1, "characters": [], "audit_version": 1}
        with self.store._connect() as connection:
            record_audit(connection, [record])
        self.assertEqual(check_collection(self.store, self.now), [])
        reports = check_collection(self.store, datetime(2026, 9, 8, 17, 10, tzinfo=timezone.utc))
        self.assertEqual(len(reports), 3)
        self.assertTrue(all("완료 표식 없음" in r["issues"] for r in reports))

    def test_legacy_batches_do_not_start_incomplete_audits(self):
        with self.store._connect() as connection:
            record_audit(connection, [{"scan_date": "2026-09-01", "characters": []}])
        self.assertEqual(check_collection(self.store, self.now), [])

    def test_empty_representative_page_is_preserved(self):
        writer = RankingBatchWriter(self.root / "empty", "test", 1)
        writer.write(self.day, 45, 1, [], "legion")
        self.store.import_batch(next((self.root / "empty").glob("*.jsonl")))
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT names FROM ranking_audit_pages").fetchone()[0], "[]")

    def test_database_missing_member_is_detected_even_if_total_is_same(self):
        self.populate()
        with self.store._connect() as connection:
            connection.execute("UPDATE ranking_snapshots SET name_key='unrelated' WHERE name_key='c45s0n0'")
        self.assertIn("DB에 없는 캐릭터 1명", check_collection(self.store, self.now)[0]["issues"])

    def test_world_labels_match_bot(self):
        self.assertEqual(WORLDS, {world: RANKING_WORLDS[world] for world in WORLDS})

    def test_achievement_global_pages_use_actual_snapshot_world(self):
        self.populate(kind="achievement")
        with self.store._connect() as connection:
            # 월드별 요청에 같은 전역 업적 목록이 돌아온 상황을 재현합니다.
            names = [r[0] for r in connection.execute('SELECT name_key FROM ranking_snapshots')]
            for world in WORLDS:
                connection.execute('UPDATE ranking_audit_pages SET names=? WHERE kind=? AND world=? AND page=1',
                                   (json.dumps(names), 'achievement', world))
                connection.execute('UPDATE ranking_audit_pages SET names=? WHERE kind=? AND world=? AND page<>1',
                                   ('[]', 'achievement', world))
        report = check_collection(self.store, self.now)[0]
        self.assertEqual(json.loads(report['issues']), [])
        self.assertEqual(json.loads(report['counts']), {str(w): 4 for w in WORLDS})

    def test_achievement_missing_value_and_unknown_name_still_warn(self):
        self.populate(kind="achievement")
        with self.store._connect() as connection:
            connection.execute("UPDATE ranking_snapshots SET achievement_score=0 WHERE name_key='c45s0n0'")
            connection.execute("UPDATE ranking_audit_pages SET names=? WHERE kind='achievement' AND world=45 AND page=1",
                               (json.dumps(['c45s0n0', 'unknown']),))
        report = check_collection(self.store, self.now)[0]
        self.assertIn('Kronos: 배치에 있지만 DB에 없는 캐릭터 2명', json.loads(report['issues']))

    def test_day_with_no_batches_is_reported_after_first_active_day(self):
        self.populate()
        reports = check_collection(self.store, datetime(2026, 9, 9, 18, tzinfo=timezone.utc))
        missing_day = [r for r in reports if r["day"] == "2026-09-08"]
        self.assertEqual(len(missing_day), 3)
        self.assertTrue(all("완료 표식 없음" in r["issues"] for r in missing_day))


class RankingAuditDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_is_not_resent_and_failure_stays_pending(self):
        import discord
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            with store._connect() as connection:
                connection.execute(
                    "INSERT INTO ranking_audit_reports VALUES (?, ?, ?, ?, ?, 0)",
                    ("2026-09-07", "world", "{}", '["누락 페이지"]', "2026-09-08T06:00:00+00:00"),
                )
            send = AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason="error"), "failed"))
            bot = SimpleNamespace(ranking_store=store,
                                  application_info=AsyncMock(return_value=SimpleNamespace(owner=SimpleNamespace(send=send))))
            with self.assertLogs(level="ERROR"):
                await MapleNewsBot.check_ranking_integrity.coro(bot)
            self.assertEqual(len(check_collection(store)), 1)
            send.side_effect = None
            await MapleNewsBot.check_ranking_integrity.coro(bot)
            await MapleNewsBot.check_ranking_integrity.coro(bot)
            self.assertEqual(send.await_count, 2)
            self.assertEqual(check_collection(store), [])
