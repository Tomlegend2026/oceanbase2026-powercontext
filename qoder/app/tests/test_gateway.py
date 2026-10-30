import json

import httpx
import pytest

from recallcoach.pc import MemoryGateway, PowerContextError


def make_gateway(workdir, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pc")
    return MemoryGateway(
        "http://pc",
        "secret-token",
        workdir / "audit.jsonl",
        client=client,
    )


def test_remember_parses_entry_and_writes_audit(workdir):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"entry": {"citation": {"entry_id": "e1", "entry_version_id": "v1-e1"}, "version": 1}},
            headers={"X-PowerContext-Request-ID": "rid-1"},
        )

    gateway = make_gateway(workdir, handler)
    result = gateway.remember("scope-1", kind="weakness", text="[kp:db/ch6] 卡住", reason="test")

    assert seen["path"] == "/v1/memory/remember"
    assert seen["auth"] == "Bearer secret-token"
    assert seen["body"]["scope_id"] == "scope-1"
    assert result["entry_id"] == "e1"

    line = json.loads((workdir / "audit.jsonl").read_text(encoding="utf-8").strip())
    assert line["request_id"] == "rid-1"
    assert line["op"] == "memory.remember"
    assert line["status"] == "ok"
    assert "secret-token" not in json.dumps(line, ensure_ascii=False)


def test_search_returns_mode_and_hits(workdir):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"mode": "fts", "hits": [{"citation": {"entry_id": "e2"}}]})

    gateway = make_gateway(workdir, handler)
    result = gateway.search("scope-1", "事务 隔离级别")
    assert result["mode"] == "fts"
    assert result["hits"][0]["citation"]["entry_id"] == "e2"


def test_error_status_raises_with_body(workdir):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    gateway = make_gateway(workdir, handler)
    with pytest.raises(PowerContextError) as exc:
        gateway.entries("scope-1")
    assert exc.value.status == 401


def test_prepare_falls_back_without_assembly_on_422(workdir):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(422, json={"detail": "assembly unsupported"})
        return httpx.Response(200, json={"status": "ready", "content": "- [kp:db/ch6] 卡住"})

    gateway = make_gateway(workdir, handler)
    result = gateway.prepare("scope-1", "事务 read view")
    assert result["status"] == "ready"
    assert "assembly" in bodies[0]
    assert "assembly" not in bodies[1]


def test_entries_uses_post_and_normalizes_shapes(workdir):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/memory/entries/list"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["scope_id"] == "scope-1"
        assert body["include_inactive"] is True
        return httpx.Response(
            200,
            json={
                "entries": [
                    {"citation": {"entry_id": "e3"}, "kind": "weakness", "text": "x", "version": 2, "state": "active"}
                ]
            },
        )

    gateway = make_gateway(workdir, handler)
    entries = gateway.entries("scope-1", include_inactive=True)
    assert entries[0]["entry_id"] == "e3"
    assert entries[0]["version"] == 2
