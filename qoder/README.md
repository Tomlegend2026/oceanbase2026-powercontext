# RecallCoach · 数据库系统复习教练

把"看懂了但一考就错"的数据库系统知识点，变成会记住你错在哪、并按薄弱点排课的本地复习教练。

## 解决的痛点

- 范式判断、EXPLAIN 读法、隔离级别现象这类题，读教材时都懂，一周后全糊。
- 错题散在纸上、截图和聊天记录里；复习时只能重读教材，说不清到底哪几个点没掌握。
- 通用刷题 App 不认识你：它不知道你已经在 MVCC read view 上翻车第三次。

## 核心功能

| # | 功能 | 说明 |
|---|---|---|
| 1 | 今日队列 | SM-2 到期分 + 记忆加权，按知识点薄弱程度排序 |
| 2 | 答题与自评 | 更新调度；**同一知识点 7 天内二次失败才写入一条"薄弱点"**，第三次是修订而非新增 |
| 3 | 分数拆解 | 每张卡显示 `due / weakness / misconception / progress` 各项贡献与命中的记忆条目 id |
| 4 | 教练备忘 | 讲解区展示由 `context/prepare` 生成、带引用的历史备忘 |
| 5 | Replay | 记忆时间线，可改写（revise）或标记已掌握（retire），改完立即重排 |
| 6 | A/B 对照 | 关掉个性化即为纯到期排序，两列并排，差异即"记忆的作用" |
| 7 | Evidence | 每次记忆调用的审计日志（含 PowerContext 请求 ID），可导出 Markdown 报告 |

## 数据面分工

| 面 | 存什么 | 位置 |
|---|---|---|
| 事实与调度 | 课程、卡片、作答流水、到期时间 | `app.db`（SQLite） |
| 判断与叙事 | 薄弱点、混淆、讲解偏好、进度、下次承诺 | PowerContext 记忆条目（带 entry id / version / 引用） |

`app.db` 能回答"你什么时候答错了什么"；只有记忆层能回答"你**为什么**错、该怎么讲、上次停在哪"，并且这个判断可以被用户修改、被撤回、被下一个学期复用。

## 为什么不是普通刷题 App

1. **可修订**：用户能直接改一条记忆的措辞或把它标成"已掌握"，队列顺序当场变化。
2. **可核对**：每条记忆有 entry id、version 与写入时间；页面上标注它命中了哪张卡。
3. **可对照**：`personalization=off` 时不读也不写记忆，两列排序并排展示差异。

## 快速开始

完整步骤见 [RUNBOOK.md](RUNBOOK.md)。最短路径：

```powershell
# 1. 安装并启动 PowerContext（另开窗口保持运行）
uv tool install --force "powercontext[cli,server]==1.2.0"
.\scripts\bootstrap.ps1          # 生成受保护的 powercontext.env 与随机 token
.\scripts\start.ps1              # 起 Server → 等 ready → 起应用

# 2. 打开
#    应用      http://127.0.0.1:8080
#    Dashboard http://127.0.0.1:8000/dashboard/home
```

## 目录

```
qoder/
├─ README.md  RUNBOOK.md          # 总览与运行说明
├─ docs/
│  ├─ architecture.md             # 组件、边界、数据库、内部 API、打分公式
│  ├─ memory-design.md            # 记忆分类、写入策略、读取决策点、审计格式（Qoder 的 spec）
│  ├─ verification.md             # V1–V7 验证样例
│  ├─ qoder-prompts.md            # 可直接粘贴到 Qoder 的逐里程碑任务书
│  └─ qoder-log.md                # Qoder 开发记录索引 + 账号 UID
├─ app/                           # 应用（业务代码由 Qoder 按任务书产出）
├─ seed/db-systems.cards.json     # 28 张种子卡，7 章
├─ scripts/                       # bootstrap / start / stop / 技术层验证
├─ deploy/*.env.example           # 全占位配置
├─ site/                          # Qoder Sites 展示站
└─ evidence/                      # 截图、录屏、验证产物
```

## 项目状态

| 部分 | 状态 |
|---|---|
| 方案文档、记忆策略、验证样例 | ✅ 就绪 |
| 种子题库（28 卡 / 7 章 / 无重复 id） | ✅ 结构已校验 |
| 应用（`app/`）：后端 + 前端 + 测试 | ✅ 可运行，34 个测试通过 |
| 端到端验证脚本（V1–V7） | ✅ 真实 PowerContext 1.2.0 上 PASS 8 / WARN 1 / FAIL 0 |
| 展示站（`site/`） | ✅ 已含四张真实界面截图 |
| 使用效果材料 | 🚧 需要真实用户与截图 |

### 已验证 / 未验证

- **已验证**：种子文件可解析（28 卡、7 章、id 与 kp_id 唯一、章节引用有效）；全部 PowerShell 脚本零解析错误；提交基线为 `2026-competition-ecosystem` 的 `d647275c`；中文记忆可被检索（用仓库自身的 `analyze_text` + SQLite FTS5 复现，见 [memory-design.md](docs/memory-design.md) 第 6 节）；`pytest` 34 项通过；`scripts/verify.ps1` 在假 PowerContext 上跑完 V1–V6 全绿（V7 因假服务没有 Dashboard 记为 WARN）。
- **真实 Server 已跑通**：`uv tool install "powercontext[cli,server]==1.2.0"` 装上 1.2.0，技术层验证 9/9 通过（`mode=fts`，中文记忆可召回）；端到端 V1–V7 为 PASS 8 / WARN 1（V7 需人工登录 Dashboard）/ FAIL 0。\n- **未验证**：Dashboard 登录后的人工核对、Qoder Sites 发布、真实用户使用效果——这三项需要人的账号与真实使用。

## 验证

- 技术层：`.\scripts\verify-powercontext.ps1` —— 建独立 scope、写入两条记忆、检索、组装上下文，产物写入 `evidence/verify/<时间戳>/`。
- 端到端：`.\scripts\verify.ps1` —— 独立 scope + 独立数据库 + 8082 端口的隔离实例，跑 V1–V7 并出 `summary.md`。
- 手工步骤与预期值：[docs/verification.md](docs/verification.md)。

## 开发过程与 Qoder

过程记录见 [docs/qoder-log.md](docs/qoder-log.md)（含 Qoder 账号 UID 与逐里程碑证据索引），任务书见 [docs/qoder-prompts.md](docs/qoder-prompts.md)。

坦白说明：本仓库的方案、种子数据与应用初版由 AI 助手先行完成；Qoder 接手的是"接真实 Server 修兼容、补 E2E、做站点"这一轮，记录按实际发生的内容写，不写"从零生成"。

## 来源与许可

- 记忆与上下文能力：[PowerContext](https://github.com/oceanbase/powercontext) `1.2.0`（许可证见上游仓库 `LICENSE`）。
- 种子题库为本项目自写，未复用第三方题库；题目覆盖关系模型、SQL、范式、索引、查询优化、事务、恢复七章。
