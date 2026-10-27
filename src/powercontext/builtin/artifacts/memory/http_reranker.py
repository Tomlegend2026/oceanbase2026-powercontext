import math
import httpx
from typing import Sequence

from powercontext.artifacts.memory import MemoryReranker, MemoryHit, MemoryRerankDecision
from powercontext.artifacts.usage import InferenceUsage

# 如果 MemoryMatchedBy 需要导入，加上这一句（如果报错找不到，说明不需要）
# from powercontext.artifacts.memory import MemoryMatchedBy

class HTTPMemoryReranker(MemoryReranker):
    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1"
    ):
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
        # 1. 参数边界校验
        if not candidates or self.batch_size <= 0 or not (1 <= limit <= len(candidates)):
            raise ValueError("Invalid parameters: candidates non-empty, batch_size > 0, 1 <= limit <= len(candidates)")

        # 2. 组装完整候选池的 payload，记录原始一基 rank
        all_candidates_payload = []
        for idx, hit in enumerate(candidates):
            global_rank = idx + 1
            all_candidates_payload.append({
                "rank": global_rank,
                "text": hit.text
            })

        # 3. 分批发送请求
        scores_map = {}
        request_count = 0

        for i in range(0, len(all_candidates_payload), self.batch_size):
            batch = all_candidates_payload[i : i + self.batch_size]
            batch_ranks = {c["rank"] for c in batch}
            
            payload = {
                "query": query,
                "candidates": batch
            }

            # 网络请求与 HTTP 状态异常处理（保留 httpx 异常）
            try:
                response = await self.client.post(self.endpoint, json=payload)
                response.raise_for_status()
                request_count += 1
            except httpx.HTTPError:
                raise  # 直接抛出

            # JSON 解析与 Schema 校验
            try:
                data = response.json()
            except Exception as e:
                raise ValueError("Invalid JSON response") from e

            if not isinstance(data, dict) or "scores" not in data or not isinstance(data["scores"], list):
                raise ValueError("Response must contain 'scores' list")

            returned_ranks = set()
            for item in data["scores"]:
                if not isinstance(item, dict) or "rank" not in item or "score" not in item:
                    raise ValueError("Each score must contain 'rank' and 'score'")
                
                # 检查是否包含额外字段
                if set(item.keys()) != {"rank", "score"}:
                    raise ValueError("Score object contains extra fields")

                r = item["rank"]
                s = item["score"]

                # 检查跨批、缺失、重复
                if r not in batch_ranks:
                    raise ValueError(f"Invalid rank {r} for this batch")
                if r in returned_ranks:
                    raise ValueError(f"Duplicate rank {r}")
                
                # 检查分数是否为有限数值（拒绝 NaN 和 Infinity）
                if not isinstance(s, (int, float)) or not math.isfinite(s):
                    raise ValueError("Score must be a finite number")

                returned_ranks.add(r)
                scores_map[r] = s

            # 确保本批次的 rank 恰好全覆盖
            if returned_ranks != batch_ranks:
                raise ValueError("Missing ranks in response")

        # 4. 全局排序：按分数降序，分数相同按原始全局 rank 升序
        sorted_ranks = sorted(
            scores_map.keys(),
            key=lambda r: (-scores_map[r], r)
        )

        # 5. 截取前 limit 个
        selected_ranks = tuple(sorted_ranks[:limit])
        
        # 6. 计算丢弃的候选数量
        discarded_count = len(candidates) - len(selected_ranks)
        
        # 7. 组装 usage 和 最终 Decision
        usage = InferenceUsage(requests=request_count)

        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=usage,
            discarded_rank_count=discarded_count,
            used_fallback=False,
            matched_by=tuple(),  # 若有报错，检查是否需要传入特定的枚举值，如 (MemoryMatchedBy.SCORE,)
        )