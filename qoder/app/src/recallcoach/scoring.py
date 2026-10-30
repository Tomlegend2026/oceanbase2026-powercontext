"""打分：到期分 + 记忆命中加权，输出可解释的 breakdown。"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .scheduler import due_score

W_DUE = 0.5
W_WEAKNESS = 1.2
W_MISCONCEPTION = 1.0
W_PROGRESS = 0.3
W_FATIGUE = -0.4


def rank(
    cards: Iterable[dict[str, Any]],
    *,
    hits: Iterable[dict[str, Any]],
    answers_today: dict[str, int],
    today: date,
) -> list[dict[str, Any]]:
    """给每张卡打分并排序。

    hits 的每一项包含 entry_id / kind / kp_id（来自 memory_refs 或文本前缀）。
    """
    hit_list = list(hits)
    ranked: list[dict[str, Any]] = []
    for card in cards:
        kp = card["kp_id"]
        chapter = card.get("chapter") or ""

        weakness = next((h for h in hit_list if h["kind"] == "weakness" and h["kp_id"] == kp), None)
        misconception = next((h for h in hit_list if h["kind"] == "misconception" and h["kp_id"] == kp), None)
        progress = next(
            (h for h in hit_list if h["kind"] == "progress" and str(h.get("kp_id") or "").startswith(chapter)),
            None,
        )
        practiced = answers_today.get(kp, 0)

        base = round(W_DUE * due_score(card["due_at"], today), 3)
        weakness_part = W_WEAKNESS if weakness else 0.0
        misconception_part = W_MISCONCEPTION if misconception else 0.0
        progress_part = W_PROGRESS if progress else 0.0
        fatigue_part = round(W_FATIGUE * min(practiced, 2) / 2, 3) if practiced else 0.0

        total = round(base + weakness_part + misconception_part + progress_part + fatigue_part, 3)
        ranked.append(
            {
                **card,
                "score": total,
                "breakdown": {
                    "due": base,
                    "weakness": weakness_part,
                    "misconception": misconception_part,
                    "progress": progress_part,
                    "fatigue": fatigue_part,
                },
                "memory_hits": [
                    {"entry_id": hit["entry_id"], "kind": hit["kind"]}
                    for hit in (weakness, misconception, progress)
                    if hit
                ],
                "practiced_today": practiced,
            }
        )

    ranked.sort(key=lambda item: (-item["score"], -item["breakdown"]["due"], item["id"]))
    return ranked
