"""SM-2 调度与到期分。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

EASE_FLOOR = 1.3
EASE_CEILING = 3.0
LAPSE_EASE_PENALTY = 0.2
EASY_BONUS = 0.1


def review(state: dict[str, Any], *, correct: bool, confidence: int, today: date) -> dict[str, Any]:
    """按一次作答推进调度状态，返回新的状态。"""
    ease = float(state["ease"])
    interval = int(state["interval_days"])
    reps = int(state["reps"])
    lapses = int(state["lapses"])

    if correct:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
        if confidence >= 4:
            ease = min(EASE_CEILING, ease + EASY_BONUS)
    else:
        lapses += 1
        reps = 0
        interval = 1
        ease = max(EASE_FLOOR, ease - LAPSE_EASE_PENALTY)

    # 答错的卡当天仍然到期：同一轮里可以立刻再练一次，也便于演示"错误 → 记忆 → 重排"。
    due_at = today if not correct else today + timedelta(days=interval)
    return {
        "due_at": due_at.isoformat(),
        "interval_days": interval,
        "ease": round(ease, 3),
        "reps": reps,
        "lapses": lapses,
    }


def due_score(due_at: str, today: date) -> float:
    """到期分：当天到期 0.2，每逾期一天 +0.12，封顶 1.0。"""
    try:
        due = date.fromisoformat(str(due_at)[:10])
    except ValueError:
        due = today
    overdue = max(0, (today - due).days)
    return round(min(1.0, 0.2 + 0.12 * overdue), 3)
