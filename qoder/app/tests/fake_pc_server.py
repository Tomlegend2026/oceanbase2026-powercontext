"""离线开发用的假 PowerContext Server（仅用于本地联调与测试，不是产品路径）。

启动：uv run python tests/fake_pc_server.py --port 8099
它只实现 RecallCoach 用到的那几个接口，行为与真实接口的形状一致：
remember / search / entries.list / revise / retire / prepare / scopes / capabilities。
"""

from __future__ import annotations

import argparse
import itertools
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Fake PowerContext")
STATE = {"entries": {}, "counter": itertools.count(1), "scope": "scope-fake"}


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-PowerContext-Request-ID"] = f"fake-{datetime.now().strftime('%H%M%S%f')}"
    return response


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    return {"status": "ready"}


@app.get("/v1/capabilities")
def capabilities():
    return {"memory": {"search_modes": ["auto", "fts"]}}


@app.post("/v1/scopes")
def create_scope(payload: dict):
    return {"scope_id": STATE["scope"], "title": payload.get("title")}


def _entry(entry_id: str) -> dict:
    item = STATE["entries"][entry_id]
    return {
        "citation": {
            "entry_id": entry_id,
            "entry_version_id": f"v{item['version']}-{entry_id}",
            "memory_ref": {"artifact_id": "m-1", "revision": 1},
        },
        "kind": item["kind"],
        "text": item["text"],
        "version": item["version"],
        "state": item["state"],
    }


@app.post("/v1/memory/remember")
def remember(payload: dict):
    entry_id = f"e{next(STATE['counter']):04d}"
    STATE["entries"][entry_id] = {
        "kind": payload["kind"],
        "text": payload["text"],
        "version": 1,
        "state": "active",
    }
    return {"entry": _entry(entry_id)}


@app.post("/v1/memory/search")
def search(payload: dict):
    query = str(payload.get("query", ""))
    hits = []
    for entry_id in STATE["entries"]:
        item = _entry(entry_id)
        if item["state"] != "active":
            continue
        if any(token and token in item["text"] for token in query.split()):
            hits.append(item)
    return {"mode": "fts", "hits": hits[: int(payload.get("limit", 10))]}


@app.post("/v1/memory/entries/list")
def entries(payload: dict | None = None):
    payload = payload or {}
    include_inactive = bool(payload.get("include_inactive"))
    items = [
        _entry(entry_id)
        for entry_id in STATE["entries"]
        if include_inactive or _entry(entry_id)["state"] == "active"
    ]
    return {"entries": items}


@app.post("/v1/memory/entries/revise")
def revise(payload: dict):
    entry_id = payload["citation"]["entry_id"]
    item = STATE["entries"][entry_id]
    item["text"] = payload["text"]
    item["kind"] = payload.get("kind", item["kind"])
    item["version"] += 1
    return {"entry": _entry(entry_id)}


@app.post("/v1/memory/entries/retire")
def retire(payload: dict):
    entry_id = payload["citation"]["entry_id"]
    STATE["entries"][entry_id]["state"] = "retired"
    return {"ok": True, "entry": _entry(entry_id)}


@app.post("/v1/context/prepare")
def prepare(payload: dict):
    active = [_entry(entry_id) for entry_id in STATE["entries"] if _entry(entry_id)["state"] == "active"]
    if not active:
        return {"status": "empty", "content": None}
    return {"status": "ready", "content": "\n".join(f"- {item['text']}" for item in active)}


@app.exception_handler(Exception)
def on_error(request: Request, exc: Exception):  # pragma: no cover - 仅调试用
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
