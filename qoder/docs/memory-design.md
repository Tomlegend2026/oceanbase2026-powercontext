# 记忆设计（Qoder 实现依据）

这份文档是 `app/src/recallcoach/` 的 spec。实现时以本文件的字段、路径、阈值、文案为准；**不要自行发明 PowerContext 的接口形状**，不确定就先停下来问，而不是猜。

## 1. 原则

1. 记忆存**判断**，不存流水。题面、答案、作答时间留在 `app.db`。
2. 只写"将来会用到"的结论：薄弱点、具体混淆、讲解偏好、进度叙事、下次承诺。
3. 每次读取都发生在决策点上（排序、讲解、次日首屏），而不是为了展示"我调用了 API"。
4. 任何一条记忆都能被用户改写或退休，改完立即影响排序。
5. 记忆不参与正确率统计，避免写坏记忆污染成绩。

## 2. 分类

| kind | 语义 | 文本模板 | 前缀 |
|---|---|---|---|
| `weakness` | 知识点薄弱 | `[kp:db/ch6/mvcc-readview] MVCC read view 的创建时机反复出错；能说出"快照"但说不清 RC 与 RR 的差别。` | `[kp:<kp_id>]` |
| `misconception` | 具体混淆 | `[kp:db/ch6/mvcc-readview] 把 read view 的创建时机与锁的持有范围混为一谈，第 3 次。` | `[kp:<kp_id>]` |
| `preference` | 讲解偏好 | `[pref] 先给结论再给推导；单个概念不超过 3 句；例子优先用"两个并发事务"。` | `[pref]` **（v2，本轮未实现）** |
| `progress` | 进度叙事 | `[kp:db/ch2] 10/28 复习完 SQL 章；NOT IN 遇 NULL 还卡着，下次从 db-008 开始。` | `[kp:<章>]` |
| `commitment` | 下次承诺 | `[next] 明天先过 db-008 与 db-022，再开范式章。` | `[next]` **（v2，本轮未实现）** |

**`preference` / `commitment` 本轮未实现**（记为 v2）：策略层、网关与打分都没有这两条分支，代码里不会写入、UI 里也没有入口。它们的触发入口——题目下方反复点"换个讲法"累计到 `PREFERENCE_STREAK_THRESHOLD` 才写 `preference`、小结页点"就这么定"写 `commitment`——需要先在 UI 上补两个明确的按钮与计数，留待后续实现；`PREFERENCE_STREAK_THRESHOLD` 常量保留在下面的清单里作为设计意图的记录，当前无代码引用。

前缀是刻意设计：给人读，同时给 FTS 提供稳定的英文 token，保证"按知识点精确召回"和"按自然语言召回"都成立。`kp_id` 与 `app.db.kp.id` 完全一致。

## 3. 写入策略（状态机）

`domain/memory_policy.py` 接收一次作答事实，返回决策：

```
输入: card, correct, confidence, cause, kp_recent_lapses, has_active_weakness
输出: [{action: "skip" | "write" | "revise", kind, text, reason}, ...]   # 薄弱点分支与混淆分支各至多一条

若 correct: 
    skip（不在答对时新增薄弱点；答对只影响调度）
否则:
    lapses = 该 kp 近 7 天的 lapses（含本次）
    若 lapses < 2:                     → skip, reason="lapses<2"
    若 has_active_weakness:            → revise 该条目（文本追加"第 N 次"）
    否则:                              → write weakness
若 cause == "概念混淆" 且用户填了混淆对象:
    → write misconception（同 kp 已有则 revise）
若用户连续 2 次点同向"换个讲法":                     # v2：本轮未实现，UI 还没有"换个讲法"入口
    → write preference（已存在则 revise）
会话结束:
    → write progress（同一天已有则 revise）
用户点"就这么定":                                     # v2：本轮未实现，小结页没有确认按钮
    → write commitment
```

