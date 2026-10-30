from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from recallcoach import db as dbmod
from recallcoach.config import Settings
from recallcoach.service import Service

SEED = Path(__file__).parents[2] / "seed" / "db-systems.cards.json"


class FakeGateway:
    """内存版 PowerContext：行为与真实接口的形状一致，便于断言策略效果。"""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.online = True
        self.scope = "scope-test"
        self.counter = 0

    def live(self) -> bool:
        return self.online

    def capabilities(self) -> dict:
        return {"memory": {"search_modes": ["auto", "fts"]}}

    def ensure_scope(self, title: str, summary: str, idempotency_key: str) -> str:
        self.calls.append(("ensure_scope", idempotency_key))
        return self.scope

    def remember(self, scope_id: str, *, kind: str, text: str, reason: str | None = None) -> dict:
        self.counter += 1
        entry_id = f"e{self.counter:04d}"
        citation = {
            "entry_id": entry_id,
            "entry_version_id": f"v1-{entry_id}",
            "memory_ref": {"artifact_id": "m-1", "revision": 1},
        }
        self.store[entry_id] = {
            "entry_id": entry_id,
            "kind": kind,
            "text": text,
            "version": 1,
            "state": "active",
            "citation": citation,
        }
        self.calls.append(("remember", kind, text, reason))
        return {"entry_id": entry_id, "citation": citation, "version": 1}

    def search(self, scope_id: str, query: str, *, limit: int = 20, mode: str = "auto") -> dict:
        self.calls.append(("search", query))
        hits = []
        for entry in self.store.values():
            if entry["state"] != "active":
                continue
            if any(term and term in entry["text"] for term in str(query).split()):
                hits.append({"citation": entry["citation"], "text": entry["text"], "kind": entry["kind"]})
        return {"mode": "fts", "hits": hits}

    def entries(self, scope_id: str) -> list[dict]:
        self.calls.append(("entries",))
        return [dict(entry) for entry in self.store.values()]

    def prepare(self, scope_id: str, query: str, *, max_bytes: int = 2000) -> dict:
        self.calls.append(("prepare", query))
        active = [entry for entry in self.store.values() if entry["state"] == "active"]
        if not active:
            return {"status": "empty", "content": None}
        return {"status": "ready", "content": "\n".join(f"- {entry['text']}" for entry in active)}

    def revise(self, scope_id: str, citation: dict, *, kind: str, text: str) -> dict:
        entry = self.store[citation["entry_id"]]
        entry["text"] = text
        entry["kind"] = kind
        entry["version"] += 1
        entry["citation"] = {**citation, "entry_version_id": f"v{entry['version']}-{entry['entry_id']}"}
        self.calls.append(("revise", entry["entry_id"], text))
        return {"entry": dict(entry)}

    def retire(self, scope_id: str, citation: dict, *, reason: str = "已掌握") -> dict:
        entry = self.store[citation["entry_id"]]
        entry["state"] = "retired"
        self.calls.append(("retire", entry["entry_id"]))
        return {"ok": True}


@pytest.fixture
def workdir() -> Path:
    """不使用 pytest 的临时目录：沙箱环境会拒绝删除/扫描系统 temp。"""
    path = Path(__file__).parents[1] / ".runtime" / f"case-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def settings(workdir: Path) -> Settings:
    return Settings(
        base_url="http://fake-pc",
        token="test-token",
        scope_id="",
        scope_title="RecallCoach 测试",
        scope_key="recallcoach-test",
        db_path=workdir / "app.db",
        audit_path=workdir / "audit.jsonl",
        today_override=date(2026, 11, 1),
        personalization=True,
        port=8080,
    )


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def service(settings: Settings, gateway: FakeGateway) -> Service:
    conn = dbmod.connect(settings.db_path)
    dbmod.init_db(conn, SEED, settings.today())
    return Service(settings, conn, gateway)


@pytest.fixture
def client(service: Service):
    from fastapi.testclient import TestClient

    from recallcoach.api import create_app

    return TestClient(create_app(service))
