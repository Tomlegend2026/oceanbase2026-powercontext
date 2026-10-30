# Qoder 任务书（可直接粘贴）

用法：一个里程碑一个会话。开新会话先粘"通用前置"，再粘该里程碑的任务。每完成一个里程碑：跑验收 → 截图/录屏 → 提交 → 在 [qoder-log.md](qoder-log.md) 补一行。

## 通用前置（每个会话都粘）

```
你在实现 qoder/ 下的 RecallCoach 项目。先读同目录的：
- docs/architecture.md（组件、边界、app.db 结构、内部 REST 契约、打分公式）
- docs/memory-design.md（记忆分类、写入策略、读取决策点、审计格式、测试要求）

硬约束：
1. 所有工作只在 qoder/ 内，不要改 qoder/ 之外的文件。
2. 严格按文档里的接口路径与字段；PowerContext 的接口形状不要自己发明，不确定就先问我。
3. 默认零模型依赖、零外部 API Key；不要引入需要密钥的服务。
4. 每次改动配 pytest 单测；不许跳过或删除既有测试。
5. 中文注释与中文界面文案；代码标识符用英文。
6. 完成时给我：改动文件清单、验收命令、以及"哪些地方你做了假设"。
```

---

## M1 记忆网关与 doctor

```
目标：把 PowerContext 打通到能被后续模块调用的程度。

实现：
- app/pyproject.toml：Python 3.12，依赖 fastapi、uvicorn、httpx、pydantic；dev 依赖 pytest。src 布局。
- memory/gateway.py：httpx.AsyncClient 封装
  POST /v1/memory/remember、POST /v1/memory/search、GET /v1/memory/entries/list、
  POST /v1/memory/entries/revise、POST /v1/memory/entries/retire、POST /v1/context/prepare、
  POST /v1/scopes、GET /v1/scopes/default、GET /v1/capabilities。
  统一带 Authorization: Bearer，超时 10s，失败抛自定义异常（不要吞掉）。
- memory/audit.py：每次调用写 memory_audit.jsonl（字段见 memory-design.md 第 7 节），
  解析响应头 X-PowerContext-Request-ID，token 与路径一律脱敏。
- store/sqlite.py：连接管理 + schema.sql（architecture.md 第 5 节，含 memory_refs）。
- cli.py：init-db（建库 + 导入 seed/db-systems.cards.json）、doctor（PC 地址/ready/capabilities/scope/卡片数/记忆数/审计路径）。
- 启动时若无 scope：POST /v1/scopes {title, summary, idempotency_key} 建一个并写入 settings。

验收：
- uv run python -m recallcoach.cli init-db --seed ../seed/db-systems.cards.json  → 28 张卡
- uv run python -m recallcoach.cli doctor  → 六项全绿，打印 capabilities 与 scope
- 单元测试：MockTransport 断言 URL/请求体/审计落盘/request_id 解析

不要做：队列、答题、打分、前端。
```

**记录点**：录屏 1 —— 空目录到 `doctor` 打印 `mode=auto,fts` 与 scope；贴一条真实 `remember` 响应（含 entry_id）。

---

## M2 调度与种子导入

```
目标：不依赖记忆的基础复习节奏。

实现：
- store/seed.py：解析 seed/db-systems.cards.json（course/chapters/cards），幂等导入 kp、cards、schedule。
- domain/scheduler.py：SM-2。correct=1 时 interval 递增、ease 上调；correct=0 时 lapses+1、interval 归 1。
  暴露 due_cards(today, limit)、due_score(card_schedule, today) 归一化到 0..1。
- domain/clock.py：today() 优先读 RECALLCOACH_TODAY（ISO 日期），否则本地日期。
- 单测：到期边界、ease 下限 1.3、lapses 递增、归一化范围。

验收：init-db 后 SELECT count(*) FROM cards = 28；due_cards 返回 28 条；单测全绿。
```

**记录点**：截图 Qoder 把 28 张卡结构化的对话 + 你手工改过的卡片内容（标注人工修改）。

---

## M3 主流程：今天 / 答题 / 小结

```
目标：能刷完一轮，先不接记忆。

实现：
- api/today.py、api/answer.py、api/session.py（契约见 architecture.md 第 6 节）。
- web/：三页（今日队列、答题、小结），原生 HTML/JS/CSS，无构建、无 CDN。
- 答题：显示题面 → 翻面看答案 → 三档自评（忘了/勉强/会了）→ 可选错因下拉 → 下一张。
- 小结：本次正确率、下次到期时间。
- 单测：API 层用临时 sqlite；断言作答后 schedule 变化。

验收：连续刷完 10 张；重启后进度在。
```