**分支独立性**（实现约定）：`若 cause == "概念混淆"` 这一支**不套** `lapses < 2` 门槛——它记的是用户在错因下拉里确认过的判断，而不是我们的推断（第 5 节禁写清单挡的是后者）。由此：

- `decide_answer` 返回**决策列表**：薄弱点分支给一条（skip / write / revise）；混淆分支只在"选了概念混淆**且**写出混淆对象"时给一条，没填对象就不给；两条可以同时出现（对应第 4 节"每个 kp 各存 1 条 active"）。
- 只要有一条是 write/revise，就不再回传另一条的 skip，免得证据里"写了"和"跳过"并存；全是 skip 时按 `(action, reason)` 去重。
- `personalization=False`（会话 `mode="ab_off"`）时策略层直接返回 `[skip: personalization-off]`，不产生任何写操作——第 9 节要求 off 组连写都不写，A/B 才可比。

阈值常量集中在一处，便于测试与调参：

```python
WEAKNESS_LAPSE_WINDOW_DAYS = 7
WEAKNESS_LAPSE_THRESHOLD = 2
PREFERENCE_STREAK_THRESHOLD = 2
DAILY_WRITE_CAP = 20
MAX_TEXT_CHARS = 300        # 远低于 API 的 8192 字节上限
```

## 4. 去重与上限

1. 每个 kp 最多 1 条 active `weakness` + 1 条 active `misconception`；再触发就 `revise`。
2. `preference` 全局最多 5 条 active；满了先 `retire` 最旧的。（v2，随 `preference` 一起未实现）
3. `progress` 每天最多 1 条；`commitment` 每天最多 1 条。（`commitment` 属 v2，本轮未实现）
4. 单日写入达到 `DAILY_WRITE_CAP` 时**不静默 skip**：策略层把本要执行的 `write`/`revise` 降级成 `skip + reason="daily-cap"`，但**保留 `kind` / `text` / `target_entry_id`**，于是
   - `/api/answer`、`/api/session/end` 的 `memory[]` 对应项返回 `status="pending"`，响应顶层带 `pending_writes` 计数；
   - 同时往当天的审计文件追加一行 `op="memory.pending"`（含 `would_be`、`kind`、`text`、`target_entry_id`、`writes_today`、`daily_cap`），"待确认"清单以审计日志为准，不再另建表；
   - 只有"本来就要写"的分支才会被降级；本来就 `skip`（`lapses<2`、`correct`、`forbidden`、空会话）的决策回传各自的原因，不冒充成上限。
5. 写入前用 `entries/list` 查同 kp 是否已有 active 条目——不要靠 `search` 判断存在性（search 是召回，不是去重）。

## 5. 禁写清单

- 题面、答案、解析原文。
- 姓名、学号、邮箱、任何 token、文件绝对路径。
- 情绪或人格判断（"基础差""不认真"）。
- 未经用户在错因下拉里确认的推断。
- 单次随机错误（由第 3 节阈值挡掉）。

写入前跑一次校验函数；命中即 `skip` 并记 `reason="forbidden"`，UI 不提示。

## 6. 读取决策点

| 决策点 | 调用 | 处理 |
|---|---|---|
| 生成今日队列 | `GET /v1/memory/entries/list` + `POST /v1/memory/search` | 清单给出按 kp 精确对齐的命中集合；search 补充语义召回；两者合并后参与打分 |
| 单卡讲解 | `POST /v1/context/prepare` | `content` 作为"教练备忘"置于题目上方 |
| 次日首屏 | `search`（query 含"下次"） | 显示"上次停在 db-008" |

**为什么清单和检索都用**：记忆条目总量只有几十条，用清单可以按 `kp_id` 精确对齐、不漏条目；而长 query 会撞上服务端的覆盖率准入门槛（见下），单靠 search 会漏掉"看似无关"的薄弱点。所以清单负责完整性，search 负责语义补充，两者按 `entry_id` 去重合并。

