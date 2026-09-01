"""MapleBot의 공개 그래프를 레벨 순서대로 읽어 랭킹 DB의 빈 날짜만 채운다.

로컬 실행에는 Playwright와 설치된 Edge가 필요하다.
    python -m pip install playwright
    python tools/backfill_maplebot.py --level 299 --ssh-key PATH

운영 DB의 기존 스냅샷은 절대 덮어쓰지 않는다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import subprocess
import sys
import time
from bisect import bisect_right
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "/home/ubuntu/maplestory-discord-bot/ranking.db"
DEFAULT_REMOTE_SCRIPT = "/home/ubuntu/maplestory-discord-bot/tools/backfill_maplebot.py"
EU_WORLD_IDS = {30, 46}
TOLERANCE = 1_000_000


FETCH_DAILY_GAINS = r"""
async ({name, region}) => {
  const response = await fetch(
    `/api/character/${encodeURIComponent(name)}?region=${encodeURIComponent(region)}`,
    {
      headers: {'Content-Type': 'application/json'},
      signal: AbortSignal.timeout(15_000)
    }
  );
  const body = await response.json();
  if (!response.ok) throw new Error(`API status ${response.status}`);

  let payload = body;
  if (body.encrypted) {
    const packed = JSON.parse(atob(body.encrypted));
    const fromHex = text => new Uint8Array(
      (text.match(/../g) || []).map(value => parseInt(value, 16))
    );
    const key = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode('0081a87cab06abc65de027850c191f16'),
      {name: 'AES-CBC'},
      false,
      ['decrypt']
    );
    const plain = await crypto.subtle.decrypt(
      {name: 'AES-CBC', iv: fromHex(packed.iv)},
      key,
      fromHex(packed.encrypted)
    );
    payload = JSON.parse(new TextDecoder().decode(plain));
  }

  if (!payload.success || !payload.data) {
    return {
      gains: [],
      not_found: /not found/i.test(String(payload.error || '')),
      profile_loaded: false
    };
  }
  const history = payload.data.expHistory || [];
  if (!history.length) {
    return {gains: [], not_found: false, profile_loaded: true};
  }

  const byDate = new Map(history.map(item => [item.date, Number(item.exp) || 0]));
  const last = new Date(`${history.at(-1).date}T00:00:00Z`);
  const gains = [];
  for (let offset = 29; offset >= 0; offset--) {
    const current = new Date(last);
    current.setUTCDate(current.getUTCDate() - offset);
    const day = current.toISOString().slice(0, 10);
    gains.push({date: day, exp: Math.max(0, Math.round(byDate.get(day) || 0))});
  }
  return {gains, not_found: false, profile_loaded: true};
}
"""


def validate_series(gains: list[dict]) -> None:
    if len(gains) != 30:
        raise ValueError(f"expected 30 gains, got {len(gains)}")
    previous = None
    for item in gains:
        current = date.fromisoformat(item["date"])
        if previous is not None and current != previous + timedelta(days=1):
            raise ValueError("gain dates are not consecutive")
        if not isinstance(item["exp"], int) or item["exp"] < 0:
            raise ValueError("invalid EXP gain")
        previous = current


def reconstruction_plan(
    gains: list[dict], existing: dict[str, int]
) -> tuple[dict[str, int], str]:
    """기존 기준점과 일간 증가량으로 안전하게 추가할 누적 경험치를 만든다."""
    validate_series(gains)
    first = date.fromisoformat(gains[0]["date"])
    cumulative = {(first - timedelta(days=1)).isoformat(): 0}
    running = 0
    for item in gains:
        running += item["exp"]
        cumulative[item["date"]] = running

    anchors = [day for day in existing if day in cumulative]
    if not anchors:
        raise ValueError("no existing snapshot anchor")
    offsets = {day: existing[day] - cumulative[day] for day in anchors}
    consistent = max(offsets.values()) - min(offsets.values()) <= TOLERANCE
    if consistent:
        offset = offsets[max(anchors)]
        allowed = cumulative
        mode = "full"
    else:
        first_anchor = min(anchors)
        offset = offsets[first_anchor]
        allowed = {day: value for day, value in cumulative.items() if day <= first_anchor}
        mode = "before_first_anchor"
    return {
        day: offset + value
        for day, value in allowed.items()
        if day not in existing and offset + value >= 0
    }, mode


def exp_prefix() -> list[int]:
    sys.path.insert(0, str(ROOT))
    from maple_data import LEVEL_EXP

    result = [0]
    for value in LEVEL_EXP:
        result.append(result[-1] + int(value))
    return result


def total_exp(prefix: list[int], level: int, exp: int) -> int:
    if not 200 <= level <= 300:
        raise ValueError(f"unsupported level: {level}")
    return prefix[level - 200] + int(exp)


def split_total(prefix: list[int], value: int) -> tuple[int, int]:
    index = min(max(bisect_right(prefix, value) - 1, 0), 100)
    return 200 + index, value - prefix[index]


def list_characters(db_path: str, level: int) -> list[dict]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """SELECT name, world_id
                 FROM characters
                WHERE level = ?
                ORDER BY ranking ASC, name_key""",
            (level,),
        ).fetchall()
    return [
        {"name": name, "region": "EU" if world_id in EU_WORLD_IDS else "NA"}
        for name, world_id in rows
    ]


def apply_payload(
    db_path: str, payload: list[dict], check_integrity: bool = False
) -> dict:
    prefix = exp_prefix()
    inserted = 0
    full = 0
    partial = []
    skipped = []
    connection = sqlite3.connect(db_path, timeout=60)
    connection.execute("PRAGMA busy_timeout = 60000")
    connection.execute("PRAGMA synchronous = NORMAL")
    try:
        connection.execute("BEGIN IMMEDIATE")
        processed = 0
        for item in payload:
            name = item["name"]
            gains = item["gains"]
            validate_series(gains)
            first = date.fromisoformat(gains[0]["date"]) - timedelta(days=1)
            last = date.fromisoformat(gains[-1]["date"])
            character = connection.execute(
                "SELECT name_key, ranking FROM characters WHERE name_key = ?",
                (name.casefold(),),
            ).fetchone()
            if character is None:
                skipped.append([name, "character missing"])
                continue
            rows = connection.execute(
                """SELECT snapshot_date, level, exp
                     FROM ranking_snapshots
                    WHERE name_key = ? AND snapshot_date BETWEEN ? AND ?
                    ORDER BY snapshot_date""",
                (character[0], first.isoformat(), last.isoformat()),
            ).fetchall()
            existing = {
                day: total_exp(prefix, level, exp) for day, level, exp in rows
            }
            try:
                plan, mode = reconstruction_plan(gains, existing)
            except ValueError as error:
                skipped.append([name, str(error)])
                continue
            added = 0
            for day, value in sorted(plan.items()):
                level, exp = split_total(prefix, value)
                cursor = connection.execute(
                    """INSERT INTO ranking_snapshots
                           (name_key, snapshot_date, level, exp, ranking)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(name_key, snapshot_date) DO NOTHING""",
                    (character[0], day, level, exp, character[1]),
                )
                inserted += cursor.rowcount
                added += cursor.rowcount
            if mode == "full":
                full += 1
            else:
                partial.append([name, added])
            processed += 1
            if processed % 50 == 0:
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
        connection.commit()
        integrity = (
            connection.execute("PRAGMA integrity_check").fetchone()[0]
            if check_integrity
            else "not_run"
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "characters": len(payload),
        "inserted": inserted,
        "full": full,
        "partial": partial,
        "skipped": skipped,
        "integrity": integrity,
    }


def ssh_base(host: str, key: str) -> list[str]:
    null = "NUL" if os.name == "nt" else "/dev/null"
    return [
        "ssh",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"UserKnownHostsFile={null}",
        host,
    ]


def remote_json(args: argparse.Namespace, extra: Iterable[str], payload: bytes | None = None):
    command = ssh_base(args.ssh_host, args.ssh_key) + [
        "python3",
        args.remote_script,
        *extra,
    ]
    result = subprocess.run(command, input=payload, capture_output=True, check=True)
    output = result.stdout.decode("utf-8").strip()
    return json.loads(output)


def load_checkpoint(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    saved = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if "gains" in item:
                validate_series(item["gains"])
                saved[item["name"].casefold()] = item
            elif item.get("skipped") in {"not_found", "no_history"}:
                saved[item["name"].casefold()] = item
    return saved


def append_checkpoint(path: Path, item: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def find_edge() -> str:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("Microsoft Edge was not found")


def collect(args: argparse.Namespace, characters: list[dict]) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit("Install the tool dependency: python -m pip install playwright") from error

    checkpoint = Path(args.checkpoint or f"maplebot-backfill-level-{args.level}.jsonl")
    saved = load_checkpoint(checkpoint)
    targets = [item for item in characters if item["name"].casefold() not in saved]
    if args.limit is not None:
        targets = targets[: args.limit]
    pending = deque(targets)
    blocked = []
    started = time.monotonic() - args.delay
    errors = 0
    recovery_count = 0
    character_failures: dict[str, int] = {}
    with sync_playwright() as playwright:
        launch_options = {"headless": True}
        if args.edge or os.name == "nt":
            launch_options["executable_path"] = args.edge or find_edge()
        browser = playwright.chromium.launch(**launch_options)

        def new_page():
            result = browser.new_page()
            result.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "font", "media"}
                else route.continue_(),
            )
            result.on(
                "response",
                lambda response: blocked.append((response.status, response.url))
                if response.status in {403, 429}
                and "/api/character/" in response.url
                else None,
            )
            result.goto(
                "https://maplebot.io",
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            return result

        page = new_page()
        completed = 0
        while pending:
            character = pending.popleft()
            wait = args.delay - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)
            name = character["name"]
            name_key = name.casefold()
            started = time.monotonic()
            succeeded = False
            not_found = False
            profile_loaded = False
            for attempt in range(args.retries + 1):
                try:
                    blocked.clear()
                    result = page.evaluate(
                        FETCH_DAILY_GAINS,
                        {"name": name, "region": character["region"]},
                    )
                    not_found = bool(result.get("not_found"))
                    profile_loaded = bool(result.get("profile_loaded"))
                    gains = result.get("gains", [])
                    if not gains:
                        raise ValueError(
                            "character not found" if not_found else "history unavailable"
                        )
                    validate_series(gains)
                    item = {"name": name, "gains": gains}
                    append_checkpoint(checkpoint, item)
                    saved[name_key] = item
                    errors = 0
                    recovery_count = 0
                    character_failures.pop(name_key, None)
                    completed += 1
                    succeeded = True
                    print(f"[{completed}/{len(targets)}] {name}: ok", flush=True)
                    break
                except Exception as error:
                    if blocked:
                        raise RuntimeError(f"blocked by MapleBot: {blocked[-1]}")
                    page_title = "MapleBot API"
                    if not (not_found or profile_loaded):
                        page.close()
                        page = new_page()
                    if attempt < args.retries:
                        print(
                            f"[{completed + 1}/{len(targets)}] {name}: retry "
                            f"{attempt + 1}/{args.retries}",
                            file=sys.stderr,
                            flush=True,
                        )
                        time.sleep(args.delay)
                        continue
                    character_failures[name_key] = character_failures.get(name_key, 0) + 1
                    detail = (
                        f"{error}; title={page_title!r}; not_found={not_found}"
                    )
                    append_checkpoint(checkpoint, {"name": name, "error": detail})
                    print(
                        f"[{completed + 1}/{len(targets)}] {name}: {detail}",
                        file=sys.stderr,
                        flush=True,
                    )
            if succeeded:
                continue

            # 정상 캐릭터 페이지인데 그래프가 없거나 실제 미검색이면 두 번 확인 후
            # 체크포인트에 남깁니다. 재시작 때 같은 실패를 처음부터 반복하지 않습니다.
            if (not_found or profile_loaded) and character_failures[name_key] >= 2:
                skipped = "not_found" if not_found else "no_history"
                item = {"name": name, "skipped": skipped}
                append_checkpoint(checkpoint, item)
                saved[name_key] = item
                completed += 1
                errors = 0
                print(
                    f"[{completed}/{len(targets)}] {name}: skipped after repeated {skipped}",
                    file=sys.stderr,
                    flush=True,
                )
                continue

            pending.append(character)
            if not_found or profile_loaded:
                # 정상 페이지의 데이터 부재는 한 바퀴 뒤 재확인하되 장애로 세지 않습니다.
                errors = 0
                continue
            errors += 1
            if errors < args.max_errors:
                continue

            recovery_count += 1
            if recovery_count > args.max_recoveries:
                raise RuntimeError(
                    f"stopped after {recovery_count - 1} recovery waits"
                )
            recovery_wait = min(
                args.recovery_delay * (3 ** (recovery_count - 1)), 90
            )
            print(
                f"MapleBot temporarily unavailable; rebuilding browser and "
                f"waiting {recovery_wait} seconds",
                file=sys.stderr,
                flush=True,
            )
            browser.close()
            time.sleep(recovery_wait)
            browser = playwright.chromium.launch(**launch_options)
            page = new_page()
            errors = 0
        browser.close()
    return [item for item in saved.values() if "gains" in item]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", type=int)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--list-json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--apply-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ssh-host", default="ubuntu@192.18.141.115")
    parser.add_argument("--ssh-key")
    parser.add_argument("--remote-script", default=DEFAULT_REMOTE_SCRIPT)
    parser.add_argument("--edge")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-errors", type=int, default=3)
    parser.add_argument("--recovery-delay", type=int, default=10)
    parser.add_argument("--max-recoveries", type=int, default=3)
    parser.add_argument("--checkpoint")
    parser.add_argument("--check-integrity", action="store_true")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()

    if args.list_json:
        if args.level is None:
            parser.error("--level is required")
        print(json.dumps(list_characters(args.db, args.level), ensure_ascii=False))
        return
    if args.apply_stdin:
        payload = json.loads(gzip.decompress(sys.stdin.buffer.read()))
        print(
            json.dumps(
                apply_payload(args.db, payload, args.check_integrity),
                ensure_ascii=False,
            )
        )
        return
    if args.level is None:
        parser.error("--level is required")
    if args.local:
        results = collect(args, list_characters(args.db, args.level))
        print(
            json.dumps(
                apply_payload(args.db, results, args.check_integrity),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not args.ssh_key:
        parser.error("--ssh-key is required unless --local is used")

    characters = remote_json(
        args,
        ["--list-json", "--level", str(args.level), "--db", args.db],
    )
    results = collect(args, characters)
    apply_args = ["--apply-stdin", "--db", args.db]
    if args.check_integrity:
        apply_args.append("--check-integrity")
    outcome = remote_json(
        args,
        apply_args,
        gzip.compress(json.dumps(results, ensure_ascii=False).encode("utf-8")),
    )
    print(json.dumps(outcome, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        alert_path = os.getenv("BACKFILL_ALERT_PATH")
        if alert_path:
            Path(alert_path).write_text(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                f"{type(error).__name__}: {error}",
                encoding="utf-8",
            )
        raise