**记录点**：从队列页到小结页的完整流转截图。

---

## M4 记忆写入策略

```
目标：记忆只在该写的时候写，且不重复堆积。

实现：
- domain/memory_policy.py：按 memory-design.md 第 3 节的决策状态机。
  输入包含 card、correct、confidence、cause、近 7 天 lapses、是否已有 active weakness。
  输出 {action, kind, text, reason}，纯函数，不直接调网关。
- service 层执行决策：write → remember；revise → 用 memory_refs 里的 citation 调 revise；
  写入成功后把 citation 落 memory_refs。
- 禁写校验 + 单日上限（待确认列表存 settings 或独立表）。
- /api/answer 返回 memory_decision，前端 toast 明示"已记下：…"，可当场撤销（retire）。
- 单测：memory-design.md 第 10 节列的 7 个场景。

验收：连续答错同一 kp 两次 → 只有 1 条 weakness；第三次 → version 2，entry_id 不变。
```

**记录点**：录屏 2 —— 两次答错只写一条、第三次 revise；同屏展示 `entries/list`。

---

## M5 记忆驱动排序

```
目标：记忆真的改变出题顺序，而且能在界面上解释。

实现：
- domain/scoring.py：architecture.md 第 7 节公式，返回 score 与 breakdown。
- api/today.py：personalization=on 时 search（query 构造见 memory-design.md 第 6 节）→ 命中集合
  → 打分 → 附 memory_hits；同时 prepare 取 coach_note。
- 页面：卡片上显示 breakdown 与命中 entry_id；讲解区顶部显示教练备忘。
- personalization=off：不 search、不 prepare、不写记忆，mode=ab_off。
- PC 不可用：degraded=true、mode=offline，纯到期排序 + 顶部横幅，不伪造记忆。
- 单测：命中时顺序前移、off 时完全按 due、prepare 为空时不渲染备忘。

验收：同一批卡片 on/off 两列顺序不同；off 时 db-022 回落到第 7 位。
```

**记录点**：on/off 对照截图 + 卡片上的分数拆解。

---

## M6 Replay：改写与退休

```
目标：用户能改记忆，改完立刻影响队列。

实现：
- GET /api/replay：entries/list 列 active + retired，按 kind 分组，显示 version、时间、命中卡片。
- POST /api/memory/{entry_id}/revise|retire：用 memory_refs 的 citation 调对应接口，
  成功后重算队列，返回移动的卡片列表。
- 页面：编辑框、保存、"已掌握"按钮；操作后高亮顺序变化。
- 单测：revise 后 version+1；retire 后该 kp 不再命中；citation 缺失时报错而不是静默跳过。

验收：改一条 weakness → 顺序变化；退休 → 回落。
```

**记录点**：录屏 3 —— 编辑 → 重排 → `entries/list` 里 version/state 变化。

---

## M7 对照页与证据导出

```
目标：让评审不用读代码就能判断"记忆在做什么"。

实现：
- GET /api/compare?limit=5：on/off 两列 + diff（位置变化、理由）。
- GET /api/evidence：审计日志最近 7 天 + report_markdown（环境快照、capabilities、记忆清单、
  A/B 差异、最近 20 条问答）。
- 页面：表格 + "导出 Markdown" 按钮。

验收：导出的 md 能独立解释一次排序变化。
```

**记录点**：Evidence 页截图 + 导出的报告文件。

---

## M8 验证脚本与文档

```
目标：别人按文档能复现。

实现：
- scripts/verify.ps1：跑 V1–V7（docs/verification.md），独立 scope、固定日期，
  产物写 evidence/verify/<ts>/，结束打印预期 vs 实际对照表与 summary.md。
- 补 scripts/*.sh（macOS/Linux 等价）。
- RUNBOOK.md 补齐应用侧步骤与故障排查。
- 给 app/tests 补一条端到端测试（假 PC 服务），保证 CI 可跑。

验收：干净环境按 RUNBOOK 从零到验证全绿。
```

**记录点**：录屏 4 —— 一条命令跑完验证并打开证据目录。

---

## M9 展示站

```
目标：Qoder Sites 上的项目网站。

实现（site/，纯静态、无构建）：
- 首屏：一句话 + 痛点对比（"复习一遍 2 小时 → 20 分钟"）。
- 功能：3 张截图/短 GIF（队列、分数拆解、Replay）。
- 技术：架构图（SVG 或内联 HTML）、记忆边界表、A/B 对照图、审计日志截图。
- 验证：V1–V7 摘要 + 命令片段。
- 链接：仓库、RUNBOOK、验证文档。
- 不出现：token、真实姓名、本机绝对路径。

验收：无痕窗口可打开；可见范围"任何人都可访问"。
```