**query 构造**（决定语义召回质量，别把整段题面丢进去）：

```
query = 队列首位那张卡的 chapter_title + kp_name（截断 120 字符）
limit = 20
mode  = "auto"
```

**中文可用性依据**：记忆入库时正文经 `analyze_text` 归一（`src/powercontext/builtin/persistence/memory.py` 中 `searchable_text=analyze_text(version.text)`），该分析器对 CJK 输出单字 `u_xxxx` 与双字 `b_xxxx_xxxx` token（`src/powercontext/builtin/artifacts/search.py` 的 `flush_cjk`）。因此中文 query 与中文正文能按字/双字重合召回，不需要额外分词服务。副作用是短 query 可能召回偏宽——所以打分只把命中当 0/1 信号，不用它做精细排序。

**降级**：`search` 返回空 `hits` 时，队列照常出（只按到期分），讲解区显示"暂无历史，按默认讲法"；`prepare` 返回 `status:"empty"` 时同样处理。**不要**伪造记忆或补一段通用建议。

**query 长度是硬约束**：真实 Server 上把整队 20 个知识点拼成 240 字符的 query，`context/prepare` 直接返回 `status:"empty"`；收敛到"首位卡的章节 + 知识点"（约 20 字符）后稳定返回 `ready`。命中集合本身不依赖 query——它来自 `entries/list` 的清单对齐，所以 query 只需要服务"教练备忘"这一条路径。`n`n**本机复核结论**（用仓库自身的 `analyze_text` 与 SQLite FTS5 复现）：

- 中文记忆文本入库后形如 `kp db ch6 mvcc readview mvcc u_7684 read view ...`，`隔离级别`、`read view 快照` 都能召回该条。
- 纯 FTS 会偏宽：`BCNF 超键` 对另一条偏好条目也产生了候选，但服务端的覆盖率准入（`admits_fts_text`，`BCNF 超键` 要求命中 2 个 token）把它挡掉了。
- 长 query 代价：`事务 并发 隔离级别 read view 快照` 生成 18 个 token、要求至少命中 5 个。**所以 query 必须把课程、章节、知识点一起写**，只写一个词会召回不稳。
- 结论：`hits` 只作为 0/1 信号参与打分，不做精细排序；这也是打分公式里 `hit()` 取 0/1 的原因。

## 7. 审计格式

配置里写的是**基名** `memory_audit.jsonl`；实际落盘按天滚动成 `memory_audit-YYYYMMDD.jsonl`（`RECALLCOACH_AUDIT` 只给基名与目录，日期由实现补上）。归哪一天看记录自己的 `ts`，写入与读取因此不会错位；`ts` 解析不出来时兜底到今天。`/api/health` 的 `audit_path` 回基名、`audit_file` 回今天正在写的那个文件，`doctor` 打印后者。

一行一次调用：

```json
{"ts":"2026-10-30T15:41:02+08:00","op":"memory.search","scope_id":"...","personalization":true,
 "request":{"query":"数据库系统 第6章 事务与并发 MVCC read view","limit":20,"mode":"auto"},
 "response":{"mode":"fts","hit_count":2,"entry_ids":["e3f1a9","7c22b0"]},
 "request_id":"...","latency_ms":37,"status":"ok"}
```

除网关调用外还有一类"待确认"记录（第 4 节第 4 条），它没有 `request_id`：

```json
{"ts":"2026-10-30T21:02:11+08:00","op":"memory.pending","personalization":true,
 "card_id":"db-022","kp_id":"db/ch6/mvcc-readview","kind":"weakness",
 "text":"[kp:db/ch6/mvcc-readview] MVCC 与 read view 创建时机：2 次未通过，最近一次自评 2/5。",
 "would_be":"write","target_entry_id":null,"writes_today":20,"daily_cap":20,"status":"pending"}
```

规则：

