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
        return create_app(ranking_path=self.path)

    async def get(self, query=''):
        return await self.client.get('/api/rankings'+query,headers={'Host':'127.0.0.1:8766'})

    async def test_public_world_list_and_character_history(self):
        data=await (await self.get('?world=45')).json()
        self.assertEqual([r['name'] for r in data['rows']],['First','Sample'])
        bera=await (await self.get('?world=1')).json()
        self.assertEqual([r['name'] for r in bera['rows']],['Other'])
        scania=await (await self.get('?world=19')).json()
        self.assertEqual(scania['rows'],[])
        data=await (await self.get('?nickname=sAmPlE')).json()
        self.assertEqual(data['character']['name'],'Sample')
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
    def test_store_initializes_index_used_by_world_query(self):
        with tempfile.TemporaryDirectory() as folder:
            store=RankingStore(Path(folder)/'ranking.db')
            with closing(sqlite3.connect(store.path)) as db:
                plan=db.execute('EXPLAIN QUERY PLAN SELECT name FROM characters WHERE world_id=? AND level>=260 ORDER BY level DESC,exp DESC,name_key LIMIT 100',(45,)).fetchall()
            self.assertIn('idx_characters_web_ranking',str(plan))
            self.assertNotIn('TEMP B-TREE',str(plan))
