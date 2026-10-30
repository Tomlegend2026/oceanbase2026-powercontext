"""配置读取：优先 .env，环境变量覆盖。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = APP_DIR.parent


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    base_url: str
    token: str
    scope_id: str
    scope_title: str
    scope_key: str
    db_path: Path
    audit_path: Path
    today_override: date | None
    personalization: bool
    port: int

    def today(self) -> date:
        return self.today_override or date.today()


def load_settings() -> Settings:
    values = read_env_file(APP_DIR / ".env")
    for key, value in os.environ.items():
        if key.startswith(("POWERCONTEXT_", "RECALLCOACH_", "APP_")):
            values[key] = value

    def as_path(key: str, default: str) -> Path:
        candidate = Path(values.get(key, default))
        return candidate if candidate.is_absolute() else (APP_DIR / candidate).resolve()

    today_raw = values.get("RECALLCOACH_TODAY", "").strip()
    return Settings(
        base_url=values.get("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        token=values.get("POWERCONTEXT_TOKEN", "").strip(),
        scope_id=values.get("POWERCONTEXT_SCOPE_ID", "").strip(),
        scope_title=values.get("RECALLCOACH_SCOPE_TITLE", "RecallCoach · 数据库系统"),
        scope_key=values.get("RECALLCOACH_SCOPE_KEY", "recallcoach-db"),
        db_path=as_path("RECALLCOACH_DB", ".runtime/app.db"),
        audit_path=as_path("RECALLCOACH_AUDIT", ".runtime/memory_audit.jsonl"),
        today_override=date.fromisoformat(today_raw) if today_raw else None,
        personalization=values.get("RECALLCOACH_PERSONALIZATION", "on").strip().lower() != "off",
        port=int(values.get("APP_PORT", "8080")),
    )
