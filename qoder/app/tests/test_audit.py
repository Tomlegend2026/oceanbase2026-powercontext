"""审计日志按天滚动 + 最近 7 天窗口读取（memory-design.md 第 7 节）。"""

import json
from datetime import date, datetime, timedelta

from recallcoach import audit


def day_file(day: date) -> str:
    return f"memory_audit-{day:%Y%m%d}.jsonl"


def stamp(day: date, second: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time()) + timedelta(seconds=second)


def record(day: date, op: str, second: int = 0) -> dict:
    return {"ts": stamp(day, second).isoformat(timespec="seconds"), "op": op, "status": "ok"}


def test_records_land_in_their_own_day_file(workdir):
    base = workdir / "memory_audit.jsonl"

    audit.append(base, record(date(2026, 10, 5), "memory.search"))
    audit.append(base, record(date(2026, 10, 6), "memory.remember"))

    day_one = workdir / day_file(date(2026, 10, 5))
    day_two = workdir / day_file(date(2026, 10, 6))
    assert day_one.read_text(encoding="utf-8").count("\n") == 1
    assert day_two.read_text(encoding="utf-8").count("\n") == 1
    assert json.loads(day_two.read_text(encoding="utf-8"))["op"] == "memory.remember"
    # 基名只用于推导滚动文件，本身不再写入
    assert not base.exists()


def test_record_without_a_parseable_timestamp_falls_back_to_today(workdir):
    base = workdir / "memory_audit.jsonl"
    written = audit.append(base, {"ts": "", "op": "memory.pending"})
    assert written.name == day_file(datetime.now().date())


def test_window_reads_seven_days_oldest_first_and_drops_the_eighth(workdir):
    base = workdir / "memory_audit.jsonl"
    anchor = date(2026, 10, 31)
    for offset in range(8):
        day = anchor - timedelta(days=offset)
        audit.append(base, record(day, "memory.search", second=offset))

    lines, files = audit.read_window(base, limit=50, anchor=anchor)

    assert [path.name for path in files] == [day_file(anchor - timedelta(days=o)) for o in range(6, -1, -1)]
    assert len(lines) == 7, "第 8 天前的文件不在窗口里"
    assert [line["ts"][:10] for line in lines] == [
        (anchor - timedelta(days=o)).isoformat() for o in range(6, -1, -1)
    ]


def test_limit_keeps_the_most_recent_lines(workdir):
    base = workdir / "memory_audit.jsonl"
    anchor = date(2026, 10, 31)
    for second in range(5):
        audit.append(base, record(anchor, f"op.{second}", second=second))

    lines, files = audit.read_window(base, limit=2, anchor=anchor)

    assert len(files) == 1
    assert [line["op"] for line in lines] == ["op.3", "op.4"]


def test_missing_days_and_broken_lines_are_ignored(workdir):
    base = workdir / "memory_audit.jsonl"
    anchor = date(2026, 10, 31)
    audit.append(base, record(anchor, "memory.search"))
    (workdir / "memory_audit-20261030.jsonl").write_text("这不是 JSON\n", encoding="utf-8")

    lines, files = audit.read_window(base, limit=10, anchor=anchor)

    assert [line["op"] for line in lines] == ["memory.search"]
    assert [path.name for path in files] == [day_file(date(2026, 10, 30)), day_file(date(2026, 10, 31))]
