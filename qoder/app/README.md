# RecallCoach 应用

数据库系统复习教练。后端 FastAPI + SQLite，前端零构建静态页面，记忆层走 PowerContext HTTP API。

## 目录

```
app/
├─ pyproject.toml
├─ src/recallcoach/
│  ├─ config.py        # .env / 环境变量
│  ├─ db.py            # schema、种子导入、查询
│  ├─ scheduler.py     # SM-2
│  ├─ scoring.py       # 到期分 + 记忆加权
│  ├─ policy.py        # 记忆写入策略（纯函数）
│  ├─ pc.py            # PowerContext 网关 + 审计
│  ├─ service.py       # 编排
│  ├─ api.py           # FastAPI 路由
│  ├─ cli.py           # init-db / doctor
│  ├─ main.py          # ASGI 入口
│  └─ web/             # index.html / app.js / style.css
└─ tests/              # 单测 + 假 PowerContext（联调用）
```

## 开发

```powershell
cd qoder\app
uv sync
uv run pytest -q
```

## 不接真实 Server 的联调

```powershell
# 终端 A：假的 PowerContext（仅实现用到的接口，形状与真实一致）
uv run python tests/fake_pc_server.py --port 8099

# 终端 B：应用指向假服务
$env:POWERCONTEXT_BASE_URL="http://127.0.0.1:8099"
$env:POWERCONTEXT_TOKEN="fake-token"
$env:RECALLCOACH_DB=".runtime/e2e/app.db"
$env:RECALLCOACH_AUDIT=".runtime/e2e/audit.jsonl"   # 基名；实际按天滚动成 audit-YYYYMMDD.jsonl
$env:RECALLCOACH_TODAY="2026-11-01"
uv run uvicorn recallcoach.main:app --port 8080
```

假服务只用于 UI 与流程联调；提交材料的验证必须跑真实 PowerContext（见 [../docs/verification.md](../docs/verification.md)）。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | PowerContext 连通性、capabilities、scope、卡片与记忆计数 |
| GET | `/api/today` | 今日队列（含分数拆解、命中 entry_id、教练备忘） |
| POST | `/api/answer` | 作答 + 调度 + 记忆策略 |
| POST | `/api/session/end` | 会话结束，写进度叙事 |
| GET | `/api/replay` | 记忆时间线 |
| POST | `/api/memory/{id}/revise` \| `/retire` | 改写 / 标记已掌握 |
| GET | `/api/compare` | 个性化 vs 基线排序 |
| GET | `/api/evidence` | 审计日志 + Markdown 报告 |
