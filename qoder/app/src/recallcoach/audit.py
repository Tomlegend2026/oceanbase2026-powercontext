"""审计日志：按天滚动的 jsonl 读写（memory-design.md 第 7 节）。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

WINDOW_DAYS = 7


def day_path(base: Path, day: date) -> Path:
    """配置基名 + 某一天 → 当天实际写入的滚动文件名。"""
    return base.with_name(f"{base.stem}-{day.strftime('%Y%m%d')}{base.suffix}")


def day_of(record: dict[str, Any]) -> date:
    """按记录自己的 ts 归天，写入与读取才不会错位。"""
    try:
        return date.fromisoformat(str(record.get("ts") or "")[:10])
    except ValueError:
        return datetime.now().astimezone().date()


def append(base: Path, record: dict[str, Any]) -> Path:
    path = day_path(base, day_of(record))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def window(base: Path, *, anchor: date | None = None, days: int = WINDOW_DAYS) -> list[Path]:
    anchor = anchor or datetime.now().astimezone().date()
    return [day_path(base, anchor - timedelta(days=offset)) for offset in range(days - 1, -1, -1)]


def read_window(
    base: Path,
    *,
    limit: int = 50,
    anchor: date | None = None,
    days: int = WINDOW_DAYS,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """读最近 days 天的审计行：时间正序，只留最后 limit 条，同时回传真正命中的文件。"""
    records: list[dict[str, Any]] = []
    used: list[Path] = []
    for path in window(base, anchor=anchor, days=days):
        if not path.exists():
            continue
        used.append(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(raw))
            except ValueError:
                continue
    return records[-limit:], used
