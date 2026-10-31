"""端到端：真的拉起 tests/fake_pc_server.py 子进程，让记忆读写走 HTTP。

与 test_api.py 的区别：这里不用内存版 FakeGateway，链路与生产一致
（MemoryGateway → httpx → 127.0.0.1:随机端口 → FastAPI 假 Server），
所以序列化、路径、字段名、scope 建立这些细节也会被覆盖到。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from recallcoach.api import create_app
from recallcoach.config import Settings
from recallcoach.service import Service

FAKE_SERVER = Path(__file__).resolve().parent / "fake_pc_server.py"
TODAY = date(2026, 11, 1)
STARTUP_TIMEOUT = 25.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def pc_url(workdir: Path) -> Iterator[str]:
    """在随机端口起一个干净的假 PowerContext，退出时收掉进程。"""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log_path = workdir / "fake-pc.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(FAKE_SERVER), "--port", str(port)],
            cwd=str(FAKE_SERVER.parent),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        ready = False
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                ready = httpx.get(f"{url}/health/live", timeout=0.5).status_code == 200
            except httpx.HTTPError:
                ready = False
            if ready:
                break
            time.sleep(0.2)

        if not ready:
            log.flush()
            process.terminate()
            raise AssertionError(
                f"假 PowerContext 没在 {STARTUP_TIMEOUT} 秒内就绪（{url}）：\n"
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )

        yield url

        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:  # pragma: no cover - 仅进程赖着不走时触发
            process.kill()
            process.wait(5)


@dataclass
class Resp:
    """响应体随断言一起抛出，避免只看到一个光秃秃的 False。"""

    method: str
    path: str
    status: int
    body: str

    @property
    def json(self) -> dict[str, Any]:
        try:
            return json.loads(self.body)
        except ValueError:
            raise AssertionError(self.dump()) from None

    def dump(self) -> str:
        return f"\n请求：{self.method} {self.path}\n状态：HTTP {self.status}\n响应体：{self.body[:2000]}"


class Api:
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def get(self, path: str) -> Resp:
        response = self._client.get(path)
        out = Resp("GET", path, response.status_code, response.text)
        assert out.status == 200, out.dump()
        return out

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Resp:
        response = self._client.post(path, json=payload or {})
        out = Resp("POST", path, response.status_code, response.text)
        assert out.status == 200, out.dump()
        return out


def start_app(base_url: str, workdir: Path) -> tuple[Api, Service]:
    """按生产同款方式装配应用：真实 HTTP 网关 + 磁盘上的 app.db。"""
    settings = Settings(
        base_url=base_url,
        token="e2e-token",
        scope_id="",
        scope_title="RecallCoach 端到端",
        scope_key="recallcoach-e2e",
        db_path=workdir / "app.db",
        audit_path=workdir / "audit.jsonl",
        today_override=TODAY,
        personalization=True,
        port=0,
    )
    service = Service.bootstrap(settings)
    return Api(TestClient(create_app(service))), service


def fail_twice(api: Api, card_id: str) -> Resp:
    """同一知识点连错两次：第二次才达到写入阈值（memory-design.md 第 3 节）。"""
    api.post("/api/answer", {"card_id": card_id, "correct": False, "confidence": 1})
    return api.post("/api/answer", {"card_id": card_id, "correct": False, "confidence": 2})


def test_cold_start_has_no_memory_bonus(pc_url: str, workdir: Path) -> None:
    api, _ = start_app(pc_url, workdir)

    health = api.get("/api/health")
    assert health.json["pc_ready"] is True, health.dump()
    assert health.json["memory_refs"] == 0, health.dump()

    today = api.get("/api/today")
    assert today.json["mode"] == "on", today.dump()
    assert today.json["degraded"] is False, today.dump()
    items = today.json["items"]
    assert len(items) == 10, today.dump()
    assert [item["breakdown"]["weakness"] for item in items] == [0.0] * 10, today.dump()


def test_three_failures_write_then_revise_the_same_entry(pc_url: str, workdir: Path) -> None:
    api, _ = start_app(pc_url, workdir)

    first = api.post("/api/answer", {"card_id": "db-022", "correct": False, "confidence": 1})
    assert [item["action"] for item in first.json["decision"]] == ["skip"], first.dump()
    assert first.json["memory"][0]["status"] == "skipped", first.dump()

    second = api.post("/api/answer", {"card_id": "db-022", "correct": False, "confidence": 2})
    assert [item["action"] for item in second.json["decision"]] == ["write"], second.dump()
    assert second.json["memory"][0]["status"] == "written", second.dump()
    entry_id = second.json["memory"][0]["entry_id"]
    assert second.json["memory"][0]["version"] == 1, second.dump()

    third = api.post("/api/answer", {"card_id": "db-022", "correct": False, "confidence": 2})
    assert [item["action"] for item in third.json["decision"]] == ["revise"], third.dump()
    revised = third.json["memory"][0]
    assert revised["status"] == "revised", third.dump()
    assert revised["entry_id"] == entry_id, third.dump()
    assert revised["version"] == 2, third.dump()

    replay = api.get("/api/replay")
    entries = replay.json["entries"]
    assert [entry["entry_id"] for entry in entries] == [entry_id], replay.dump()
    assert entries[0]["version"] == 2, replay.dump()
    assert entries[0]["kind"] == "weakness", replay.dump()


def test_compare_shows_a_real_difference(pc_url: str, workdir: Path) -> None:
    api, _ = start_app(pc_url, workdir)

    cold = api.get("/api/compare?limit=5")
    assert cold.json["diff"] == [], cold.dump()

    # 挑队尾的卡：它没有别的原因能爬到第一位，位次差异只可能来自记忆
    target = api.get("/api/today").json["items"][-1]["id"]
    fail_twice(api, target)

    compare = api.get("/api/compare?limit=5")
    on_ids = [item["id"] for item in compare.json["on"]]
    off_ids = [item["id"] for item in compare.json["off"]]
    assert on_ids != off_ids, compare.dump()
    assert target in on_ids, compare.dump()
    moved = [row for row in compare.json["diff"] if row["card_id"] == target]
    # off 为 None：这张卡在基线里连前五都进不去，纯靠记忆爬到第一位
    assert moved == [{"card_id": target, "on": 1, "off": None}], compare.dump()
    assert target not in off_ids, compare.dump()


def test_replay_survives_an_app_restart(pc_url: str, workdir: Path) -> None:
    api, service = start_app(pc_url, workdir)
    fail_twice(api, "db-022")
    revised = api.post(
        "/api/answer", {"card_id": "db-022", "correct": False, "confidence": 3}
    ).json["memory"][0]
    entry_id = revised["entry_id"]

    before = api.get("/api/replay")
    assert len(before.json["entries"]) == 1, before.dump()

    service.conn.close()
    restarted, restarted_service = start_app(pc_url, workdir)
    try:
        after = restarted.get("/api/replay")
        assert [entry["entry_id"] for entry in after.json["entries"]] == [entry_id], after.dump()
        assert len(after.json["entries"]) == len(before.json["entries"]), after.dump()
        assert after.json["entries"][0]["version"] == revised["version"] == 2, after.dump()
        assert restarted.get("/api/health").json["memory_refs"] == 1, "本地记忆指针应随 app.db 一起活下来"
    finally:
        restarted_service.conn.close()
