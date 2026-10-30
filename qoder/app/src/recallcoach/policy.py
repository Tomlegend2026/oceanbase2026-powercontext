"""记忆写入策略：纯函数，便于测试；不直接调用 PowerContext。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

WEAKNESS_LAPSE_THRESHOLD = 2
DAILY_WRITE_CAP = 20
MAX_TEXT_CHARS = 300

FORBIDDEN_MARKERS = ("token", "Bearer", "http://", "https://", "@")


@dataclass(frozen=True)
class Decision:
    action: str  # skip | write | revise
    reason: str
    kind: str | None = None
    text: str | None = None
    target_entry_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "kind": self.kind,
            "text": self.text,
            "target_entry_id": self.target_entry_id,
        }


def _trim(text: str) -> str:
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1] + "…"


def is_forbidden(text: str) -> bool:
    if len(text) > MAX_TEXT_CHARS:
        return True
    return any(marker in text for marker in FORBIDDEN_MARKERS)


def decide_answer(
    *,
    card: Mapping[str, Any],
    correct: bool,
    confidence: int,
    cause: str | None,
    lapses_7d: int,
    active: Mapping[tuple[str, str], str],
    writes_today: int,
) -> Decision:
    """一次作答后是否写入记忆。

    active 的键是 (kind, kp_id)，值是 entry_id。
    """
    kp = str(card["kp_id"])
    kp_name = str(card.get("kp_name") or kp)

    if writes_today >= DAILY_WRITE_CAP:
        return Decision("skip", "daily-cap")
    if correct:
        return Decision("skip", "correct")
    if lapses_7d < WEAKNESS_LAPSE_THRESHOLD:
        return Decision("skip", "lapses<2")

    detail = (cause or "").strip()
    if detail.startswith("概念混淆"):
        kind = "misconception"
        extra = detail.replace("概念混淆", "", 1).strip(" ：:，,。")
        text = f"[kp:{kp}] {kp_name}：{extra or '与其他概念混淆'}（第 {lapses_7d} 次）。"
    else:
        kind = "weakness"
        hint = f"错因：{detail}。" if detail else ""
        text = f"[kp:{kp}] {kp_name}：{lapses_7d} 次未通过，最近一次自评 {confidence}/5。{hint}"

    text = _trim(text)
    if is_forbidden(text):
        return Decision("skip", "forbidden")

    target = active.get((kind, kp))
    if target:
        return Decision("revise", "dedupe", kind=kind, text=text, target_entry_id=target)
    return Decision("write", "threshold-met", kind=kind, text=text)


def decide_session_end(
    *,
    chapter: str,
    total: int,
    correct: int,
    stuck: list[str],
    next_card: str | None,
    active_progress_entry: str | None,
    writes_today: int,
) -> Decision:
    """会话结束写一条进度叙事；当天已有则修订。"""
    if writes_today >= DAILY_WRITE_CAP:
        return Decision("skip", "daily-cap")
    if total == 0:
        return Decision("skip", "empty-session")

    stuck_text = "、".join(stuck[:3]) if stuck else "无"
    next_text = next_card or "下次继续"
    text = _trim(f"[kp:{chapter}] 本次 {total} 张，答对 {correct}；卡住：{stuck_text}；{next_text}。")

    if is_forbidden(text):
        return Decision("skip", "forbidden")
    if active_progress_entry:
        return Decision("revise", "same-day", kind="progress", text=text, target_entry_id=active_progress_entry)
    return Decision("write", "session-end", kind="progress", text=text)
