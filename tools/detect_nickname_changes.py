"""두 연속 날짜의 완전한 랭킹 스냅샷에서 닉네임 변경 후보를 찾습니다."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ranking_store import RankingStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("old_date", type=date.fromisoformat)
    parser.add_argument("new_date", type=date.fromisoformat)
    parser.add_argument("--db", type=Path, default=ROOT / "ranking.db")
    args = parser.parse_args()
    result = RankingStore(args.db).detect_nickname_changes(
        args.old_date, args.new_date
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
