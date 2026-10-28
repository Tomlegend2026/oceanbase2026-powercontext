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

"""Cross-call embedding coalescing with a bounded successful-vector cache."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass
class _TextWork:
    """One text's shared completion and the batch that owns it."""

    text: str
    future: asyncio.Future[tuple[float, ...]]
    batch: _Batch | None = None


@dataclass
class _Batch:
    """One underlying model invocation and its waiters."""

    texts: tuple[str, ...]
    works: tuple[_TextWork, ...]
    usage: InferenceUsage | None = None


@dataclass
class _State:
    """Mutable scheduler state kept separate from the public constructor."""

    pending: dict[str, _TextWork] = field(default_factory=dict)
    inflight: dict[str, _TextWork] = field(default_factory=dict)
    dispatch_task: asyncio.Task[None] | None = None
    background: set[asyncio.Task[None]] = field(default_factory=set)
    cache: OrderedDict[str, tuple[float, ...]] = field(default_factory=OrderedDict)


class CoalescingEmbeddingModel:
    """Share embedding work across concurrent calls and cache successes.

    Texts requested during one coalescing window are deduplicated and split
    into provider-sized batches. A later caller joins a text that is already
    in flight instead of issuing the same work again. Callers wait for their
    own texts, so cancellation is isolated from the underlying batch task.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a strictly positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, int | float):
            raise ValueError("coalesce_ms must be a non-negative finite number")  # noqa: TRY004
        try:
            delay_ms = float(coalesce_ms)
        except OverflowError as error:
            raise ValueError("coalesce_ms must be a non-negative finite number") from error
        if not math.isfinite(delay_ms) or delay_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative finite number")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")

        self.model = model
        self.profile = model.profile
        self.max_batch_size = max_batch_size
        self.coalesce_ms = delay_ms
        self.cache_size = cache_size
        self._state = _State()

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:  # noqa: C901 - one bounded selection pass
        """Return one ordered vector per input text, preserving duplicates."""

        inputs = tuple(texts)
        if not inputs:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        values: dict[str, tuple[float, ...] | _TextWork] = {}
        for text in inputs:
            if text in values:
                continue
            cached = self._cache_get(text)
            if cached is not None:
                values[text] = cached
                continue
            work = self._state.inflight.get(text)
            if work is None:
                work = self._state.pending.get(text)
                if work is None:
                    work = self._new_work(text)
                    self._state.pending[text] = work
            values[text] = work

        if self._state.pending:
            self._ensure_dispatch()

        works: dict[int, _TextWork] = {}
        for value in values.values():
            if isinstance(value, _TextWork):
                works[id(value)] = value
        waited = tuple(works.values())

        if waited:
            # A caller's cancellation must not cancel a batch shared with other
            # callers; shield each shared completion instead of gathering the
            # underlying work directly.
            await asyncio.gather(*(asyncio.shield(work.future) for work in waited))

        vectors: list[tuple[float, ...]] = []
        for text in inputs:
            value = values[text]
            vectors.append(value.future.result() if isinstance(value, _TextWork) else value)

        batches: dict[int, _Batch] = {}
        for work in waited:
            if work.batch is not None:
                batches[id(work.batch)] = work.batch
        usage = _merge_usage(tuple(batches.values()))
        return EmbeddingResult(vectors=tuple(vectors), usage=usage)

    def _new_work(self, text: str) -> _TextWork:
        future: asyncio.Future[tuple[float, ...]] = asyncio.get_running_loop().create_future()
        future.add_done_callback(_consume_future_exception)
        return _TextWork(text=text, future=future)

    def _ensure_dispatch(self) -> None:
        task = self._state.dispatch_task
        if task is None or task.done():
            self._state.dispatch_task = asyncio.create_task(self._dispatch_pending())

    async def _dispatch_pending(self) -> None:
        current = asyncio.current_task()
        try:
            # sleep(0) still yields once, which lets callers created in the
            # same event-loop turn join the pending set when coalesce_ms is 0.
            await asyncio.sleep(self.coalesce_ms / 1_000.0)
            if not self._state.pending:
                return
            pending = self._state.pending
            self._state.pending = {}
            works = tuple(pending.values())
            for start in range(0, len(works), self.max_batch_size):
                chunk = works[start : start + self.max_batch_size]
                batch = _Batch(texts=tuple(work.text for work in chunk), works=chunk)
                for work in chunk:
                    work.batch = batch
                    self._state.inflight[work.text] = work
                task = asyncio.create_task(self._run_batch(batch))
                self._state.background.add(task)
                task.add_done_callback(self._state.background.discard)
        finally:
            if self._state.dispatch_task is current:
                self._state.dispatch_task = None

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            result = await self.model.embed(batch.texts)
            vectors = _checked_vectors(batch.texts, result)
            batch.usage = result.usage
            for text, vector in zip(batch.texts, vectors, strict=True):
                self._cache_put(text, vector)
                work = self._state.inflight.get(text)
                if work is not None and work.batch is batch and not work.future.done():
                    work.future.set_result(vector)
        except BaseException as error:
            for work in batch.works:
                if not work.future.done():
                    work.future.set_exception(error)
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            for work in batch.works:
                if self._state.inflight.get(work.text) is work:
                    del self._state.inflight[work.text]

    def _cache_get(self, text: str) -> tuple[float, ...] | None:
        if self.cache_size == 0:
            return None
        vector = self._state.cache.get(text)
        if vector is None:
            return None
        self._state.cache.move_to_end(text)
        return vector

    def _cache_put(self, text: str, vector: tuple[float, ...]) -> None:
        if self.cache_size == 0:
            return
        self._state.cache[text] = vector
        self._state.cache.move_to_end(text)
        while len(self._state.cache) > self.cache_size:
            self._state.cache.popitem(last=False)


def _checked_vectors(texts: tuple[str, ...], result: EmbeddingResult) -> tuple[tuple[float, ...], ...]:
    if len(result.vectors) != len(texts):
        raise ValueError("embedding vector count does not match the batch size")
    return result.vectors


def _consume_future_exception(future: asyncio.Future[object]) -> None:
    """Mark a completion retrieved when every waiter was cancelled."""

    if not future.cancelled():
        future.exception()


def _merge_usage(batches: Sequence[_Batch]) -> InferenceUsage:
    usages = tuple(batch.usage for batch in batches if batch.usage is not None)
    return InferenceUsage(
        requests=sum(usage.requests for usage in usages),
        input_tokens=_sum_optional(usage.input_tokens for usage in usages),
        output_tokens=_sum_optional(usage.output_tokens for usage in usages),
    )


def _sum_optional(values: Iterable[int | None]) -> int | None:
    items = tuple(values)
    if not items or any(item is None for item in items):
        return None
    return sum(item for item in items if item is not None)


__all__ = ["CoalescingEmbeddingModel"]
