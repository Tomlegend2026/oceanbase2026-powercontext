# ruff: noqa: TRY003
"""Incremental Context Pack wire protocol.

A full pack carries the exact bytes of one prepared context. A delta retains a
prefix of the receiver's previous pack and appends the remaining target chunks,
so the receiver can rebuild the target from serialized data alone.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(Exception):
    """A pack, delta, or chunk boundary violates the wire contract."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_items(items: Iterable[bytes]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_item(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ContextPackError("Base64 item must be a string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ContextPackError("Invalid Base64 item") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ContextPackError("Base64 item is not canonical")
    return decoded


def _normalize_items(items: Iterable[bytes]) -> tuple[bytes, ...]:
    try:
        values = tuple(items)
    except TypeError as error:
        raise ContextPackError("Items must be an iterable of bytes") from error
    normalized: list[bytes] = []
    for value in values:
        if isinstance(value, str) or not isinstance(value, bytes | bytearray | memoryview):
            raise ContextPackError("Items must be bytes")
        normalized.append(bytes(value))
    return tuple(normalized)


def _pack_digest(wire_items: list[str]) -> str:
    unsigned = _canonical_json({"schema": PACK_SCHEMA, "items": wire_items})
    return "sha256:" + hashlib.sha256(unsigned).hexdigest()


def _as_bytes(data: Any, label: str) -> bytes:
    if isinstance(data, str) or not isinstance(data, bytes | bytearray | memoryview):
        raise ContextPackError(f"{label} must be bytes")
    return bytes(data)


def _chunk_text(text: str, limit: int) -> tuple[bytes, ...]:
    """Split text at line boundaries, then at UTF-8 character boundaries.

    Keeping complete lines as their own chunks preserves a useful common prefix
    when a later prepared context appends entries. A line that exceeds the limit
    is split without breaking a character, so every returned chunk is valid
    UTF-8 and no longer than ``limit``.
    """

    chunks: list[bytes] = []
    for line in text.splitlines(keepends=True):
        encoded = line.encode("utf-8")
        if len(encoded) <= limit:
            chunks.append(encoded)
            continue

        current = bytearray()
        for character in line:
            piece = character.encode("utf-8")
            if len(piece) > limit:
                raise ContextPackError("A character does not fit inside chunk_bytes")
            if len(current) + len(piece) > limit:
                chunks.append(bytes(current))
                current.clear()
            current.extend(piece)
        if current:
            chunks.append(bytes(current))
    return tuple(chunks)


@dataclass(frozen=True)
class ContextPack:
    """A complete, checksummed sequence of context chunks."""

    schema: str
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", _normalize_items(self.items))
        if not isinstance(self.schema, str):
            raise ContextPackError("schema must be a string")
        if not isinstance(self.digest, str):
            raise ContextPackError("digest must be a string")
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise ContextPackError("byte_count must be a non-negative integer")

    @classmethod
    def build(cls, items: Iterable[bytes]) -> ContextPack:
        normalized = _normalize_items(items)
        digest = _pack_digest(_encode_items(normalized))
        pack = cls(PACK_SCHEMA, normalized, digest, 0)
        while True:
            byte_count = len(pack.to_bytes())
            if byte_count == pack.byte_count:
                return pack
            pack = cls(PACK_SCHEMA, normalized, digest, byte_count)

    @classmethod
    def from_content(cls, content: str | bytes | None, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a strictly positive integer")
        if content is None:
            return cls.build(())
        if isinstance(content, str):
            try:
                data = content.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ContextPackError("content is not encodable as UTF-8") from error
        elif isinstance(content, bytes | bytearray | memoryview):
            data = bytes(content)
        else:
            raise ContextPackError("content must be str, bytes, or None")
        if not data:
            return cls.build(())
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("content is not valid UTF-8") from error
        return cls.build(_chunk_text(text, chunk_bytes))

    def to_bytes(self) -> bytes:
        return _canonical_json({
            "schema": self.schema,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        raw = _as_bytes(data, "Pack")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            raise ContextPackError("Invalid pack JSON") from error
        if not isinstance(payload, dict):
            raise ContextPackError("Pack must be a JSON object")
        if set(payload) != _PACK_FIELDS:
            raise ContextPackError("Pack fields do not match the wire contract")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("Invalid pack schema")
        wire_items = payload["items"]
        if not isinstance(wire_items, list):
            raise ContextPackError("items must be an array")
        items = tuple(_decode_item(value) for value in wire_items)
        expected_digest = _pack_digest(wire_items)
        if payload["digest"] != expected_digest:
            raise ContextPackError("Pack digest mismatch")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count < 0 or byte_count != len(raw):
            raise ContextPackError("Pack byte_count mismatch")
        pack = cls(PACK_SCHEMA, items, expected_digest, byte_count)
        if len(pack.to_bytes()) != byte_count:
            raise ContextPackError("Pack is not canonically encoded")
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    """A retained base prefix plus the chunks a receiver must append."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]
    schema: ClassVar[str] = DELTA_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.base_digest, str) or not isinstance(self.target_digest, str):
            raise ContextPackError("Delta digests must be strings")
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        object.__setattr__(self, "appended", _normalize_items(self.appended))

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("between requires a base and target ContextPack")
        prefix_items = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix_items += 1
        return cls(base.digest, target.digest, prefix_items, target.items[prefix_items:])

    def to_bytes(self) -> bytes:
        return _canonical_json({
            "schema": self.schema,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        raw = _as_bytes(data, "Delta")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            raise ContextPackError("Invalid delta JSON") from error
        if not isinstance(payload, dict):
            raise ContextPackError("Delta must be a JSON object")
        if set(payload) != _DELTA_FIELDS:
            raise ContextPackError("Delta fields do not match the wire contract")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("Invalid delta schema")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("Delta digests must be strings")
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        wire_appended = payload["appended"]
        if not isinstance(wire_appended, list):
            raise ContextPackError("appended must be an array")
        appended = tuple(_decode_item(value) for value in wire_appended)
        return cls(base_digest, target_digest, prefix_items, appended)


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate a delta against a base pack and return the rebuilt target."""

    if not isinstance(base, ContextPack):
        raise ContextPackError("base must be a ContextPack")
    if not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta must be a ContextPackDelta")
    if base.digest != delta.base_digest:
        raise ContextPackError("Base digest mismatch")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0:
        raise ContextPackError("prefix_items must be a non-negative integer")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix_items exceeds the base chunk count")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("Target digest mismatch")
    return target


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