- `request_id` 取响应头 `X-PowerContext-Request-ID`；取不到写 `null`。
- 不记录 token；`request` 里出现敏感字段一律打码。
- 失败也记一行，`status:"error"` + `error` 摘要，便于排查"为什么没生效"。
- Evidence 页只读**最近 7 天**的滚动文件（`audit_window_days` 常量），从最旧那天起按时间正序拼接，再截到最后 `limit` 条；`audit_files` 回显真正读到的文件名。窗口锚点取系统当天，而不是 `RECALLCOACH_TODAY`——审计的 `ts` 本来就是系统时间，锚在同一处才对得上。

## 8. 生命周期

| 操作 | 接口 | 要求 |
|---|---|---|
| 写入 | `POST /v1/memory/remember` `{scope_id, kind, text, reason?}` | 返回 `citation{entry_id, entry_version_id, memory_ref}` |
| 检索 | `POST /v1/memory/search` `{scope_id, query, limit, mode}` | 响应含 `mode` 与 `hits` |
| 盘点 | `GET /v1/memory/entries/list?scope_id=` | Replay 页用它列全量，不用 search |
| 修订 | `POST /v1/memory/entries/revise` `{scope_id, citation, kind, text}` | 必须回传完整 citation；版本 +1 |
| 退休 | `POST /v1/memory/entries/retire` `{scope_id, citation, reason?}` | 之后不再被召回 |

`citation` 必须落库，否则无法修订。`app.db` 加一张**指针表**（只存引用，不存正文）：

```sql
CREATE TABLE memory_refs (
  entry_id TEXT PRIMARY KEY,
  citation_json TEXT NOT NULL,
  kp_id TEXT, kind TEXT, written_at TEXT
);
```

丢了也能用 `entries/list` 重建，属于可恢复的缓存。

## 9. `personalization` 语义

- `on`（默认）：读写都开。
- `off`：不调 `search`、不调 `prepare`、**也不写任何记忆**，`sessions.mode="ab_off"`，审计标 `personalization:false`。
- 这样 on/off 才是同一批卡片的可比实验；"off 还偷偷写"会污染下一次对照。

## 10. 测试要求（与功能同 commit）

`app/tests/test_memory_policy.py` 至少覆盖：

1. 第一次答错 → `skip`。
2. 同 kp 第二次答错 → `write`，kind=`weakness`。
3. 同 kp 第三次答错 → `revise`，不是新增。
4. 已有 active 时再次触发 → `revise`。
5. 命中禁写清单 → `skip`。
6. 单日达到 20 条 → 本要执行的 `write`/`revise` 降级成 `skip + reason="daily-cap"` 且保留 `kind`/`text`/`target_entry_id`；接口返回 `status="pending"` 与 `pending_writes`，同时审计里多一行 `op="memory.pending"`；本来就 skip 的分支不冒充成上限。
7. `personalization=off` → 策略层不产生任何写操作。
8. 错因选了"概念混淆"且写出对象 → 即使 `lapses=1` 也写 1 条 `misconception`（不受第 1 条门槛约束）。
9. 同一次答错同时过两个分支 → 返回 `weakness` + `misconception` 两条决策；没填混淆对象时只有 `weakness`。
10. 审计按天滚动：不同 `ts` 的记录落进不同 `memory_audit-YYYYMMDD.jsonl`，基名文件本身不写入。
11. Evidence 读最近 7 天：第 8 天之前的文件不进列表，结果按天从旧到新，`limit` 截最后若干条。

网关层用假 HTTP 服务（httpx MockTransport）断言：URL、请求体、`X-PowerContext-Request-ID` 解析、审计落盘各一条。
端到端另起一个子进程跑 `tests/fake_pc_server.py`（随机端口，见 `app/tests/test_e2e_fake_pc.py`），断言冷启动无记忆加权、`skip→write→revise` 的 `entry_id` 连续、`/api/compare` 位次差异、以及重启后条目数不变。
