"""SQLite 存取：schema、种子导入、调度与作答查询。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS courses (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
  id TEXT PRIMARY KEY,
  course_id TEXT NOT NULL REFERENCES courses(id),
  title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kp (
  id TEXT PRIMARY KEY,
  course_id TEXT NOT NULL REFERENCES courses(id),
  chapter TEXT NOT NULL,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY,
  course_id TEXT NOT NULL REFERENCES courses(id),
  kp_id TEXT NOT NULL REFERENCES kp(id),
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  explanation TEXT NOT NULL,
  cause_options TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule (
  card_id TEXT PRIMARY KEY REFERENCES cards(id),
  due_at TEXT NOT NULL,
  interval_days INTEGER NOT NULL DEFAULT 1,
  ease REAL NOT NULL DEFAULT 2.5,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL REFERENCES cards(id),
  session_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  correct INTEGER NOT NULL,
  confidence INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  cause TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  course_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  mode TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_refs (
  entry_id TEXT PRIMARY KEY,
  citation_json TEXT NOT NULL,
  kp_id TEXT,
  kind TEXT,
  written_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_cards_kp ON cards(kp_id);
CREATE INDEX IF NOT EXISTS ix_answers_card ON answers(card_id, ts);
CREATE INDEX IF NOT EXISTS ix_schedule_due ON schedule(due_at);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def init_db(conn: sqlite3.Connection, seed_path: Path, today: date) -> dict[str, int]:
    """幂等导入题库；不改动已有进度。"""
    payload = json.loads(seed_path.read_text(encoding="utf-8-sig"))
    now = datetime.now().isoformat(timespec="seconds")
    course = payload["course"]

    with conn:
        conn.execute(
            "INSERT INTO courses(id, title, created_at) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET title=excluded.title",
            (course["id"], course["title"], now),
        )
        for chapter in payload.get("chapters", []):
            conn.execute(
                "INSERT INTO chapters(id, course_id, title) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET title=excluded.title",
                (chapter["id"], course["id"], chapter["title"]),
            )
        for card in payload["cards"]:
            conn.execute(
                "INSERT INTO kp(id, course_id, chapter, name) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name, chapter=excluded.chapter",
                (card["kp_id"], course["id"], card["chapter"], card["kp_name"]),
            )
            conn.execute(
                "INSERT INTO cards(id, course_id, kp_id, front, back, explanation, cause_options, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET front=excluded.front, back=excluded.back,"
                " explanation=excluded.explanation, cause_options=excluded.cause_options",
                (
                    card["id"],
                    course["id"],
                    card["kp_id"],
                    card["front"],
                    card["back"],
                    card["explanation"],
                    json.dumps(card.get("cause_options", []), ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO schedule(card_id, due_at, interval_days, ease, reps, lapses)"
                " VALUES (?, ?, 1, 2.5, 0, 0) ON CONFLICT(card_id) DO NOTHING",
                (card["id"], today.isoformat()),
            )

    return {
        "cards": conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0],
        "kps": conn.execute("SELECT COUNT(*) FROM kp").fetchone()[0],
        "chapters": conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0],
    }


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def due_cards(conn: sqlite3.Connection, today: date, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id, c.front, c.back, c.explanation, c.cause_options, c.kp_id,
               k.chapter AS chapter, k.name AS kp_name, ch.title AS chapter_title,
               s.due_at, s.interval_days, s.ease, s.reps, s.lapses
        FROM cards c
        JOIN kp k ON k.id = c.kp_id
        JOIN chapters ch ON ch.id = k.chapter
        JOIN schedule s ON s.card_id = c.id
        WHERE s.due_at <= ?
        ORDER BY s.due_at ASC, c.id ASC
        LIMIT ?
        """,
        (today.isoformat(), limit),
    ).fetchall()
    return [dict(row) for row in rows]


