"""Amnesia Detective: a tiny HTTP face over PowerContext memory.

Dify calls these two tools; PowerContext owns the long-term case memory.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel, Field

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

DATABASE_URL = os.environ.get("AMNESIA_DB", "sqlite+aiosqlite:///./amnesia.db")
CONFIG = BuiltinConfig(database=SQLiteConfig(url=DATABASE_URL))

app = FastAPI(
    title="Amnesia Detective",
    version="1.0.0",
    description="Case memory for a detective who forgets everything between calls.",
)


class RememberRequest(BaseModel):
    case_id: str = Field(description="Case the clue belongs to, e.g. 'the-red-lantern'.")
    text: str = Field(description="One clue or line of testimony, in Chinese or English.")


class RememberResponse(BaseModel):
    entry_id: str
    case_id: str


class RecallRequest(BaseModel):
    case_id: str
    query: str = Field(description="What the detective is trying to remember.")
    limit: int = Field(default=5, ge=1, le=20)


class RecallResponse(BaseModel):
    clues: list[str]
    count: int


async def _scope(runtime, case_id: str) -> str:
    scope = await runtime.scopes.create(
        ScopeDraft(title=f"case:{case_id}", summary="case file", idempotency_key=f"case-{case_id}")
    )
    return scope.scope_id


@app.post("/remember", response_model=RememberResponse, summary="File one clue into the case memory")
async def remember(request: RememberRequest) -> RememberResponse:
    async with open_builtin_runtime(CONFIG) as runtime:
        scope_id = await _scope(runtime, request.case_id)
        result = await runtime.memory.for_scope(scope_id).remember(
            RememberMemoryRequest(entries=(MemoryEntryInput(kind="clue", text=request.text),))
        )
    return RememberResponse(entry_id=result.entry.entry.entry_id, case_id=request.case_id)


@app.post("/recall", response_model=RecallResponse, summary="Recall clues that match what the detective is thinking about")
async def recall(request: RecallRequest) -> RecallResponse:
    async with open_builtin_runtime(CONFIG) as runtime:
        scope_id = await _scope(runtime, request.case_id)
        page = await runtime.memory.for_scope(scope_id).search(
            SearchMemoryRequest(query=request.query, mode="fts", limit=request.limit)
        )
    clues = [hit.text for hit in page.hits]
    return RecallResponse(clues=clues, count=len(clues))


@app.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}
