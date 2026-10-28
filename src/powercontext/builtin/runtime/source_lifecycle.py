# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ruff: noqa: TRY003

"""Dependency-aware Source lifecycle state for Memory and Topic Memory."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

_SOURCE_KIND = "source"
_MEMORY_KIND = "memory"
_TOPIC_KIND = "topic"
_POLICIES = frozenset({"all", "any"})
_PURPOSES = frozenset({"generation", "audit"})


class SourceLifecycleError(ValueError):
    """A lifecycle graph, event, or native propagation operation is invalid."""


class SourceLifecycleEvent(BaseModel):
    """One caller-supplied Source availability event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(strict=True)
    reason: str = Field(strict=True)
    revision: int = Field(strict=True, gt=0)
    sequence: int = Field(strict=True, gt=0)
    available: bool = Field(strict=True)

    @field_validator("node_id", "reason")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


@dataclass(frozen=True)
class _Binding:
    kind: Literal["source", "memory", "topic"]
    source: SourceRef | None = None
    citation: MemoryCitation | None = None
    artifact: ArtifactRef | None = None

    def to_json(self) -> str:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.source is not None:
            payload["source"] = self.source.model_dump(mode="json")
        if self.citation is not None:
            payload["citation"] = self.citation.model_dump(mode="json")
        if self.artifact is not None:
            payload["artifact"] = self.artifact.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> _Binding:
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as error:
            raise SourceLifecycleError("stored lifecycle binding is invalid") from error
        if not isinstance(payload, dict):
            raise SourceLifecycleError("stored lifecycle binding is invalid")
        kind = payload.get("kind")
        try:
            if kind == _SOURCE_KIND:
                return cls(kind=_SOURCE_KIND, source=SourceRef.model_validate(payload["source"]))
            if kind == _MEMORY_KIND:
                return cls(kind=_MEMORY_KIND, citation=MemoryCitation.model_validate(payload["citation"]))
            if kind == _TOPIC_KIND:
                return cls(kind=_TOPIC_KIND, artifact=ArtifactRef.model_validate(payload["artifact"]))
        except (KeyError, ValueError) as error:
            raise SourceLifecycleError("stored lifecycle binding is invalid") from error
        raise SourceLifecycleError("stored lifecycle binding is invalid")

    @property
    def reference(self) -> SourceRef | MemoryCitation | ArtifactRef:
        if self.source is not None:
            return self.source
        if self.citation is not None:
            return self.citation
        if self.artifact is not None:
            return self.artifact
        raise SourceLifecycleError("stored lifecycle binding has no reference")


@dataclass(frozen=True)
class _Node:
    node_id: str
    kind: Literal["source", "memory", "topic"]
    revision: int | None
    parents: tuple[str, ...]
    policy: str | None
    binding: _Binding
    available: bool
    last_sequence: int
    last_available: bool | None
    last_reason: str | None


