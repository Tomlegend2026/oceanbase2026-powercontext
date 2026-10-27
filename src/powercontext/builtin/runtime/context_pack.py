import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Sequence, Optional, Any

class ContextPackError(Exception):
    """协议或分块校验错误"""
    pass

@dataclass(frozen=True)
class ContextPack:
    schema: str
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: Sequence[bytes]) -> "ContextPack":
        # 构造仅包含 schema 和 items 的 payload 用于计算摘要
        b64_items = [base64.b64encode(item).decode('utf-8') for item in items]
        payload_for_digest = {
            "schema": "powercontext.context-pack.v1",
            "items": b64_items
        }
        digest = cls._calculate_digest(payload_for_digest)
        
        # 先构造一个占位的 byte_count
        temp_pack = cls(
            schema="powercontext.context-pack.v1",
            items=tuple(items),
            digest=digest,
            byte_count=0
        )
        # 算出真实 byte_count
        byte_count = len(temp_pack.to_bytes())
        
        return cls(
            schema="powercontext.context-pack.v1",
            items=tuple(items),
            digest=digest,
            byte_count=byte_count
        )

    @classmethod
    def from_content(cls, content: Optional[Any], *, chunk_bytes: int = 512) -> "ContextPack":
        if content is None:
            return cls.build([])
        
        # 兼容传入字符串或字节
        if isinstance(content, str):
            content_bytes = content.encode('utf-8')
        elif isinstance(content, bytes):
            content_bytes = content
        else:
            # 如果是其他对象，尝试转成字节
            content_bytes = str(content).encode('utf-8')

        if not content_bytes:
            return cls.build([])

        items = []
        current_chunk = bytearray()

        # 简单的按行或按字符边界分块，保证不超过 chunk_bytes
        for char in content_bytes.decode('utf-8'):
            char_bytes = char.encode('utf-8')
            if len(current_chunk) + len(char_bytes) > chunk_bytes:
                if not current_chunk:
                    raise ContextPackError("Cannot form a valid chunk within the limit")
                items.append(bytes(current_chunk))
                current_chunk = bytearray()
            current_chunk.extend(char_bytes)
        
        if current_chunk:
            items.append(bytes(current_chunk))

        return cls.build(items)

    def to_bytes(self) -> bytes:
        b64_items = [base64.b64encode(item).decode('utf-8') for item in self.items]
        payload = {
            "schema": self.schema,
            "items": b64_items,
            "digest": self.digest,
            "byte_count": self.byte_count
        }
        return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_bytes(cls, data: bytes) -> "ContextPack":
        try:
            payload = json.loads(data.decode('utf-8'))
        except Exception as e:
            raise ContextPackError(f"Invalid JSON: {e}")

        required_keys = {"schema", "items", "digest", "byte_count"}
        if set(payload.keys()) != required_keys:
            raise ContextPackError("Invalid fields in Pack")
        
        if payload["schema"] != "powercontext.context-pack.v1":
            raise ContextPackError("Invalid schema")
        
        if not isinstance(payload["items"], list):
            raise ContextPackError("Items must be a list")
        
        items = []
        for b64_str in payload["items"]:
            if not isinstance(b64_str, str):
                raise ContextPackError("Item must be string")
            try:
                items.append(base64.b64decode(b64_str))
            except Exception:
                raise ContextPackError("Invalid Base64")

        # 校验摘要
        payload_for_digest = {
            "schema": payload["schema"],
            "items": payload["items"]
        }
        expected_digest = cls._calculate_digest(payload_for_digest)
        if payload["digest"] != expected_digest:
            raise ContextPackError("Digest mismatch")

        # 校验 byte_count
        if payload["byte_count"] != len(data):
            raise ContextPackError("byte_count mismatch")

        return cls(
            schema=payload["schema"],
            items=tuple(items),
            digest=payload["digest"],
            byte_count=payload["byte_count"]
        )

    @staticmethod
    def _calculate_digest(payload: dict) -> str:
        json_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return "sha256:" + hashlib.sha256(json_str.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ContextPackDelta:
    schema: str
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]
    byte_count: int

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> "ContextPackDelta":
        # 寻找公共前缀
        prefix_len = 0
        for i in range(min(len(base.items), len(target.items))):
            if base.items[i] == target.items[i]:
                prefix_len += 1
            else:
                break
        
        appended = target.items[prefix_len:]
        
        temp_delta = cls(
            schema="powercontext.context-pack-delta.v1",
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_len,
            appended=appended,
            byte_count=0
        )
        byte_count = len(temp_delta.to_bytes())
        
        return cls(
            schema="powercontext.context-pack-delta.v1",
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_len,
            appended=appended,
            byte_count=byte_count
        )

    def to_bytes(self) -> bytes:
        b64_appended = [base64.b64encode(item).decode('utf-8') for item in self.appended]
        payload = {
            "schema": self.schema,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": b64_appended
        }
        return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_bytes(cls, data: bytes) -> "ContextPackDelta":
        try:
            payload = json.loads(data.decode('utf-8'))
        except Exception as e:
            raise ContextPackError(f"Invalid JSON: {e}")

        required_keys = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        if set(payload.keys()) != required_keys:
            raise ContextPackError("Invalid fields in Delta")
        
        if payload["schema"] != "powercontext.context-pack-delta.v1":
            raise ContextPackError("Invalid schema")
        
        if not isinstance(payload["prefix_items"], int) or isinstance(payload["prefix_items"], bool) or payload["prefix_items"] < 0:
            raise ContextPackError("Invalid prefix_items")

        if not isinstance(payload["appended"], list):
            raise ContextPackError("Appended must be a list")

        appended = []
        for b64_str in payload["appended"]:
            if not isinstance(b64_str, str):
                raise ContextPackError("Appended item must be string")
            try:
                appended.append(base64.b64decode(b64_str))
            except Exception:
                raise ContextPackError("Invalid Base64")

        temp_delta = cls(
            schema=payload["schema"],
            base_digest=payload["base_digest"],
            target_digest=payload["target_digest"],
            prefix_items=payload["prefix_items"],
            appended=tuple(appended),
            byte_count=len(data)
        )
        
        return temp_delta

def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if base.digest != delta.base_digest:
        raise ContextPackError("Base digest mismatch")
    
    if delta.prefix_items > len(base.items):
        raise ContextPackError("Prefix items out of bounds")
        
    new_items = list(base.items[:delta.prefix_items])
    new_items.extend(delta.appended)
    
    target_pack = ContextPack.build(new_items)
    
    if target_pack.digest != delta.target_digest:
        raise ContextPackError("Target digest mismatch")
        
    return target_pack