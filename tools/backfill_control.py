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
    counts = {}
    for path in root.glob("maplebot-backfill-level-*.jsonl"):
        match = re.search(r"level-(\d+)\.jsonl$", path.name)
        if not match:
            continue
        names = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("gains"):
                names.add(str(item.get("name", "")).casefold())
        counts[int(match.group(1))] = len(names)
    return counts


def latest_progress(log_path: Path = LOG_PATH) -> str | None:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        match = re.match(r"\[(\d+)/(\d+)\]", line)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return None


def status_text(root: Path = ROOT, processes: list[tuple[int, str]] | None = None) -> str:
    processes = backfill_processes() if processes is None else processes
    worker = next((command for _, command in processes if "backfill_maplebot.py" in command), "")
    level_match = re.search(r"--level\s+(\d+)", worker)
    counts = checkpoint_counts(root)
    level = int(level_match.group(1)) if level_match else max(counts, default=0)
    progress = latest_progress(root / LOG_PATH.name)
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