class SourceLifecycleApplication:
    """Runtime-owned Source lifecycle registry and topic visibility filter."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleApplication:
        return ScopedSourceLifecycleApplication(self._runtime, self._database, validate_scope_id(scope_id))

    async def hidden_topic_artifact_ids(self, scope_id: str) -> frozenset[str]:
        """Return artifact identities whose registered Topic node is unavailable."""

        if self._database is None:
            return frozenset()
        scope = validate_scope_id(scope_id)
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(table.c.binding, table.c.available).where(
                        table.c.scope_id == scope,
                        table.c.kind == _TOPIC_KIND,
                    )
                )
            ).mappings()
            active: set[str] = set()
            inactive: set[str] = set()
            for row in rows:
                binding = _Binding.from_json(str(row["binding"]))
                if binding.artifact is None:
                    raise SourceLifecycleError("stored Topic lifecycle binding is invalid")
                target = active if bool(row["available"]) else inactive
                target.add(binding.artifact.artifact_id)
        return frozenset(inactive - active)


class ScopedSourceLifecycleApplication:
    """Register lifecycle nodes, apply Source events, and resolve exact references."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = scope_id

    async def register_source(
        self,
        node_id: str,
        source: SourceRef,
        revision: int = 1,
    ) -> None:
        """Register a Source root at one caller-provided lifecycle revision."""

        source_ref = _coerce_source_ref(source)
        revision = _positive_int(revision, "revision")
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            await self._validate_source(source_ref)
            await self._register_node(
                context,
                node_id=node_id,
                binding=_Binding(kind=_SOURCE_KIND, source=source_ref),
                parents=(),
                policy=None,
                revision=revision,
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        """Register a Memory entry as a derived lifecycle node."""

        citation = _coerce_citation(citation)
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            await self._validate_memory(context, citation)
            await self._register_node(
                context,
                node_id=node_id,
                binding=_Binding(kind=_MEMORY_KIND, citation=citation),
                parents=parents,
                policy=policy,
                revision=None,
            )

    async def register_topic(
        self,
        node_id: str,
        reference: ArtifactRef,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        """Register a Topic Memory Artifact as a derived lifecycle node."""

        artifact = _coerce_artifact_ref(reference)
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            await self._validate_topic(artifact)
            await self._register_node(
                context,
                node_id=node_id,
                binding=_Binding(kind=_TOPIC_KIND, artifact=artifact),
                parents=parents,
                policy=policy,
                revision=None,
            )

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:  # noqa: C901 - validated event transaction
        """Apply one Source event and return every currently available node id."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("apply requires a SourceLifecycleEvent")
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            if self._database is None:
                raise SourceLifecycleError("Source lifecycle persistence is unavailable")
            async with self._database.transaction() as connection:
                nodes = await self._load_nodes(connection)
                node = nodes.get(event.node_id)
                if node is None:
                    raise SourceLifecycleError("event references an unknown node")
                if node.kind != _SOURCE_KIND:
                    raise SourceLifecycleError("events can only target registered Sources")
                if node.revision != event.revision:
                    raise SourceLifecycleError("event revision does not match the Source lifecycle revision")
                if event.sequence < node.last_sequence:
                    raise SourceLifecycleError("event sequence is older than the last applied sequence")
                if event.sequence == node.last_sequence:
                    if node.last_available != event.available or node.last_reason != event.reason:
                        raise SourceLifecycleError("event sequence was already used for different content")
                    idempotent = True
                else:
                    idempotent = False

                if not idempotent:
                    nodes[event.node_id] = replace(
                        node,
                        available=event.available,
                        last_sequence=event.sequence,
                        last_available=event.available,
                        last_reason=event.reason,
                    )
                    active = _compute_active(nodes)
                    for node_id in sorted(nodes):
                        nodes[node_id] = replace(nodes[node_id], available=active[node_id])
                    for node_id in sorted(nodes):
                        current = nodes[node_id]
                        values: dict[str, object] = {"available": current.available}
                        if node_id == event.node_id:
                            values.update(
                                last_sequence=current.last_sequence,
                                last_available=current.last_available,
                                last_reason=current.last_reason,
                            )
                        await connection.execute(
                            update(SOURCE_LIFECYCLE_NODES_TABLE)
                            .where(
                                SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                            )
                            .values(**values)
                        )
                else:
                    active = _compute_active(nodes)

            await self._reconcile_memory(context, nodes)
            return sorted(node_id for node_id, available in active.items() if available)

    async def resolve(
        self,
        node_id: str,
        purpose: str,
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the registered reference for generation or audit use."""

        node_id = _validate_node_id(node_id)
        if purpose not in _PURPOSES:
            raise SourceLifecycleError("purpose must be generation or audit")
        if self._database is None:
            raise SourceLifecycleError("Source lifecycle persistence is unavailable")
        async with self._database.transaction() as connection:
            nodes = await self._load_nodes(connection)
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown lifecycle node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("node is not available for generation")
        return node.binding.reference

    async def _register_node(
        self,
        context: Any,
        *,
        node_id: str,
        binding: _Binding,
        parents: tuple[str, ...],
        policy: str | None,
        revision: int | None,
    ) -> None:
        node_id = _validate_node_id(node_id)
        parents = _validate_parents(parents, binding.kind == _SOURCE_KIND, node_id)
        if binding.kind != _SOURCE_KIND:
            if policy not in _POLICIES:
                raise SourceLifecycleError("derived node policy must be all or any")
        elif policy is not None:
            raise SourceLifecycleError("Source nodes do not have a dependency policy")
        if self._database is None:
            raise SourceLifecycleError("Source lifecycle persistence is unavailable")

        async with self._database.transaction() as connection:
            nodes = await self._load_nodes(connection)
            existing = nodes.get(node_id)
            if existing is not None:
                if not _same_registration(existing, binding, parents, policy, revision):
                    raise SourceLifecycleError("node id is already bound to a different lifecycle node")
            else:
                for parent in parents:
                    if parent not in nodes:
                        raise SourceLifecycleError("parent node must be registered before its child")
                    if parent == node_id or _reaches(nodes, parent, node_id):
                        raise SourceLifecycleError("lifecycle dependencies must be acyclic")

                available = _node_available(nodes, binding.kind, parents, policy)
                new_node = _Node(
                    node_id=node_id,
                    kind=binding.kind,
                    revision=revision,
                    parents=parents,
                    policy=policy,
                    binding=binding,
                    available=available,
                    last_sequence=0,
                    last_available=None,
                    last_reason=None,
                )
                await connection.execute(
                    insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                        scope_id=self.scope_id,
                        node_id=node_id,
                        kind=binding.kind,
                        revision=revision,
                        parents=json.dumps(parents, separators=(",", ":")),
                        policy=policy,
                        binding=binding.to_json(),
                        available=available,
                        last_sequence=0,
                        last_available=None,
                        last_reason=None,
                    )
                )
                nodes[node_id] = new_node

        await self._reconcile_memory(context, nodes)

    async def _load_nodes(self, connection: AsyncConnection) -> dict[str, _Node]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (await connection.execute(select(table).where(table.c.scope_id == self.scope_id))).mappings()
        nodes: dict[str, _Node] = {}
        for row in rows:
            try:
                parents = tuple(json.loads(str(row["parents"])))
            except (TypeError, ValueError) as error:
                raise SourceLifecycleError("stored lifecycle parents are invalid") from error
            if not all(isinstance(parent, str) for parent in parents):
                raise SourceLifecycleError("stored lifecycle parents are invalid")
            nodes[str(row["node_id"])] = _Node(
                node_id=str(row["node_id"]),
                kind=str(row["kind"]),  # type: ignore[arg-type]
                revision=None if row["revision"] is None else int(row["revision"]),
                parents=parents,
                policy=None if row["policy"] is None else str(row["policy"]),
                binding=_Binding.from_json(str(row["binding"])),
                available=bool(row["available"]),
                last_sequence=int(row["last_sequence"]),
                last_available=None if row["last_available"] is None else bool(row["last_available"]),
                last_reason=None if row["last_reason"] is None else str(row["last_reason"]),
            )
        return nodes

    async def _validate_source(self, source: SourceRef) -> None:
        try:
            await self._runtime._records().get_source(self.scope_id, source.source_type, source.source_id)
        except Exception as error:
            raise SourceLifecycleError("Source reference is not readable") from error

    async def _validate_memory(self, context: Any, citation: MemoryCitation) -> None:
        try:
            memory = await context.artifacts.memory.revision(citation.memory_ref)
            entries = await context.artifacts.memory.entries(memory)
        except Exception as error:
            raise SourceLifecycleError("Memory citation is not readable") from error
        if not any(
            entry.entry_id == citation.entry_id and entry.entry_version_id == citation.entry_version_id
            for entry in entries
        ):
            raise SourceLifecycleError("Memory citation does not address the registered entry version")

    async def _validate_topic(self, reference: ArtifactRef) -> None:
        if reference.family != "topic-memory":
            raise SourceLifecycleError("Topic lifecycle reference must be a Topic Memory Artifact")
        try:
            await self._runtime._records().get_artifact_revision(
                self.scope_id,
                "topic-memory",
                reference.artifact_id,
                reference.revision,
            )
        except Exception as error:
            raise SourceLifecycleError("Topic Memory Artifact reference is not readable") from error

    async def _reconcile_memory(self, context: Any, nodes: dict[str, _Node]) -> None:
        memory_nodes = sorted(
            (node for node in nodes.values() if node.kind == _MEMORY_KIND),
            key=lambda node: node.node_id,
        )
        service = context.artifacts.memory
        for node in memory_nodes:
            citation = node.binding.citation
            if citation is None:
                raise SourceLifecycleError("Memory lifecycle node has no citation")
            try:
                memory, entries = await service.head_entries(citation.memory_ref.artifact_id)
            except Exception as error:
                raise SourceLifecycleError("registered Memory is not readable") from error
            entry = next(
                (
                    candidate
                    for candidate in entries
                    if candidate.entry_id == citation.entry_id
                    and candidate.entry_version_id == citation.entry_version_id
                ),
                None,
            )
            if entry is None:
                raise SourceLifecycleError("registered Memory entry version is no longer current")
            manifest_entry = next(
                (item for item in memory.content.manifest.entries if item.entry_id == citation.entry_id),
                None,
            )
            if manifest_entry is None:
                raise SourceLifecycleError("registered Memory entry is missing from the current head")
            active = manifest_entry.state == "active"
            if node.available and not active:
                await service.reactivate(memory, entries=(entry,), reason="source lifecycle")
            elif not node.available and active:
                await service.forget(memory, entries=(entry,), reason="source lifecycle")


def _node_available(
    nodes: dict[str, _Node],
    kind: str,
    parents: tuple[str, ...],
    policy: str | None,
) -> bool:
    if kind == _SOURCE_KIND:
        return True
    support = [nodes[parent].available for parent in parents]
    return all(support) if policy == "all" else any(support)


def _compute_active(nodes: dict[str, _Node]) -> dict[str, bool]:
    memo: dict[str, bool] = {}
    visiting: set[str] = set()

    def active(node_id: str) -> bool:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            raise SourceLifecycleError("lifecycle dependencies must be acyclic")
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("lifecycle dependency is missing")
        visiting.add(node_id)
        try:
            if node.kind == _SOURCE_KIND:
                result = node.available
            else:
                if node.policy not in _POLICIES:
                    raise SourceLifecycleError("derived node policy must be all or any")
                support = [active(parent) for parent in node.parents]
                result = all(support) if node.policy == "all" else any(support)
            memo[node_id] = result
            return result
        finally:
            visiting.remove(node_id)

    for node_id in sorted(nodes):
        active(node_id)
    return memo


def _same_registration(
    existing: _Node,
    binding: _Binding,
    parents: tuple[str, ...],
    policy: str | None,
    revision: int | None,
) -> bool:
    return (
        existing.binding == binding
        and existing.parents == parents
        and existing.policy == policy
        and existing.revision == revision
    )


def _reaches(nodes: dict[str, _Node], start: str, target: str) -> bool:
    stack = [start]
    seen: set[str] = set()
    while stack:
        node_id = stack.pop()
        if node_id == target:
            return True
        if node_id in seen:
            continue
        seen.add(node_id)
        node = nodes.get(node_id)
        if node is not None:
            stack.extend(node.parents)
    return False


def _validate_node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("node_id must not be blank")
    if len(value) > 256:
        raise SourceLifecycleError("node_id is too long")
    return value


def _validate_parents(parents: tuple[str, ...], is_source: bool, node_id: str) -> tuple[str, ...]:
    if isinstance(parents, str):
        raise SourceLifecycleError("parents must be a sequence of node ids")
    try:
        normalized = tuple(parents)
    except TypeError as error:
        raise SourceLifecycleError("parents must be a sequence of node ids") from error
    if not all(isinstance(parent, str) and parent.strip() for parent in normalized):
        raise SourceLifecycleError("parents must contain non-blank node ids")
    if len(set(normalized)) != len(normalized):
        raise SourceLifecycleError("parents must not repeat")
    normalized = tuple(sorted(normalized))
    if is_source:
        if normalized:
            raise SourceLifecycleError("Source nodes cannot have parents")
    elif not normalized:
        raise SourceLifecycleError("derived nodes require at least one parent")
    if node_id in normalized:
        raise SourceLifecycleError("a node cannot depend on itself")
    return normalized


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise SourceLifecycleError(f"{field} must be a strictly positive integer")
    return value


def _coerce_source_ref(value: Any) -> SourceRef:
    if isinstance(value, SourceRef):
        return value
    try:
        return SourceRef.model_validate(value)
    except ValueError as error:
        raise SourceLifecycleError("source must be a SourceRef") from error


def _coerce_citation(value: Any) -> MemoryCitation:
    if isinstance(value, MemoryCitation):
        return value
    try:
        return MemoryCitation.model_validate(value)
    except ValueError as error:
        raise SourceLifecycleError("memory must be a MemoryCitation") from error


def _coerce_artifact_ref(value: Any) -> ArtifactRef:
    if isinstance(value, ArtifactRef):
        return value
    try:
        return ArtifactRef.model_validate(value)
    except ValueError as error:
        raise SourceLifecycleError("reference must be an ArtifactRef") from error


__all__ = ["SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
