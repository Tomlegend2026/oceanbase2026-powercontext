"""ASGI 入口：uvicorn recallcoach.main:app"""

from __future__ import annotations

from .api import create_app

app = create_app()
