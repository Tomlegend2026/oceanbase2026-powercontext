"""业务编排：把调度、打分、记忆策略与 PowerContext 串起来。"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import db as dbmod
from . import policy, scheduler, scoring
from .config import APP_DIR, Settings, load_settings
from .pc import MemoryGateway, PowerContextError

KP_PATTERN = re.compile(r"\[kp:([^\]]+)\]")


class Gateway(Protocol):
    def live(self) -> bool: ...
    def capabilities(self) -> dict[str, Any]: ...
    def ensure_scope(self, title: str, summary: str, idempotency_key: str) -> str: ...
    def remember(self, scope_id: str, *, kind: str, text: str, reason: str | None = None) -> dict[str, Any]: ...
    def search(self, scope_id: str, query: str, *, limit: int = 20, mode: str = "auto") -> dict[str, Any]: ...
    def entries(self, scope_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]: ...
    def prepare(self, scope_id: str, query: str, *, max_bytes: int = 2000) -> dict[str, Any]: ...
    def revise(self, scope_id: str, citation: dict[str, Any], *, kind: str, text: str) -> dict[str, Any]: ...
    def retire(self, scope_id: str, citation: dict[str, Any], *, reason: str = "已掌握") -> dict[str, Any]: ...


def _now() -> datetime:
    return datetime.now()


class Service:
    def __init__(self, settings: Settings, conn: sqlite3.Connection, gateway: Gateway) -> None:
        self.settings = settings
        self.conn = conn
        self.gateway = gateway

    # ---------- 构造 ----------

    @classmethod
    def bootstrap(
        cls,
        settings: Settings | None = None,
        gateway: Gateway | None = None,
        *,
        seed: bool = True,
    ) -> "Service":
        settings = settings or load_settings()
        conn = dbmod.connect(settings.db_path)
        if seed:
            seed_path = APP_DIR.parent / "seed" / "db-systems.cards.json"
            if seed_path.exists():
                dbmod.init_db(conn, seed_path, settings.today())
        if gateway is None:
            gateway = MemoryGateway(
                settings.base_url,
                settings.token,
                settings.audit_path,
                personalization=settings.personalization,
            )
        return cls(settings, conn, gateway)

    # ---------- 基础 ----------

    @property
    def today(self) -> date:
        return self.settings.today()

    def memory_ready(self) -> bool:
        try:
            return bool(self.gateway.live())
        except Exception:  # noqa: BLE001 - 探测失败一律按不可用处理
            return False

    def scope(self) -> str:
        scope = self.settings.scope_id or dbmod.get_setting(self.conn, "scope_id") or ""
        if scope:
            return scope
        if not self.memory_ready():
            return ""
        try:
            scope = self.gateway.ensure_scope(
                self.settings.scope_title,
                "RecallCoach 复习教练：薄弱点、混淆、讲解偏好、进度叙事。",
                self.settings.scope_key,
            )
        except PowerContextError:
            return ""
        dbmod.put_setting(self.conn, "scope_id", scope)
        return scope

    def _active_map(self) -> dict[tuple[str, str], str]:
        mapping: dict[tuple[str, str], str] = {}
        for row in dbmod.memory_refs(self.conn):
            if row.get("kp_id") and row.get("kind"):
                mapping[(row["kind"], row["kp_id"])] = row["entry_id"]
        return mapping

    def _writes_today(self) -> int:
        today = self.today.isoformat()
        return sum(1 for row in dbmod.memory_refs(self.conn) if str(row.get("written_at", ""))[:10] == today)

    # ---------- 检索 ----------

    @staticmethod
    def _focus_query(card: dict[str, Any]) -> str:
        """聚焦到当前最该练的一张卡：章节 + 知识点。

        短 query 才能过服务端的覆盖率准入门槛；把整队知识点拼进去会让 context/prepare 返回 empty。
        """
        parts = [str(card.get("chapter_title") or ""), str(card.get("kp_name") or "")]
        return " ".join(part for part in parts if part)[:120]

    @staticmethod
    def _kp_from_text(text: str) -> str | None:
        match = KP_PATTERN.search(text or "")
        return match.group(1) if match else None

    def _resolve_hits(self, raw_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for hit in raw_hits:
            citation = hit.get("citation") or {}
            entry_id = citation.get("entry_id") or hit.get("entry_id")
            if not entry_id:
                continue
            ref = dbmod.memory_ref(self.conn, str(entry_id))
            text = str(hit.get("text") or "")
            if ref and ref.get("kp_id"):
                kind, kp_id = str(ref["kind"]), str(ref["kp_id"])
            else:
                kind = str(hit.get("kind") or ("preference" if text.startswith("[pref]") else "weakness"))
                kp_id = self._kp_from_text(text) or ""
            resolved.append({"entry_id": str(entry_id), "kind": kind, "kp_id": kp_id})
        return resolved

    def _entry_hits(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """用记忆清单构造命中集合：条目总量小，比一次长 query 更可靠。"""
        hits: list[dict[str, Any]] = []
        for entry in entries:
            if str(entry.get("state") or "active") != "active":
                continue
            entry_id = entry.get("entry_id")
            if not entry_id:
                continue
            ref = dbmod.memory_ref(self.conn, str(entry_id))
            text = str(entry.get("text") or "")
            kind = str((ref or {}).get("kind") or entry.get("kind") or "weakness")
            kp_id = str((ref or {}).get("kp_id") or self._kp_from_text(text) or "")
            if kp_id:
                hits.append({"entry_id": str(entry_id), "kind": kind, "kp_id": kp_id})
        return hits

    @staticmethod
    def _merge_hits(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = {hit["entry_id"] for hit in primary}
        merged = list(primary)
        for hit in extra:
            if hit["entry_id"] not in seen:
                merged.append(hit)
                seen.add(hit["entry_id"])
        return merged

    # ---------- 队列 ----------

    def _ranked(self, personalization: bool, *, want_coach_note: bool = True) -> dict[str, Any]:
        cards = dbmod.due_cards(self.conn, self.today, limit=200)
        mode = "off"
        degraded = False
        coach_note = ""
        hits: list[dict[str, Any]] = []
        answers_today = dbmod.answers_today_by_kp(self.conn, self.today)

        def rank(current_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return scoring.rank(cards, hits=current_hits, answers_today=answers_today, today=self.today)

        items = rank(hits)

        if personalization:
            if not self.memory_ready():
                degraded, mode = True, "offline"
            else:
                mode = "on"
                scope = self.scope()
                if scope:
                    try:
                        # 命中集合先来自全量清单：记忆条目少、按 kp 精确对齐，不受 query 表达影响。
                        inventory = self._entry_hits(self.gateway.entries(scope))
                        items = rank(inventory)
                        hits = inventory

                        # 再用队列首位（也就是最该练的那张卡）做一次聚焦检索与上下文组装。
                        # 长 query 会撞上服务端的覆盖率准入门槛，导致 prepare 返回 empty。
                        if items:
                            query = self._focus_query(items[0])
                            found = self.gateway.search(scope, query, limit=20)
                            merged = self._merge_hits(hits, self._resolve_hits(found.get("hits") or []))
                            if len(merged) != len(hits):
                                hits = merged
                                items = rank(hits)
                            prepared = (
                                self.gateway.prepare(scope, query)
                                if want_coach_note
                                else {"status": None, "content": None}
                            )
                            if prepared.get("status") == "ready" and prepared.get("content"):
                                coach_note = str(prepared["content"])
                    except PowerContextError:
                        degraded, mode, hits, coach_note = True, "offline", [], ""
                        items = rank([])

        items = items[:10]
        return {"items": items, "mode": mode, "degraded": degraded, "coach_note": coach_note, "hits": hits}

    def build_today(self, personalization: bool | None = None) -> dict[str, Any]:
        enabled = self.settings.personalization if personalization is None else personalization
        payload = self._ranked(enabled)
        session = dbmod.reuse_session(
            self.conn,
            dbmod.course_id(self.conn),
            "normal" if enabled else "ab_off",
            _now(),
        )
        payload.update({"date": self.today.isoformat(), "session_id": session["id"], "personalization": enabled})
        return payload

    # ---------- 作答 ----------

    def submit_answer(
        self,
        *,
        card_id: str,
        correct: bool,
        confidence: int,
        cause: str | None = None,
        latency_ms: int = 0,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        card = dbmod.card_by_id(self.conn, card_id)
        if not card:
            return None

        now = _now()
        session_id = session_id or dbmod.reuse_session(
            self.conn,
            dbmod.course_id(self.conn),
            "normal" if self.settings.personalization else "ab_off",
            now,
        )["id"]
        # off 会话是对照组：既不能读记忆，也不能写记忆，否则污染下一次比较（memory-design.md 第 9 节）
        enabled = dbmod.session_mode(self.conn, session_id) != "ab_off"
        dbmod.add_answer(
            self.conn,
            card_id=card_id,
            session_id=session_id,
            correct=correct,
            confidence=confidence,
            latency_ms=latency_ms,
            cause=cause,
            now=now,
        )
        new_state = scheduler.review(card, correct=correct, confidence=confidence, today=self.today)
        dbmod.save_schedule(self.conn, card_id, new_state)

        decisions = policy.decide_answer(
            card=card,
            correct=correct,
            confidence=confidence,
            cause=cause,
            lapses_7d=dbmod.lapses_in_window(self.conn, card["kp_id"], now - timedelta(days=7), now),
            active=self._active_map(),
            writes_today=self._writes_today(),
            personalization=enabled,
        )

        return {
            "card_id": card_id,
            "session_id": session_id,
            "schedule": new_state,
            "personalization": enabled,
            "decision": [decision.as_dict() for decision in decisions],
            "memory": self._execute(decisions, card),
        }

    def _execute(self, decisions: list[policy.Decision], card: dict[str, Any]) -> list[dict[str, Any]]:
        """逐条执行决策，返回与 decision 列表同序的结果；不在线时保持 skipped，不伪造写入。"""
        results: list[dict[str, Any]] = []
        for decision in decisions:
            if decision.action not in {"write", "revise"} or not self.memory_ready():
                results.append({"status": "skipped", "decision": decision.as_dict()})
                continue
            try:
                results.append(self._apply_decision(decision, card))
            except PowerContextError as exc:
                results.append({"status": "error", "error": str(exc), "decision": decision.as_dict()})
        return results

    def _apply_decision(self, decision: policy.Decision, card: dict[str, Any]) -> dict[str, Any]:
        scope = self.scope()
        if not scope:
            return {"status": "offline", "decision": decision.as_dict()}
        assert decision.kind and decision.text  # noqa: S101 - 策略保证非空

        if decision.action == "write":
            result = self.gateway.remember(
                scope,
                kind=decision.kind,
                text=decision.text,
                reason=f"recallcoach:{decision.reason}",
            )
            entry_id = result.get("entry_id")
            if not entry_id:
                return {"status": "error", "error": "响应缺少 entry_id", "decision": decision.as_dict()}
            dbmod.save_memory_ref(
                self.conn, str(entry_id), result.get("citation") or {}, card["kp_id"], decision.kind, _now()
            )
            return {
                "status": "written",
                "entry_id": entry_id,
                "version": result.get("version"),
                "kind": decision.kind,
                "text": decision.text,
                "decision": decision.as_dict(),
            }

        ref = dbmod.memory_ref(self.conn, decision.target_entry_id or "")
        if not ref:
            return self._apply_decision(
                policy.Decision("write", decision.reason, kind=decision.kind, text=decision.text), card
            )
        result = self.gateway.revise(scope, ref["citation"], kind=decision.kind, text=decision.text)
        entry = result.get("entry") or {}
        citation = entry.get("citation") or ref["citation"]
        dbmod.save_memory_ref(
            self.conn, ref["entry_id"], citation, card["kp_id"], decision.kind, _now()
        )
        return {
            "status": "revised",
            "entry_id": ref["entry_id"],
            "version": entry.get("version"),
            "kind": decision.kind,
            "text": decision.text,
            "decision": decision.as_dict(),
        }

    # ---------- 会话结束 ----------

    def end_session(self, session_id: str) -> dict[str, Any]:
        answers = dbmod.session_answers(self.conn, session_id)
        total = len(answers)
        correct = sum(1 for row in answers if int(row["correct"]) == 1)
        stuck: list[str] = []
        chapter = ""
        for row in answers:
            if int(row["correct"]) == 0 and row["kp_name"] not in stuck:
                stuck.append(row["kp_name"])
        if answers:
            first_card = dbmod.card_by_id(self.conn, answers[0]["card_id"])
            chapter = str(first_card["chapter"]) if first_card else ""

        mode = dbmod.session_mode(self.conn, session_id)
        enabled = mode != "ab_off"
        # 次日起点：本轮没答对的第一张卡；全答对则用当前排序的首位
        queue = self._ranked(enabled, want_coach_note=False)["items"]
        done = {row["card_id"] for row in answers if int(row["correct"]) == 1}
        next_start = next((item["id"] for item in queue if item["id"] not in done), None)
        next_start = next_start or (queue[0]["id"] if queue else None)

        active_progress = next(
            (
                row["entry_id"]
                for row in dbmod.memory_refs(self.conn)
                if row.get("kind") == "progress" and row.get("kp_id") == chapter
            ),
            None,
        )
        decisions = policy.decide_session_end(
            chapter=chapter or "db",
            total=total,
            correct=correct,
            stuck=stuck,
            next_card=next_start,
            active_progress_entry=active_progress,
            writes_today=self._writes_today(),
            personalization=enabled,
        )

        memory: list[dict[str, Any]] = []
        if any(item.action in {"write", "revise"} for item in decisions):
            card = {"kp_id": chapter or "db", "kp_name": chapter or "会话"}
            memory = self._execute(decisions, card)
        else:
            memory = [{"status": "skipped", "decision": item.as_dict()} for item in decisions]

        dbmod.close_session(self.conn, session_id, _now())
        return {
            "session_id": session_id,
            "total": total,
            "correct": correct,
            "stuck": stuck,
            "personalization": enabled,
            # 次日起点与进度条目：小结页与"上次停在 db-008"都靠这两个字段
            "next_start": next_start,
            "progress_entry_id": next(
                (item.get("entry_id") for item in memory if item.get("entry_id")), None
            ),
            "decision": [item.as_dict() for item in decisions],
            "memory": memory,
        }

    # ---------- Replay ----------

    def replay(self, kind: str | None = None) -> dict[str, Any]:
        scope = self.scope()
        entries: list[dict[str, Any]] = []
        note = ""
        if not scope:
            note = "记忆离线：先启动 PowerContext"
        else:
            try:
                entries = self.gateway.entries(scope, include_inactive=True)
            except PowerContextError as exc:
                note = f"读取记忆失败：{exc}"
        cards = dbmod.all_cards(self.conn)
        for entry in entries:
            kp = self._kp_from_text(str(entry.get("text") or ""))
            entry["kp_id"] = kp
            entry["hit_cards"] = [c["id"] for c in cards if kp and c["kp_id"] == kp][:5]
        if kind:
            entries = [entry for entry in entries if entry.get("kind") == kind]
        return {"entries": entries, "note": note}

    def _personal_on(self) -> bool:
        return bool(self.settings.personalization)

    @staticmethod
    def _moved(before: list[str], after: list[str]) -> list[dict[str, Any]]:
        """只报告位次真的变了的卡片，前端据此高亮。"""
        changed = []
        for index, card_id in enumerate(after):
            previous = before.index(card_id) + 1 if card_id in before else None
            if previous is not None and previous != index + 1:
                changed.append({"card_id": card_id, "before": previous, "after": index + 1})
        for card_id in before:
            if card_id not in after:
                changed.append({"card_id": card_id, "before": before.index(card_id) + 1, "after": None})
        return changed

    def revise_memory(self, entry_id: str, text: str) -> dict[str, Any]:
        ref = dbmod.memory_ref(self.conn, entry_id)
        if not ref:
            return {"ok": False, "error": "本地没有这条记忆的 citation，无法修订"}
        scope = self.scope()
        if not scope:
            return {"ok": False, "error": "记忆离线"}
        enabled = self._personal_on()
        before = [item["id"] for item in self._ranked(enabled, want_coach_note=False)["items"]]
        try:
            result = self.gateway.revise(scope, ref["citation"], kind=str(ref["kind"]), text=text)
        except PowerContextError as exc:
            return {"ok": False, "error": str(exc)}
        entry = result.get("entry") or {}
        citation = entry.get("citation") or ref["citation"]
        dbmod.save_memory_ref(self.conn, entry_id, citation, ref.get("kp_id"), str(ref["kind"]), _now())
        items = self._ranked(enabled, want_coach_note=False)["items"]
        return {
            "ok": True,
            "version": entry.get("version"),
            "entry_id": entry_id,
            "items": items,
            "moved": self._moved(before, [item["id"] for item in items]),
        }

    def retire_memory(self, entry_id: str, reason: str = "已掌握") -> dict[str, Any]:
        ref = dbmod.memory_ref(self.conn, entry_id)
        if not ref:
            return {"ok": False, "error": "本地没有这条记忆的 citation，无法退休"}
        scope = self.scope()
        if not scope:
            return {"ok": False, "error": "记忆离线"}
        enabled = self._personal_on()
        before = [item["id"] for item in self._ranked(enabled, want_coach_note=False)["items"]]
        try:
            self.gateway.retire(scope, ref["citation"], reason=reason)
        except PowerContextError as exc:
            return {"ok": False, "error": str(exc)}
        dbmod.drop_memory_ref(self.conn, entry_id)
        items = self._ranked(enabled, want_coach_note=False)["items"]
        return {
            "ok": True,
            "entry_id": entry_id,
            "state": "retired",
            "items": items,
            "moved": self._moved(before, [item["id"] for item in items]),
        }

    # ---------- 对照与证据 ----------

    def compare(self, limit: int = 5) -> dict[str, Any]:
        on = self._ranked(True)
        off = self._ranked(False)
        on_ids = [item["id"] for item in on["items"][:limit]]
        off_ids = [item["id"] for item in off["items"][:limit]]
        diff = []
        for card_id in on_ids:
            if card_id in off_ids:
                on_pos, off_pos = on_ids.index(card_id), off_ids.index(card_id)
                if on_pos != off_pos:
                    diff.append({"card_id": card_id, "on": on_pos + 1, "off": off_pos + 1})
            else:
                diff.append({"card_id": card_id, "on": on_ids.index(card_id) + 1, "off": None})
        return {
            "on": [{"id": i["id"], "kp_name": i["kp_name"], "score": i["score"], "memory_hits": i["memory_hits"]} for i in on["items"][:limit]],
            "off": [{"id": i["id"], "kp_name": i["kp_name"], "score": i["score"]} for i in off["items"][:limit]],
            "diff": diff,
            "mode": {"on": on["mode"], "off": off["mode"]},
            "degraded": on["degraded"],
        }

    def evidence(self, limit: int = 50) -> dict[str, Any]:
        audit_path: Path = self.settings.audit_path
        lines: list[dict[str, Any]] = []
        if audit_path.exists():
            for raw in audit_path.read_text(encoding="utf-8").splitlines()[-limit:]:
                try:
                    lines.append(json.loads(raw))
                except ValueError:
                    continue
        replay = self.replay()
        entries = replay["entries"]
        compare = self.compare(limit=5)
        answers = dbmod.recent_answers(self.conn, limit=20)

        report: list[str] = [
            "# RecallCoach 证据报告",
            "",
            f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"- 业务日期：{self.today.isoformat()}",
            f"- PowerContext：{self.settings.base_url}",
            f"- scope：`{self.scope() or '未建立（离线）'}`",
            f"- 卡片数：{dbmod.count_cards(self.conn)}；本地记忆指针：{len(dbmod.memory_refs(self.conn))}",
            "",
            "## 记忆条目",
            "",
            "| entry_id | kind | version | state | 文本 |",
            "|---|---|---|---|---|",
        ]
        for entry in entries[:20]:
            text = str(entry.get("text") or "").replace("|", "\\|")[:80]
            report.append(
                f"| {entry.get('entry_id')} | {entry.get('kind')} | {entry.get('version')} | {entry.get('state')} | {text} |"
            )
        report += ["", "## A/B 排序差异", "", "| 卡片 | 个性化位次 | 基线位次 |", "|---|---|---|"]
        for item in compare["diff"]:
            report.append(f"| {item['card_id']} | {item['on']} | {item['off']} |")
        report += ["", "## 最近作答", "", "| 时间 | 卡片 | 正确 | 自评 |", "|---|---|---|---|"]
        for row in answers:
            report.append(
                f"| {row['ts']} | {row['card_id']} | {'是' if int(row['correct']) else '否'} | {row['confidence']} |"
            )
        if not compare["diff"]:
            report.append("")
            report.append("> 本次没有位次差异：当前记忆还没有命中今日队列里的卡片。")

        return {"audit": lines, "report_markdown": "\n".join(report), "count": len(lines)}

    # ---------- 健康 ----------

    def health(self) -> dict[str, Any]:
        ready = self.memory_ready()
        caps: dict[str, Any] = {}
        if ready:
            try:
                caps = self.gateway.capabilities()
            except PowerContextError:
                caps = {}
        modes = None
        if isinstance(caps, dict):
            modes = caps.get("search_modes") or (caps.get("memory") or {}).get("search_modes")
        return {
            "pc_ready": ready,
            "capabilities": caps,
            "memory_search_modes": modes,
            "scope_id": self.scope(),
            "cards": dbmod.count_cards(self.conn),
            "memory_refs": len(dbmod.memory_refs(self.conn)),
            "audit_path": str(self.settings.audit_path),
            "db_path": str(self.settings.db_path),
            "date": self.today.isoformat(),
            "personalization": self.settings.personalization,
        }
