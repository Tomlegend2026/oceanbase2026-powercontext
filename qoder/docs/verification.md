# 验证样例

两组：**技术层**（现在就能跑，不依赖应用）与**端到端**（应用完成后）。所有样例使用独立 scope 与固定日期，评审无需作者个人数据。

## 0. 环境准备

```powershell
cd qoder
.\scripts\bootstrap.ps1          # 首次
.\scripts\start.ps1              # 或手工起 PowerContext
.\scripts\verify-powercontext.ps1 -Reset   # 建一个新的验证 scope
```

约定：`$PC_URL`、`$PC_TOKEN`、`$PC_SCOPE` 由脚本写入 `evidence/verify/<时间戳>/env.json`，后续命令从该文件读取。

## T1 技术层：写入、召回、上下文组装

- **初始数据**：空 scope。
- **操作**：`verify-powercontext.ps1` 执行 ① 建 scope ② 写 1 条 weakness ③ 写 1 条 preference ④ 检索"事务 并发 隔离级别" ⑤ `context/prepare`。
- **预期**：
  - 两次 `remember` 各返回一个 `entry_id`；
  - `search` 返回 `mode: "fts"`，`hits` ≥ 1，且包含 weakness 那条的 `entry_id`；
  - `prepare` 返回 `status: "ready"`，`content` 非空且含 `[kp:db/ch6/mvcc-readview]`；
  - 无意义 query（如 `zzzqqq`）返回 0 命中。
- **证据**：`evidence/verify/<ts>/` 下的 `01-scope.json` … `05-prepare.json` 与 `summary.md`。
- **判据**：脚本退出码 0，summary 全 PASS。

## T2 中文召回：字/双字 token 生效

- **操作**：写入一条纯中文记忆"事务隔离级别中，RC 每条语句生成快照"，再用"隔离级别"检索。
- **预期**：能召回该条（证明中文不需要额外分词）。
- **证据**：`search` 请求与响应原文。
- **备注**：这条同时是给评审看的"中文不是摆设"的证据。

## V1 冷启动：记忆为空时行为正确

- **初始数据**：新 scope，0 条记忆；28 张卡全部到期。
- **操作**：`GET /api/today?personalization=on`。
- **预期**：10 张卡，全按 `due_score` 排序；每张 `score_breakdown.weak = 0`；备忘区显示"暂无历史，按默认讲法"。
- **证据**：`context/prepare` 返回 `status:"empty"`；审计日志 `mode:"fts"`、`hit_count:0`。

## V2 写入闭环：两次失败才写，第三次是修订

- **初始数据**：V1 的 scope；目标卡 `db-022`（kp `db/ch6/mvcc-readview`）。
- **输入序列**（跨两个会话，用 `RECALLCOACH_TODAY` 固定日期）：
  1. 会话 A：`db-022` 答错，自评 1 → 预期 `skip`（`lapses=1`）。
  2. 会话 B（次日）：`db-022` 再答错，自评 2 → 预期 `write weakness` **v1**。
  3. 会话 B 内第三次：`db-022` 再答错 → 预期 `revise` 成 **v2**，`entry_id` 不变。
- **预期**：`entries/list` 中 `db/ch6/mvcc-readview` 只有 1 条 active weakness；文本末尾出现"第 3 次"。
- **证据**：三条决策记录（`skip` / `write` / `revise`）+ `entries/list` 截图 + UI toast。

## V3 记忆改变排序（核心样例）

- **初始数据**：V2 结束状态（1 条 weakness 命中 `db-022`）。
- **操作**：`GET /api/today?personalization=on` 与 `=off`。
- **预期**（示例值，实际以输出为准）：
  - `off`：`db-022` 排第 7 位，`score = 0.41`；
  - `on`：`db-022` 排第 1 位，`score = 0.41 + 1.20 = 1.61`，`memory_hits` 含该 `entry_id`。
- **证据**：`/api/compare` 截图（两列并排 + 差异高亮）+ 审计日志中两次 `memory.search`。

## V4 修订与退休：人改记忆，排序立刻变

- **操作**：Replay 页把该 weakness 改成"只需记住 read view 的创建时机"，保存；再点"已掌握"。
- **预期**：保存后 version + 1，排序仍靠前；退休后 `db-022` 回落，`entries/list` 显示 `state:"retired"`。
- **证据**：`revise` / `retire` 响应 + 前后队列对比截图。

## V5 上下文注入：教练备忘来自记忆

- **操作**：打开 `db-022` 讲解。
- **预期**：题目上方 2–4 行备忘，末尾带 `[entry ...]` 引用；`prepare.content` 含"read view"字样。
- **证据**：`prepare` 请求/响应原文 + UI 截图；审计记录 `max_bytes=2000`。

## V6 重启仍在：记忆是持久的

- **操作**：停 PowerContext → 重启 → 刷新应用。
- **预期**：记忆条数与排序不变；重启后有新的成功调用记录。
- **证据**：重启前后 `entries/list` 计数对比。

## V7 Dashboard 第三方视角

- **操作**：<http://127.0.0.1:8000/dashboard/home>，用 env 文件里的 token 登录。
- **预期**：能看到 V2–V4 的同一批条目与版本历史。
- **证据**：Dashboard 与 App 同屏截图，标注同一个 `entry_id`。

## 一键复现

```powershell
cd qoder
.\scripts\start.ps1
# 应用侧 V1–V7（M8 提供）
.\scripts\verify.ps1 -Scenario all
# 打开证据目录
explorer .\evidence\verify
```

`verify.ps1` 结束时打印"预期 vs 实际"对照表，并把 `summary.md` 写到证据目录，可直接作为提交材料里的验证样例附件。
