"""PowerContext HTTP 网关 + 审计日志。

只调用文档中的公开接口；响应字段做防御式解析，避免版本漂移直接打断业务。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from . import audit as auditlog

REQUEST_ID_HEADER = "X-PowerContext-Request-ID"


class PowerContextError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _scrub(value: Any, *, depth: int = 0) -> Any:
    """截断过长字符串并去掉常见凭据形态，避免审计日志泄漏。"""
    if depth > 8:
        return "…"
    if isinstance(value, str):
        text = value.replace("\n", " ")
        for marker in ("Bearer ",):
            if marker in text:
                text = text.replace(marker, "Bearer ***")
        return text if len(text) <= 400 else text[:399] + "…"
    if isinstance(value, dict):
        return {
            key: ("***" if "token" in key.lower() or "authorization" in key.lower() else _scrub(item, depth=depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, depth=depth + 1) for item in value[:20]]
    return value


class MemoryGateway:
    """同步 HTTP 客户端；每次调用写一行审计日志。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        audit_path: Path,
        *,
        timeout: float = 10.0,
        personalization: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.audit_path = audit_path
        self.personalization = personalization
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # ---- 审计 ----

    def _audit(self, record: dict[str, Any]) -> None:
        auditlog.append(self.audit_path, record)

    def _log(
        self,
        *,
        op: str,
        scope_id: str | None,
        request: dict[str, Any],
        response: dict[str, Any],
        request_id: str | None,
        latency_ms: int,
        status: str,
        error: str | None = None,
    ) -> None:
        self._audit(
            {
                "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                "op": op,
                "scope_id": scope_id,
                "personalization": self.personalization,
                "request": _scrub(request),
                "response": _scrub(response),
                "request_id": request_id,
                "latency_ms": latency_ms,
                "status": status,
                "error": error,
            }
        )

    # ---- 底层请求 ----

    def _call(
        self,
        op: str,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope_id = (body or {}).get("scope_id") or (params or {}).get("scope_id")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        started = time.perf_counter()
        request_id: str | None = None
        try:
            response = self._client.request(method, path, json=body, params=params, headers=headers)
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log(
                op=op,
                scope_id=scope_id,
                request={"method": method, "path": path, "body": body, "params": params},
                response={},
                request_id=None,
                latency_ms=elapsed,
                status="error",
                error=str(exc),
            )
            raise PowerContextError(f"PowerContext 请求失败：{exc}") from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        request_id = response.headers.get(REQUEST_ID_HEADER)
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {"raw": response.text[:400]}

        if not response.is_success:
            self._log(
                op=op,
                scope_id=scope_id,
                request={"method": method, "path": path, "body": body, "params": params},
                response=payload,
                request_id=request_id,
                latency_ms=elapsed,
                status="error",
                error=f"HTTP {response.status_code}",
            )
            raise PowerContextError(
                f"PowerContext {path} 返回 HTTP {response.status_code}",
                status=response.status_code,
                body=payload,
            )

        self._log(
            op=op,
            scope_id=scope_id,
            request={"method": method, "path": path, "body": body, "params": params},
            response=payload,
            request_id=request_id,
            latency_ms=elapsed,
            status="ok",
        )
        return payload

    # ---- 接口 ----

    def live(self) -> bool:
        try:
            response = self._client.get("/health/live", timeout=1.5)
            return response.is_success
        except httpx.HTTPError:
            return False

    def capabilities(self) -> dict[str, Any]:
        return self._call("capabilities", "GET", "/v1/capabilities")

    def ensure_scope(self, title: str, summary: str, idempotency_key: str) -> str:
        payload = self._call(
            "scopes.create",
            "POST",
            "/v1/scopes",
            body={"title": title, "summary": summary, "idempotency_key": idempotency_key},
        )
        scope = payload.get("scope") or {}
        scope_id = payload.get("scope_id") or scope.get("scope_id") or payload.get("id")
        if not scope_id:
            raise PowerContextError("创建 scope 的响应里没有 scope_id", body=payload)
        return str(scope_id)

    def remember(self, scope_id: str, *, kind: str, text: str, reason: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            body["reason"] = reason
        payload = self._call("memory.remember", "POST", "/v1/memory/remember", body=body)
        entry = payload.get("entry") or {}
        citation = entry.get("citation") or payload.get("citation") or {}
        entry_id = citation.get("entry_id") or entry.get("entry_id")
        return {
            "entry_id": entry_id,
            "citation": citation,
            "version": entry.get("version"),
            "raw": payload,
        }

    def search(self, scope_id: str, query: str, *, limit: int = 20, mode: str = "auto") -> dict[str, Any]:
        payload = self._call(
            "memory.search",
            "POST",
            "/v1/memory/search",
            body={"scope_id": scope_id, "query": query, "limit": limit, "mode": mode},
        )
        return {"mode": payload.get("mode"), "hits": payload.get("hits") or []}

    def entries(self, scope_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        payload = self._call(
            "memory.entries.list",
            "POST",
            "/v1/memory/entries/list",
            body={"scope_id": scope_id, "include_inactive": include_inactive},
        )
        items = payload.get("entries") or payload.get("items") or payload.get("data") or []
        return [self._normalize_entry(item) for item in items]

    @staticmethod
    def _normalize_entry(item: dict[str, Any]) -> dict[str, Any]:
        citation = item.get("citation") or {}
        return {
            "entry_id": citation.get("entry_id") or item.get("entry_id"),
            "kind": item.get("kind"),
            "text": item.get("text"),
            "version": item.get("version"),
            "state": item.get("state"),
            "citation": citation,
        }

    def prepare(self, scope_id: str, query: str, *, max_bytes: int = 2000) -> dict[str, Any]:
        body = {
            "scope_id": scope_id,
            "query": query,
            "max_bytes": max_bytes,
            "assembly": {"sections": [{"family": "memory", "limit": 4}], "show": ["recall_rank"]},
        }
        try:
            payload = self._call("context.prepare", "POST", "/v1/context/prepare", body=body)
        except PowerContextError as exc:
            if exc.status != 422:
                raise
            payload = self._call(
                "context.prepare",
                "POST",
                "/v1/context/prepare",
                body={"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
            )
        return {"status": payload.get("status"), "content": payload.get("content")}

    def revise(self, scope_id: str, citation: dict[str, Any], *, kind: str, text: str) -> dict[str, Any]:
        payload = self._call(
            "memory.entries.revise",
            "POST",
            "/v1/memory/entries/revise",
            body={"scope_id": scope_id, "citation": citation, "kind": kind, "text": text},
        )
        entry = payload.get("entry") or {}
        return {"entry": entry, "raw": payload}

    def retire(self, scope_id: str, citation: dict[str, Any], *, reason: str = "已掌握") -> dict[str, Any]:
        return self._call(
            "memory.entries.retire",
            "POST",
            "/v1/memory/entries/retire",
            body={"scope_id": scope_id, "citation": citation, "reason": reason},
        )
