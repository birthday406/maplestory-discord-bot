import sqlite3
from contextlib import closing
import tempfile
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase
from website.server import create_app
from ranking_store import RankingStore
import unittest


class PublicRankingsTests(AioHTTPTestCase):
    async def get_application(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'rank.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE characters (name_key TEXT PRIMARY KEY,name TEXT,world_id INT,job_name TEXT,level INT,exp INT,updated_date TEXT,legion_level INT,achievement_score INT);
            CREATE TABLE ranking_snapshots (name_key TEXT,snapshot_date TEXT,level INT,exp INT);
            INSERT INTO characters VALUES ('sample','Sample',45,'Hero',280,100,'2026-09-15',9000,1000),('first','First',45,'Bishop',290,0,'2026-09-14',0,0),('other','Other',1,'Hero',300,0,'2026-09-15',0,0);
            INSERT INTO ranking_snapshots VALUES ('sample','2026-09-13',280,10),('sample','2026-09-15',280,100);''')
            db.executescript("CREATE TABLE official_world_rankings(name_key TEXT PRIMARY KEY,world_id INT,ranking INT,updated_date TEXT); INSERT INTO official_world_rankings VALUES ('sample',45,3,'2026-09-15'),('first',45,9,'2026-09-15'),('other',1,1,'2026-09-15');")
            for column in ('image_url TEXT', 'ranking INTEGER', 'legion_rank INTEGER', 'achievement_rank INTEGER'):
                db.execute('ALTER TABLE characters ADD COLUMN ' + column)
            db.execute("UPDATE characters SET image_url='https://example.com/avatar.png',ranking=12,legion_rank=34,achievement_rank=56 WHERE name_key='sample'")
        return create_app(ranking_path=self.path)

    async def get(self, query=''):
        return await self.client.get('/api/rankings'+query,headers={'Host':'127.0.0.1:8766'})

    async def test_public_world_list_and_character_history(self):
        data=await (await self.get('?world=45')).json()
        self.assertEqual([r['name'] for r in data['rows']],['Sample','First'])
        self.assertEqual([r['ranking'] for r in data['rows']],[3,9])
        bera=await (await self.get('?world=1')).json()
        self.assertEqual([r['name'] for r in bera['rows']],['Other'])
        scania=await (await self.get('?world=19')).json()
        self.assertEqual(scania['rows'],[])
        data=await (await self.get('?nickname=sAmPlE')).json()
        self.assertEqual(data['character']['name'],'Sample')
        self.assertEqual(data['character']['image_url'],'https://example.com/avatar.png')
        self.assertEqual(data['character']['ranking'],12)
        self.assertEqual(data['character']['legion_rank'],34)
        self.assertEqual(data['character']['achievement_rank'],56)
        self.assertGreater(data['character']['requiredExp'],100)
        self.assertEqual(data['gains'],[{'date':'2026-09-15','days':2,'exp':90}])
        self.assertNotIn('discord',str(data).lower())
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM characters').fetchone()[0],3)

    async def test_unknown_invalid_and_injection_names(self):
        self.assertEqual((await self.get('?nickname=missing')).status,404)
        self.assertEqual((await self.get('?world=999')).status,400)
        self.assertEqual((await self.get('?nickname=abcdefghijklmnop')).status,400)
        self.assertEqual((await self.get('?nickname=%27OR%201%3D1')).status,404)

    async def test_public_lookup_rate_limit(self):
        for _ in range(30):
            self.assertEqual((await self.get()).status,200)
        self.assertEqual((await self.get()).status,429)


class RankingIndexTests(unittest.TestCase):
    def test_backfill_preserves_latest_world_rank_and_character_data(self):
        import json
        from tools.backfill_official_world_ranks import backfill
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            store=RankingStore(path/'ranking.db')
            character=dict(characterName='Zebra',worldID=45,rank=1)
            records=[
                dict(scan_date='2026-09-15',ranking_type='world',characters=[character]),
                dict(scan_date='2026-09-14',ranking_type='world',characters=[dict(character,rank=5)]),
                dict(scan_date='2026-09-16',ranking_type='legion',characters=[dict(character,rank=99)]),
            ]
            (path/'batch.jsonl').write_text('\n'.join(json.dumps(r) for r in records),encoding='utf-8')
            backfill(store.path,path)
            backfill(store.path,path)
            with closing(sqlite3.connect(store.path)) as db:
                self.assertEqual(db.execute('SELECT ranking,updated_date FROM official_world_rankings').fetchall(),[(1,'2026-09-15')])
                self.assertEqual(db.execute('SELECT count(*) FROM characters').fetchone()[0],0)

    def test_overall_lookup_does_not_replace_world_rank(self):
        from datetime import date
        with tempfile.TemporaryDirectory() as folder:
            store=RankingStore(Path(folder)/'ranking.db')
            character=dict(characterName='Zebra',worldID=45,jobName='Hero',level=300,exp=0,rank=1)
            store.save_page([character],date(2026,9,15),11,source_page_index=1)
            store.save_snapshot(dict(character,rank=99),date(2026,9,16))
            store.save_page([dict(character,rank=5)],date(2026,9,14),11,source_page_index=1)
            with closing(sqlite3.connect(store.path)) as db:
                self.assertEqual(db.execute('SELECT ranking,updated_date FROM official_world_rankings').fetchone(),(1,'2026-09-15'))

    def test_store_initializes_index_used_by_world_query(self):
        with tempfile.TemporaryDirectory() as folder:
            store=RankingStore(Path(folder)/'ranking.db')
            with closing(sqlite3.connect(store.path)) as db:
                plan=db.execute('EXPLAIN QUERY PLAN SELECT name FROM characters WHERE world_id=? AND level>=260 ORDER BY level DESC,exp DESC,name_key LIMIT 100',(45,)).fetchall()
            self.assertIn('idx_characters_web_ranking',str(plan))
            self.assertNotIn('TEMP B-TREE',str(plan))
