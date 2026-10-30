# 架构

## 1. 目标与非目标

**目标**：单机、零模型依赖、可离线运行的复习教练；记忆写入与读取都必须能在页面上、日志里被逐个核对。

**非目标**：多用户、云端同步、自动抓取论文、自动生成题目、接大模型自动批改。默认不配置任何模型与 API Key；embedding 只作为文档中一段可选说明。

## 2. 组件

```
浏览器 (127.0.0.1:8080)
  │  fetch JSON
  ▼
RecallCoach 应用 (Python 3.12 / FastAPI)
  ├─ api/        today · answer · session · replay · compare · evidence · health
  ├─ domain/     scheduler(SM-2) · scoring · memory_policy
  ├─ memory/     gateway(httpx) · audit(jsonl)
  ├─ store/      sqlite3 · schema.sql · seed 导入
  └─ web/        index.html + app.js + style.css（零构建）
  │                         │
  │ sqlite3                 │ HTTP + Bearer
  ▼                         ▼
app.db                PowerContext Server (127.0.0.1:8000)
事实与调度             记忆与上下文（SQLite 后端，/mcp，Dashboard）
```

## 3. 数据面边界

| | app.db | PowerContext |
|---|---|---|
| 内容 | 课程、卡片、作答流水、到期时间、会话 | 薄弱点、混淆、讲解偏好、进度、下次承诺 |
| 形态 | 结构化行，确定可重算 | 自然语言结论，可修订、可退休、带引用 |
| 生命周期 | 随课程存在 | 跨课程 / 跨学期 / 跨设备复用 |
| 谁读 | 调度器与统计 | 队列排序、讲解上下文、次日起点 |

判定规则：**能从流水算出来的进 app.db；需要"judgment"或需要人改的进记忆层。**

## 4. 一次会话的时序

1. `GET /api/today?personalization=on`
   - `store` 读取到期卡片；
   - `memory/search`（query 由课程 + 章节 + 知识点拼装，`limit=20`，`mode=auto`）→ 取 weakness / misconception 命中；
   - `context/prepare`（`assembly.sections=[{family:memory, limit:4}]`，`max_bytes=2000`）→ 教练备忘；
   - `scoring` 计算分数与拆解 → 返回队列。
2. `POST /api/answer` → 写 `answers`、更新 `schedule` → `memory_policy` 判定是否写入 → 返回决策与 `entry_id`。
3. `POST /api/session/end` → 写 1 条 `progress`（+ 可选 `commitment`）。
4. `POST /api/memory/{entry_id}/revise|retire` → 重算队列并返回顺序变化。

`personalization=off` 时：不调用 `search` / `prepare`，也不写任何记忆（纯 baseline），审计日志标记 `personalization:false`。这样 A/B 两列才可比。

## 5. app.db 结构

```sql
CREATE TABLE courses (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE kp (                       -- 知识点，两层共用的唯一键
  id TEXT PRIMARY KEY,                  -- db/ch6/mvcc-readview
  course_id TEXT NOT NULL REFERENCES courses(id),
  chapter TEXT NOT NULL,
  name TEXT NOT NULL
);

CREATE TABLE cards (
  id TEXT PRIMARY KEY,                  -- db-022
  course_id TEXT NOT NULL REFERENCES courses(id),
  kp_id TEXT NOT NULL REFERENCES kp(id),
  front TEXT NOT NULL, back TEXT NOT NULL,
  explanation TEXT NOT NULL,
  cause_options TEXT NOT NULL,          -- JSON 数组
  created_at TEXT NOT NULL
);

CREATE TABLE schedule (
  card_id TEXT PRIMARY KEY REFERENCES cards(id),
  due_at TEXT NOT NULL,
  interval_days INTEGER NOT NULL DEFAULT 1,
  ease REAL NOT NULL DEFAULT 2.5,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL REFERENCES cards(id),
  session_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  correct INTEGER NOT NULL,             -- 0/1
  confidence INTEGER NOT NULL,          -- 1..5
  latency_ms INTEGER NOT NULL,
  cause TEXT                            -- 概念混淆 / 计算失误 / 没学过 / 看错题
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY, course_id TEXT NOT NULL,
  started_at TEXT NOT NULL, ended_at TEXT, mode TEXT NOT NULL  -- normal | ab_off
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- 记忆条目的指针缓存（正文在 PowerContext，这里只存修订/退休所需的 citation）
CREATE TABLE memory_refs (
  entry_id TEXT PRIMARY KEY,
  citation_json TEXT NOT NULL,
  kp_id TEXT, kind TEXT, written_at TEXT
);
```

迁移：`PRAGMA user_version` + `store/schema.sql`。

## 6. 内部 REST 契约

| 方法 | 路径 | 请求 | 响应要点 |
|---|---|---|---|
| GET | `/api/today` | `?personalization=on\|off` | `items[{card, score_breakdown, memory_hits[], coach_note}]`, `mode` |
| POST | `/api/answer` | `{card_id, correct, confidence, cause?, latency_ms}` | `schedule`, `memory_decision{action:write\|revise\|skip, entry_id?, reason}` |
| POST | `/api/session/end` | `{session_id}` | `progress_entry_id`, `next_start` |
| GET | `/api/replay` | `?kind=` | `entries[{entry_id, kind, text, version, state, ts, hit_cards[]}]` |
| POST | `/api/memory/{entry_id}/revise` | `{text, reason?}` | 新 version + 重排后的 `items` |
| POST | `/api/memory/{entry_id}/retire` | `{reason?}` | `state:"retired"` + 重排后的 `items` |
| GET | `/api/compare` | `?limit=5` | `on[]`, `off[]`, `diff[]` |
| GET | `/api/evidence` | — | 审计条目 + `report_markdown` |
| GET | `/api/health` | — | `pc_ready`, `capabilities`, `scope_id`, `cards`, `memories`, `audit_path` |

错误约定：PowerContext 不可用时，`/api/today` 返回 `degraded:true` 与 `mode:"offline"`，队列退化为纯到期排序，页面顶部显示"记忆离线"横幅——**不伪造记忆**。

## 7. 打分公式

```
base     = 0.5 * due_score                    # 0..1，SM-2 归一
weak     = 1.2 * hit(weakness, kp)
miscon   = 1.0 * hit(misconception, kp)
progress = 0.3 * hit(progress, chapter)
fatigue  = -0.4 * answers_today(kp)           # 当天已练过则降权
score    = base + weak + miscon + progress + fatigue
```

`hit()` 为 0/1：该 kp 是否被本次 `memory/search` 命中。`score_breakdown` 必须逐项返回，页面直接展示。

## 8. 证据埋点

1. `memory_audit.jsonl`：一行一次调用，字段见 [memory-design.md](memory-design.md) 第 7 节。
2. 页面上的记忆条目显示 `entry_id`、version、时间、命中卡片。
3. `/api/evidence` 导出 `verification-report.md`：环境快照、`capabilities`、记忆清单、A/B 差异、20 条问答记录。
4. `/api/compare` 常驻，随时可复现"记忆改变了排序"。
5. Dashboard 与 App 同屏截图，作为第三方视角。

## 9. 目录职责

| 路径 | 职责 | 谁产出 |
|---|---|---|
| `docs/` | spec 与验证 | 已完成 |
| `seed/` | 28 张卡 | 已完成 |
| `scripts/` | 启动、停止、技术层验证 | 已完成（应用相关目标随代码补齐） |
| `app/src/recallcoach/**` | 业务代码 | Qoder（M1–M7） |
| `app/tests/**` | 单测 | Qoder（与功能同 commit） |
| `site/` | 展示站 | Qoder（M9） |
