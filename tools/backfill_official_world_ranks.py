"""보존된 월드 수집 JSONL에서 공식 순위만 복구합니다. 기존 캐릭터 기록은 바꾸지 않습니다."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def backfill(database, folder):
    count = 0
    with closing(sqlite3.connect(database)) as db:
        # 운영 적용 전에 RankingStore 초기화로 새 표를 만든 뒤 실행합니다.
        db.execute('SELECT 1 FROM official_world_rankings LIMIT 1')
        for path in sorted(Path(folder).rglob('*.jsonl')):
            with path.open(encoding='utf-8') as source, db:
                for line in source:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if row.get('ranking_type', 'world') != 'world':
                        continue
                    for c in row.get('characters', []):
                        # 늦게 발견한 예전 파일이 최신 순위를 덮어쓰지 않습니다.
                        db.execute('''INSERT INTO official_world_rankings VALUES (?,?,?,?)
                            ON CONFLICT(name_key) DO UPDATE SET world_id=excluded.world_id,
                            ranking=excluded.ranking,updated_date=excluded.updated_date
                            WHERE excluded.updated_date>=official_world_rankings.updated_date''',
                            (c['characterName'].casefold(), c['worldID'], c['rank'], row['scan_date']))
                        count += 1
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    if not args.database.is_file() or not args.folder.is_dir():
        parser.error('기존 DB 파일과 수집 결과 폴더를 지정해주세요.')
    print('공식 월드 순위 반영:', backfill(args.database, args.folder))
