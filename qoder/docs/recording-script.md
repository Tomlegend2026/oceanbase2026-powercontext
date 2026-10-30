# 录屏与截图脚本

四段录屏 + 每里程碑 1–2 张截图，是「Qoder 开发可核对」这一项的全部材料。按镜头拍，别即兴。

## 通则

- 分辨率 1280×720 足够；窗口里**同时**出现 Qoder 对话与代码/运行结果。
- 开头 3 秒停在能看清账号/项目的位置；结尾停在可验证的输出上（日志、条目 id、队列顺序）。
- 每段不超过 4 分钟；旁白可以没有，但**字幕或鼠标停顿**要指向关键行。
- 拍完立刻改名 `record-0X-主题.mp4` 放进 `evidence/qoder/`。
- 拍之前清理：终端历史里的 token、含用户名的路径、浏览器其他标签页。

## record-01 环境打通（M1）

| 镜头 | 画面 | 要点 |
|---|---|---|
| 1 | 终端：`powercontext server run --env-file …` | 停 2 秒，让人看到监听地址 |
| 2 | 另开窗口：`powercontext ready` / `capabilities` | 用鼠标划出 `auto, fts` 那一行 |
| 3 | Qoder：`uv run python -m recallcoach.cli doctor` | 六项全绿 |
| 4 | Qoder 会话里一次真实的 `remember` 调用 | 停在返回的 `entry_id` 上 |
| 5 | `evidence/` 里的 `memory_audit.jsonl` 尾部 | 展示 `request_id` 字段 |

一句话结论（字幕）：**记忆服务在本机跑通，每次调用都有请求 ID 可回溯。**

## record-02 记忆不滥用（M4，最关键）

| 镜头 | 画面 | 要点 |
|---|---|---|
| 1 | 应用：答错 `db-022`，自评 1 | toast 出现「本次未写入」或跳过理由 |
| 2 | 同一个 scope 的 `entries/list` | 条目数没变 |
| 3 | 第二次答错同一张卡 | toast「已记下：…」，露出 `entry_id` |
| 4 | `entries/list` | 只多了 1 条，version=1 |
| 5 | 第三次答错 | 停在同一 `entry_id`、version=2 |
| 6 | `memory_audit.jsonl` 三条决策 | `skip` → `write` → `revise` |

一句话结论：**记忆按阈值写入，不重复堆积；每次决策都有留痕。**

## record-03 改了记忆，排序就变（M6）

| 镜头 | 画面 | 要点 |
|---|---|---|
| 1 | Today 页，`db-022` 排第 1 | 高亮它的分数拆解 |
| 2 | Replay 页，编辑那条 weakness | 改一句措辞，保存 |
| 3 | 回到 Today | 顺序变化，version 变成 2 |
| 4 | 点「已掌握」 | 该卡回落，`entries/list` 显示 retired |
| 5 | 切到 `personalization=off` | 两列并排，差异一目了然 |

一句话结论：**记忆不是装饰，人改它，出题顺序当场变。**

## record-04 一条命令复现（M8）

| 镜头 | 画面 | 要点 |
|---|---|---|
| 1 | 终端：`.\scripts\verify.ps1 -Scenario all` | 让它跑，别剪太快 |
| 2 | 末尾「预期 vs 实际」表 | 停在 PASS 那几行 |
| 3 | `explorer .\evidence\verify` | 展示产物目录 |
| 4 | 打开 `summary.md` | 停在 scope_id 与时间戳 |

一句话结论：**评审不用问我，一条命令即可复现全部样例。**

## 每里程碑截图清单

| 里程碑 | 截什么 | 文件 |
|---|---|---|
| M0 | 目录树 + `git log --oneline` | `M00-scaffold.png` |
| M1 | `doctor` 输出 | `M01-doctor.png` |
| M2 | 题库导入 + `SELECT count(*)` | `M02-seed.png` |
| M3 | 队列页与小结页 | `M03-flow.png` |
| M4 | 写入决策与 toast | `M04-policy.png` |
| M5 | on/off 两列对照 | `M05-ranking.png` |
| M6 | Replay 编辑前后 | `M06-replay.png` |
| M7 | Evidence 页与导出的报告 | `M07-evidence.png` |
| M8 | verify 汇总表 | `M08-verify.png` |
| M9 | Sites 发布页 + 无痕打开 | `M09-site.png` |
