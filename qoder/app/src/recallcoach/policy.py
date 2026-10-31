"""记忆写入策略：纯函数，便于测试；不直接调用 PowerContext。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

WEAKNESS_LAPSE_THRESHOLD = 2
DAILY_WRITE_CAP = 20
MAX_TEXT_CHARS = 300

# 错因下拉里代表"具体混淆"的选项前缀；只有它才触发 misconception 分支。
MISCONCEPTION_CAUSE = "概念混淆"

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


def _weakness_branch(
    *,
    kp: str,
    kp_name: str,
    confidence: int,
    detail: str,
    lapses_7d: int,
    active: Mapping[tuple[str, str], str],
) -> Decision:
    """薄弱点分支：受 lapses 阈值约束。"""
    if lapses_7d < WEAKNESS_LAPSE_THRESHOLD:
        return Decision("skip", "lapses<2")

    hint = f"错因：{detail}。" if detail else ""
    text = _trim(f"[kp:{kp}] {kp_name}：{lapses_7d} 次未通过，最近一次自评 {confidence}/5。{hint}")
    if is_forbidden(text):
        return Decision("skip", "forbidden")

    target = active.get(("weakness", kp))
    if target:
        return Decision("revise", "dedupe", kind="weakness", text=text, target_entry_id=target)
    return Decision("write", "threshold-met", kind="weakness", text=text)


def _misconception_branch(
    *,
    kp: str,
    kp_name: str,
    cause: str | None,
    lapses_7d: int,
    active: Mapping[tuple[str, str], str],
) -> Decision | None:
    """具体混淆分支：只在用户于错因下拉里确认过混淆对象时触发。

    它不套 lapses 阈值（memory-design.md 第 3 节的两个分支各自独立判断）：
    这条结论来自用户填写而不是我们的推断，禁写清单第 5 条挡的是后者。
    """
    detail = (cause or "").strip()
    if not detail.startswith(MISCONCEPTION_CAUSE):
        return None
    # "概念混淆：把 read view 和锁的范围搞混" → 取出冒号后的混淆对象
    extra = detail[len(MISCONCEPTION_CAUSE) :].strip(" ：:，,。")
    if not extra:
        return None

    text = _trim(f"[kp:{kp}] {kp_name}：把 {extra} 混为一谈（第 {lapses_7d} 次）。")
    if is_forbidden(text):
        return Decision("skip", "forbidden")

    target = active.get(("misconception", kp))
    if target:
        return Decision("revise", "dedupe", kind="misconception", text=text, target_entry_id=target)
    return Decision("write", "concept-mix", kind="misconception", text=text)


def _collapse(decisions: list[Decision]) -> list[Decision]:
    """有分支要写时不再回传另一分支的"为什么没写"，避免证据里同时出现写与跳过。

    全为 skip 时按 (action, reason) 去重，保持顺序。
    """
    applied = [item for item in decisions if item.action != "skip"]
    if applied:
        return applied

    unique: list[Decision] = []
    seen: set[tuple[str, str]] = set()
    for item in decisions:
        key = (item.action, item.reason)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _apply_daily_cap(decisions: list[Decision], writes_today: int) -> list[Decision]:
    """第 4 节第 4 条：超过单日上限时不静默丢弃，把"本来要写什么"留在决策里。

    被拦下的 write/revise 降级为 skip + reason="daily-cap"，但保留 kind/text/target_entry_id，
    调用方据此写"待确认"审计并给前端标记；没打算写的分支不受上限影响，照原样回传真实原因。
    """
    if writes_today < DAILY_WRITE_CAP:
        return decisions

    blocked = [item for item in decisions if item.action in {"write", "revise"}]
    if not blocked:
        return decisions
    return [
        Decision(
            "skip",
            "daily-cap",
            kind=item.kind,
            text=item.text,
            target_entry_id=item.target_entry_id,
        )
        for item in blocked
    ]


def decide_answer(
    *,
    card: Mapping[str, Any],
    correct: bool,
    confidence: int,
    cause: str | None,
    lapses_7d: int,
    active: Mapping[tuple[str, str], str],
    writes_today: int,
    personalization: bool = True,
) -> list[Decision]:
    """一次作答后的写入决策，返回按分支顺序排列的决策列表。

    active 的键是 (kind, kp_id)，值是 entry_id。
    personalization=False 时策略层不产生任何写操作（memory-design.md 第 9 节）。
    """
    if not personalization:
        return [Decision("skip", "personalization-off")]
    if correct:
        return [Decision("skip", "correct")]

    kp = str(card["kp_id"])
    kp_name = str(card.get("kp_name") or kp)
    detail = (cause or "").strip()

    branches = [
        _weakness_branch(
            kp=kp, kp_name=kp_name, confidence=confidence, detail=detail, lapses_7d=lapses_7d, active=active
        ),
        _misconception_branch(kp=kp, kp_name=kp_name, cause=cause, lapses_7d=lapses_7d, active=active),
    ]
    return _apply_daily_cap(_collapse([branch for branch in branches if branch is not None]), writes_today)


def decide_session_end(
    *,
    chapter: str,
    total: int,
    correct: int,
    stuck: list[str],
    next_card: str | None,
    active_progress_entry: str | None,
    writes_today: int,
    personalization: bool = True,
) -> list[Decision]:
    """会话结束写一条进度叙事；当天已有则修订。"""
    if not personalization:
        return [Decision("skip", "personalization-off")]
    if total == 0:
        return [Decision("skip", "empty-session")]

    stuck_text = "、".join(stuck[:3]) if stuck else "无"
    next_text = f"下次从 {next_card} 开始" if next_card else "下次继续"
    text = _trim(f"[kp:{chapter}] {total} 张里答对 {correct}；卡住：{stuck_text}；{next_text}。")

    if is_forbidden(text):
        return [Decision("skip", "forbidden")]
    intended = (
        Decision("revise", "same-day", kind="progress", text=text, target_entry_id=active_progress_entry)
        if active_progress_entry
        else Decision("write", "session-end", kind="progress", text=text)
    )
    return _apply_daily_cap([intended], writes_today)
