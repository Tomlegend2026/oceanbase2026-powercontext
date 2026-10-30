# 运行说明（RUNBOOK）

从零到可以复习的完整步骤。主路径面向 Windows 10/11，PowerShell 5.1 与 7 均可运行（脚本以 UTF-8 BOM 保存，5.1 不会乱码）；macOS / Linux 的差异见第 8 节。

> 脚本已通过 PowerShell 解析校验，但**尚未在本机对真实 Server 跑过一次**（本机未安装 uv/PowerContext）。首次执行若遇字段差异，`verify-powercontext.ps1` 会把原始请求与响应留在 `evidence/verify/<时间戳>/`，按第 9 节排查即可。

> 文档中的示例以 PowerShell 为准。PowerContext 官方文档里带 `export`/`unset` 的 Bash 示例不要直接粘进 PowerShell。

## 0. 前置

| 依赖 | 版本 | 检查 |
|---|---|---|
| Python | 3.11+（本项目用 3.12） | `python --version` |
| uv | 最新 | `uv --version` |
| Git | 任意 | `git --version` |

首次在本机跑脚本前，若提示脚本被禁止执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 1. 安装 PowerContext

```powershell
uv tool install --force "powercontext[cli,server]==1.2.0"
```

从源码装同一版本（可选）：

```powershell
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.2.0"
```

装完确认 `powercontext` 在 PATH 上：

```powershell
Get-Command powercontext
```

## 2. 生成受保护的环境文件

```powershell
cd qoder
.\scripts\bootstrap.ps1
```

脚本会：

1. 在 `%USERPROFILE%\.recallcoach\` 下生成 `powercontext.env`，包含**随机生成的** token；
2. 用 `icacls` 去掉继承权限，只保留当前用户、`SYSTEM`、`Administrators`；
3. 指定 `POWERCONTEXT_HOME=%USERPROFILE%\.recallcoach\data`，把服务端数据放在仓库之外；
4. 只打印文件路径，不打印 token。

想手工做，等价命令是：

```powershell
$dir = "$env:USERPROFILE\.recallcoach"; New-Item -ItemType Directory -Force $dir | Out-Null
$token = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
@"
POWERCONTEXT_SERVER_ACCESS_MODE=enforced
POWERCONTEXT_SERVER_AUTH_TOKEN=$token
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_HOME=$env:USERPROFILE\.recallcoach\data
"@ | Set-Content -Encoding utf8 "$dir\powercontext.env"
```

## 3. 启动并验证 PowerContext

先校验配置，再前台启动（**这个窗口要保持运行**）：

```powershell
powercontext config validate --env-file "$env:USERPROFILE\.recallcoach\powercontext.env"
powercontext server run --env-file "$env:USERPROFILE\.recallcoach\powercontext.env"
```

另开一个窗口做检查，这三条输出就是第一批证据，建议截图：

```powershell
$env:POWERCONTEXT_CLIENT_SERVER_URL = "http://127.0.0.1:8000"
$env:POWERCONTEXT_CLIENT_API_TOKEN  = "<从 env 文件里取 POWERCONTEXT_SERVER_AUTH_TOKEN>"
powercontext ready
powercontext capabilities
powercontext doctor
```

预期：

- `ready` 的数据库 / Runtime 状态正常；**没有配置模型时**检索能力只有 `auto, fts`，这是设计选择，不是故障。
- `capabilities` 里出现 `auto, fts`；没有 `vector, hybrid` 属正常。
- Dashboard 可登录：<http://127.0.0.1:8000/dashboard/home>，用同一个 token。

## 4. 技术层验证（不依赖应用）

应用还没跑起来也能验证"记忆确实在读写"：

```powershell
cd qoder
.\scripts\verify-powercontext.ps1
```

脚本会新建一个独立的验证 scope（`recallcoach-verify-<时间戳>`），写入两条记忆、做检索、组装上下文，把每一步的请求与响应写到 `evidence/verify/<时间戳>/`，最后打印 PASS/FAIL 表。它不会碰你个人的 scope。

## 5. 安装并初始化应用

> 应用代码由 Qoder 按 [docs/qoder-prompts.md](docs/qoder-prompts.md) 产出（M1 起）。此节在 M1 完成后可用。

```powershell
cd qoder\app
uv sync
Copy-Item ..\deploy\app.env.example .env
notepad .env        # 填 POWERCONTEXT_TOKEN，scope 留空则由应用自动创建
uv run python -m recallcoach.cli init-db --seed ..\seed\db-systems.cards.json
uv run python -m recallcoach.cli doctor
```

`doctor` 应打印：Server 地址、`ready` 状态、检索模式、scope id、app.db 卡片数、记忆条数、审计文件路径。

## 6. 启动应用

```powershell
uv run uvicorn recallcoach.main:app --port 8080
```

打开 <http://127.0.0.1:8080>。

或用一键脚本（会先确保 PowerContext 已就绪）：

```powershell
cd qoder
.\scripts\start.ps1
```

停止：

```powershell
.\scripts\stop.ps1
```

## 7. 重置与清理

| 目的 | 操作 |
|---|---|
| 只重置应用数据 | 删除 `app\.runtime\app.db`，重跑 `init-db` |
| 重置全部记忆 | 换一个新 scope（`app\.env` 里清空 `POWERCONTEXT_SCOPE_ID`），或删 `%USERPROFILE%\.recallcoach\data` 后重启 Server |
| 验证不污染个人数据 | 始终用 `verify-powercontext.ps1` 或 `RECALLCOACH_SCOPE_TITLE` 指定的独立 scope |
| 彻底重装 | 删 `%USERPROFILE%\.recallcoach`，重跑第 1–3 节 |

## 8. macOS / Linux 差异

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
powercontext config validate --env-file ~/.recallcoach/powercontext.env
chmod 600 ~/.recallcoach/powercontext.env          # 代替 icacls
powercontext server run --env-file ~/.recallcoach/powercontext.env
```

其余步骤相同，把 `.\scripts\*.ps1` 换成 `bash scripts/*.sh`（脚本在 M8 一并补齐）。

## 9. 故障排查

| 现象 | 优先检查 |
|---|---|
| `powercontext` 不是可识别命令 | 重开终端；或 `uv tool update-shell` |
| 请求返回 401 | 应用 `.env` 里的 token 与 `powercontext.env` 不一致 |
| `ready` 显示 `degraded` | 未配置模型时的预期表现；若数据库项异常才是真故障，看 Server 窗口日志 |
| 端口被占用 | `Get-NetTCPConnection -LocalPort 8000` / `-LocalPort 8080`，换端口或结束占用进程 |
| 中文检索召回为空 | 确认条目已写入（`entries/list`）；把 query 换成记忆文本里出现过的词；详见 [memory-design.md](docs/memory-design.md) 第 6 节 |
| `icacls` 报错 | 文件 owner 不是当前用户时先改 owner，再重试 |
| 应用启动报缺模块 | 在 `app\` 下执行 `uv sync`，不要用系统 Python |
| 换机器后记忆为空 | scope 不同或 `POWERCONTEXT_HOME` 指向了别的目录 |
