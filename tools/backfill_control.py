"""보조 서버의 MapleBot 백필 상태를 확인하거나 체크포인트에서 재시작합니다."""

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_maplebot_backfill_aux.sh"
LOG_PATH = ROOT / "maplebot-backfill-280-plus.log"
ALERT_PATH = ROOT / "maplebot-backfill-alert.txt"
CURRENT_LEVEL_PATH = ROOT / "maplebot-backfill-current-level"
COUNT_CACHE_PATH = ROOT / ".maplebot-backfill-counts.json"


def backfill_processes() -> list[tuple[int, str]]:
    result = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = path.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if "tools/backfill_maplebot.py" in command or RUNNER.name in command:
            result.append((int(path.parent.name), command))
    return result


def checkpoint_counts(root: Path = ROOT) -> dict[int, int]:
    cache_path = root / COUNT_CACHE_PATH.name
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cache = {}
    counts = {}
    changed = False
    for path in root.glob("maplebot-backfill-level-*.jsonl"):
        match = re.search(r"level-(\d+)\.jsonl$", path.name)
        if not match:
            continue
        stat = path.stat()
        cached = cache.get(path.name, {})
        cached_names = cached.get("names")
        if (
            isinstance(cached_names, list)
            and cached.get("size") == stat.st_size
            and cached.get("mtime_ns") == stat.st_mtime_ns
        ):
            names = set(cached_names)
        else:
            can_continue = (
                isinstance(cached_names, list)
                and isinstance(cached.get("size"), int)
                and 0 <= cached["size"] <= stat.st_size
            )
            names = set(cached_names) if can_continue else set()
            with path.open("rb") as handle:
                if can_continue:
                    handle.seek(cached["size"])
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("gains"):
                        names.add(str(item.get("name", "")).casefold())
            cache[path.name] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "names": sorted(names),
            }
            changed = True
        counts[int(match.group(1))] = len(names)
    if changed:
        temporary = cache_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
        temporary.replace(cache_path)
    return counts


def latest_progress(log_path: Path = LOG_PATH, level: int | None = None) -> str | None:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None
    start = 0
    if level is not None:
        marker = f"level {level} start"
        start = max((index for index, line in enumerate(lines) if marker in line), default=0)
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.search(r"level \d+ start", lines[index])
        ),
        len(lines),
    )
    for line in reversed(lines[start:end]):
        match = re.match(r"\[(\d+)/(\d+)\]", line)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return None


def status_text(root: Path = ROOT, processes: list[tuple[int, str]] | None = None) -> str:
    processes = backfill_processes() if processes is None else processes
    worker = next((command for _, command in processes if "backfill_maplebot.py" in command), "")
    level_match = re.search(r"--level\s+(\d+)", worker)
    counts = checkpoint_counts(root)
    try:
        saved_level = int((root / CURRENT_LEVEL_PATH.name).read_text().strip())
    except (FileNotFoundError, ValueError):
        saved_level = 0
    level = saved_level or (int(level_match.group(1)) if level_match else max(counts, default=0))
    progress = latest_progress(root / LOG_PATH.name, level or None)
    lines = [f"백필 상태: {'실행 중' if processes else '중지됨'}"]
    if level:
        lines.append(f"현재 레벨: Lv.{level}")
        lines.append(f"현재 레벨 저장: {counts.get(level, 0):,}명")
    lines.append(f"전체 체크포인트: {sum(counts.values()):,}명")
    if progress:
        lines.append(f"최근 실행 진행: {progress}")
    return "\n".join(lines)


def restart() -> str:
    processes = backfill_processes()
    for pid, _ in processes:
        os.kill(pid, signal.SIGTERM)
    time.sleep(1)
    for pid, _ in backfill_processes():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    ALERT_PATH.write_text("", encoding="utf-8")
    with LOG_PATH.open("ab") as output:
        subprocess.Popen(
            ["bash", str(RUNNER)],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(1)
    return "백필을 체크포인트에서 재시작했습니다.\n" + status_text()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "restart"))
    args = parser.parse_args()
    print(restart() if args.action == "restart" else status_text())


if __name__ == "__main__":
    main()
