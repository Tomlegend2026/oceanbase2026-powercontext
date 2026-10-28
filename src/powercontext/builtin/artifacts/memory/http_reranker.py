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

"""HTTP cross-encoder Memory reranking over an already ranked candidate pool."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

_DEFAULT_BATCH_SIZE = 32
_DEFAULT_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score one candidate pool through a caller-supplied HTTP service.

    The service contract is deliberately small: one POST carries a query and a
    batch of originally ranked candidates, and the response maps every rank in
    that batch to one finite score. All batches contribute to one global
    selection, so a later batch can outrank an earlier one.
    """

    policy_id: str = _DEFAULT_POLICY_ID

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        policy_id: str = _DEFAULT_POLICY_ID,
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a strictly positive integer")
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: Sequence[MemoryHit],
        limit: int,
    ) -> MemoryRerankDecision:
        """Return the globally best ``limit`` original ranks from the pool."""

        pool = self._candidate_pool(candidates)
        if not pool:
            raise ValueError("rerank requires at least one candidate")
        if type(self.batch_size) is not int or self.batch_size < 1:
            raise ValueError("batch_size must be a strictly positive integer")
        if type(limit) is not int or limit < 1 or limit > len(pool):
            raise ValueError("limit must be between one and the candidate count")

        scores: dict[int, float] = {}
        request_count = 0
        for start in range(0, len(pool), self.batch_size):
            batch = pool[start : start + self.batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text} for rank, candidate in zip(ranks, batch, strict=True)
                ],
            }
            response = await self.client.post(self.endpoint, json=payload)
            self._require_success(response)
            request_count += 1
            scores.update(self._parse_scores(response, ranks))

        ordered_ranks = sorted(scores, key=lambda rank: (-scores[rank], rank))
        selected_ranks = tuple(ordered_ranks[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=request_count),
            discarded_rank_count=len(pool) - len(selected_ranks),
            used_fallback=False,
        )

    @staticmethod
    def _candidate_pool(candidates: Sequence[MemoryHit]) -> tuple[MemoryHit, ...]:
        try:
            return tuple(candidates)
        except TypeError as error:
            raise ValueError("candidates must be a sequence") from error

    @staticmethod
    def _require_success(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        request = getattr(response, "request", None)
        if not isinstance(request, httpx.Request):
            request = httpx.Request("POST", "https://reranker.invalid")
        raise httpx.HTTPStatusError(
            f"Reranker returned HTTP {response.status_code}",
            request=request,
            response=response,
        )

    @staticmethod
    def _parse_scores(response: httpx.Response, ranks: tuple[int, ...]) -> dict[int, float]:
        try:
            payload: Any = response.json()
        except ValueError as error:
            raise ValueError("Reranker response is not valid JSON") from error
        if not isinstance(payload, dict) or set(payload) != {"scores"}:
            raise ValueError("Reranker response must contain only 'scores'")
        entries = payload["scores"]
        if not isinstance(entries, list):
            raise ValueError("Reranker response 'scores' must be an array")  # noqa: TRY004

        expected = set(ranks)
        observed: set[int] = set()
        scores: dict[int, float] = {}
        for entry in entries:
            rank, score = HTTPMemoryReranker._parse_score(entry, expected, observed)
            scores[rank] = score

        if observed != expected:
            raise ValueError("Scores must cover every rank in the current batch exactly once")
        return scores

    @staticmethod
    def _parse_score(entry: Any, expected: set[int], observed: set[int]) -> tuple[int, float]:
        if not isinstance(entry, dict) or set(entry) != {"rank", "score"}:
            raise ValueError("Each score must contain exactly 'rank' and 'score'")
        rank = entry["rank"]
        score = entry["score"]
        if type(rank) is not int or rank not in expected:
            raise ValueError("Score rank must be an integer from the current batch")
        if rank in observed:
            raise ValueError("Score rank must not repeat inside a batch")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise ValueError("Score must be a finite number")  # noqa: TRY004
        if isinstance(score, float) and not math.isfinite(score):
            raise ValueError("Score must be a finite number")
        observed.add(rank)
        return rank, score


__all__ = ["HTTPMemoryReranker"]
