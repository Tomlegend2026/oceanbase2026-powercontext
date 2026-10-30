"""FastAPI 路由与静态页面。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .service import Service

WEB_DIR = Path(__file__).parent / "web"


class AnswerIn(BaseModel):
    card_id: str
    correct: bool
    confidence: int = Field(default=3, ge=1, le=5)
    cause: str | None = None
    latency_ms: int = 0
    session_id: str | None = None


class EndSessionIn(BaseModel):
    session_id: str


class ReviseIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class RetireIn(BaseModel):
    reason: str | None = None


def create_app(service: Service | None = None) -> FastAPI:
    app = FastAPI(title="RecallCoach", version="0.1.0")
    app.state.service = service

    def svc() -> Service:
        if app.state.service is None:
            app.state.service = Service.bootstrap()
        return app.state.service

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return svc().health()

    @app.get("/api/today")
    def today(personalization: str | None = None) -> dict[str, Any]:
        enabled: bool | None = None
        if personalization in {"on", "off"}:
            enabled = personalization == "on"
        return svc().build_today(enabled)

    @app.post("/api/answer")
    def answer(payload: AnswerIn) -> dict[str, Any]:
        result = svc().submit_answer(
            card_id=payload.card_id,
            correct=payload.correct,
            confidence=payload.confidence,
            cause=payload.cause,
            latency_ms=payload.latency_ms,
            session_id=payload.session_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="卡片不存在")
        return result

    @app.post("/api/session/end")
    def end_session(payload: EndSessionIn) -> dict[str, Any]:
        return svc().end_session(payload.session_id)

    @app.get("/api/replay")
    def replay(kind: str | None = None) -> dict[str, Any]:
        return svc().replay(kind)

    @app.post("/api/memory/{entry_id}/revise")
    def revise(entry_id: str, payload: ReviseIn) -> dict[str, Any]:
        return svc().revise_memory(entry_id, payload.text)

    @app.post("/api/memory/{entry_id}/retire")
    def retire(entry_id: str, payload: RetireIn | None = None) -> dict[str, Any]:
        return svc().retire_memory(entry_id)

    @app.get("/api/compare")
    def compare(limit: int = 5) -> dict[str, Any]:
        return svc().compare(limit)

    @app.get("/api/evidence")
    def evidence(limit: int = 50) -> dict[str, Any]:
        return svc().evidence(limit)

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