def card_by_id(conn: sqlite3.Connection, card_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT c.id, c.front, c.back, c.explanation, c.cause_options, c.kp_id,
               k.name AS kp_name, k.chapter AS chapter, ch.title AS chapter_title,
               s.due_at, s.interval_days, s.ease, s.reps, s.lapses
        FROM cards c
        JOIN kp k ON k.id = c.kp_id
        JOIN chapters ch ON ch.id = k.chapter
        JOIN schedule s ON s.card_id = c.id
        WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    return dict(row) if row else None


def all_cards(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id, c.front, c.back, c.explanation, c.cause_options, c.kp_id,
               k.name AS kp_name, k.chapter AS chapter, ch.title AS chapter_title,
               s.due_at, s.interval_days, s.ease, s.reps, s.lapses
        FROM cards c
        JOIN kp k ON k.id = c.kp_id
        JOIN chapters ch ON ch.id = k.chapter
        JOIN schedule s ON s.card_id = c.id
        ORDER BY c.id ASC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def save_schedule(conn: sqlite3.Connection, card_id: str, state: dict[str, Any]) -> None:
    with conn:
        conn.execute(
            "UPDATE schedule SET due_at=?, interval_days=?, ease=?, reps=?, lapses=? WHERE card_id=?",
            (
                state["due_at"],
                state["interval_days"],
                state["ease"],
                state["reps"],
                state["lapses"],
                card_id,
            ),
        )


def add_answer(
    conn: sqlite3.Connection,
    *,
    card_id: str,
    session_id: str,
    correct: bool,
    confidence: int,
    latency_ms: int,
    cause: str | None,
    now: datetime,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO answers(card_id, session_id, ts, correct, confidence, latency_ms, cause)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (card_id, session_id, now.isoformat(timespec="seconds"), int(correct), confidence, latency_ms, cause),
        )


def lapses_in_window(conn: sqlite3.Connection, kp_id: str, since: datetime, until: datetime) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM answers a
        JOIN cards c ON c.id = a.card_id
        WHERE c.kp_id = ? AND a.correct = 0 AND a.ts >= ? AND a.ts <= ?
        """,
        (kp_id, since.isoformat(timespec="seconds"), until.isoformat(timespec="seconds")),
    ).fetchone()
    return int(row["n"])


def wrong_answers_today(conn: sqlite3.Connection, kp_id: str, day: date) -> int:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM answers a
        JOIN cards c ON c.id = a.card_id
        WHERE c.kp_id = ? AND a.ts >= ? AND a.ts < ?
        """,
        (kp_id, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
    ).fetchone()
    return int(row["n"])


def answers_today_by_kp(conn: sqlite3.Connection, day: date) -> dict[str, int]:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    rows = conn.execute(
        """
        SELECT c.kp_id AS kp_id, COUNT(*) AS n FROM answers a
        JOIN cards c ON c.id = a.card_id
        WHERE a.ts >= ? AND a.ts < ?
        GROUP BY c.kp_id
        """,
        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
    ).fetchall()
    return {row["kp_id"]: int(row["n"]) for row in rows}


def open_session(conn: sqlite3.Connection, course_id: str, mode: str, now: datetime) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL AND course_id = ? ORDER BY started_at DESC LIMIT 1",
        (course_id,),
    ).fetchone()
    if row and row["mode"] == mode:
        return dict(row)
    session_id = f"s-{now.strftime('%Y%m%d%H%M%S')}-{mode}"
    with conn:
        conn.execute(
            "INSERT INTO sessions(id, course_id, started_at, ended_at, mode) VALUES (?, ?, ?, NULL, ?)",
            (session_id, course_id, now.isoformat(timespec="seconds"), mode),
        )
    return {
        "id": session_id,
        "course_id": course_id,
        "started_at": now.isoformat(timespec="seconds"),
        "ended_at": None,
        "mode": mode,
    }


def close_session(conn: sqlite3.Connection, session_id: str, now: datetime) -> None:
    with conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (now.isoformat(timespec="seconds"), session_id),
        )


def session_answers(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT a.*, c.kp_id, k.name AS kp_name FROM answers a"
        " JOIN cards c ON c.id = a.card_id JOIN kp k ON k.id = c.kp_id"
        " WHERE a.session_id = ? ORDER BY a.id ASC",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def save_memory_ref(conn: sqlite3.Connection, entry_id: str, citation: dict[str, Any], kp_id: str | None, kind: str, now: datetime) -> None:
    with conn:
        conn.execute(
            "INSERT INTO memory_refs(entry_id, citation_json, kp_id, kind, written_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(entry_id) DO UPDATE SET citation_json=excluded.citation_json, kp_id=excluded.kp_id,"
            " kind=excluded.kind",
            (entry_id, json.dumps(citation, ensure_ascii=False), kp_id, kind, now.isoformat(timespec="seconds")),
        )


def memory_ref(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM memory_refs WHERE entry_id = ?", (entry_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["citation"] = json.loads(item["citation_json"])
    return item


def memory_refs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM memory_refs ORDER BY written_at ASC").fetchall()
    return [dict(row) for row in rows]


def drop_memory_ref(conn: sqlite3.Connection, entry_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM memory_refs WHERE entry_id = ?", (entry_id,))


def count_cards(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0])


def course_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM courses LIMIT 1").fetchone()
    return row["id"] if row else "db"


def recent_answers(conn: sqlite3.Connection, limit: int = 20) -> Iterable[dict[str, Any]]:
    rows = conn.execute(
        "SELECT a.*, c.front, c.kp_id FROM answers a JOIN cards c ON c.id = a.card_id ORDER BY a.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]
