# 从这里开始（需要你做的事）

按顺序做，每步都有"预期结果"和"失败怎么办"。全程约 40 分钟，其中 Qoder 与发布环节约 20 分钟。

路径约定：

```powershell
cd C:\Users\W\Desktop\oceanbase2026-powercontext\.qoder-wt\qoder
```

---

## 第 0 步：放行脚本（每个新窗口一次）

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

预期：无输出。失败：如果提示被组策略锁定，改用 `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1`。

---

## 第 1 步：装 uv

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

预期：安装完成后**重开一个终端**，`uv --version` 有版本号。

失败：`uv` 仍找不到时执行 `uv tool update-shell`，或重开终端。

---

## 第 2 步：装 PowerContext 并生成配置

```powershell
cd C:\Users\W\Desktop\oceanbase2026-powercontext\.qoder-wt\qoder
uv tool install --force "powercontext[cli,server]==1.2.0"
.\scripts\bootstrap.ps1
```

预期：

- 出现 `已生成环境文件：C:\Users\W\Desktop\oceanbase2026-powercontext\...` 之外的 `%USERPROFILE%\.recallcoach\powercontext.env`；
- 出现 `已生成 app/.env（token 已填入）`；
- 打印"Token：已隐藏"。

失败：

| 现象 | 处理 |
|---|---|
| `未找到 powercontext` | 重开终端；或把 `uv` 的 bin 目录加进 PATH |
| `icacls` 报错 | 文件 owner 不是当前用户，跳过不影响使用 |

---

## 第 3 步：启动 PowerContext（这个窗口要一直开着）

```powershell
$env:PCENV = "$env:USERPROFILE\.recallcoach\powercontext.env"
powercontext config validate --env-file $env:PCENV
powercontext server run --env-file $env:PCENV
```

预期：打印监听地址与 `127.0.0.1:8000`，进程不退出。

失败：端口占用时 `Get-NetTCPConnection -LocalPort 8000` 找占用进程。

---

## 第 4 步：另开一个终端，验证技术层

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
cd C:\Users\W\Desktop\oceanbase2026-powercontext\.qoder-wt\qoder
.\scripts\verify-powercontext.ps1
```

预期：结尾 `PASS 6+ / WARN 0-2 / FAIL 0`，并打印证据目录。

失败：把屏幕输出整段复制给我（脚本会把原始响应留在 `evidence\verify\<时间戳>\`）。

**这一步很关键**：它证明中文记忆能写入、能检索、能组装上下文。

---

## 第 5 步：装应用依赖并初始化

```powershell
cd app
uv sync
uv run python -m recallcoach.cli init-db --seed ..\seed\db-systems.cards.json
uv run python -m recallcoach.cli doctor
```

预期：

- `init-db` 打印 `"cards": 28`；
- `doctor` 六行全部正常：`Server 就绪: 是`、`检索模式: auto, fts`、`scope: ...`、`卡片数: 28`。

失败：

| 现象 | 处理 |
|---|---|
| `doctor` 显示 `Server 就绪: 否` | 第 3 步的窗口没在跑 |
| `401` | `app\.env` 里的 token 与 `powercontext.env` 不一致，重跑 `bootstrap.ps1 -Force` |
| 报缺模块 | 确认在 `app\` 目录下执行 `uv sync` |

---

## 第 6 步：启动应用，自己刷一轮

```powershell
uv run uvicorn recallcoach.main:app --port 8080
```

打开 <http://127.0.0.1:8080>：

1. 今日队列应出现 10 张卡；
2. 点任意卡 → 显示答案 → 选"忘了"；
3. 对 `db-022`（MVCC read view）连续答错 **3 次**，观察右下角提示：第 2 次"已记下"，第 3 次"已修订"；
4. 切到「我记得你」，能看到条目，改一句话保存，再点"已掌握"；
5. 切到「对照」，两列顺序不同；
6. 切到「证据」，能看到每次调用的 `request_id`。

---

## 第 7 步：端到端验证（自动跑 V1–V7）

```powershell
cd C:\Users\W\Desktop\oceanbase2026-powercontext\.qoder-wt\qoder
.\scripts\verify.ps1
```

预期：`PASS 8 / WARN 1 / FAIL 0`（WARN 是 V7，需要人工登录 Dashboard 核对）。

失败：把输出复制给我。若只是 V7 的 WARN，属于正常。

---

## 第 8 步：截四张图

```powershell
.\scripts\screenshots.ps1
```

预期：`site\assets\` 下出现 4 张 PNG。

失败：用 `Win+Shift+S` 手工截 `#today`、`#replay`、`#compare`、`#evidence` 四个页签，存成同名文件。

然后把 `site\index.html` 里 `#gallery` 的三个占位块换成：

```html
<figure class="shot">
  <img src="assets/today.png" alt="今日队列，卡片显示分数拆解与命中的 entry_id">
  <figcaption>今日队列：分数拆解与记忆命中</figcaption>
</figure>
```

---

## 第 9 步：Qoder 三个会话（这是评分关键）

在 Qoder 里打开 `qoder/` 目录，每个会话先粘"通用前置"，再粘任务。任务原文在 [qoder-prompts.md](qoder-prompts.md) 的「第二轮任务书」。

| 会话 | 任务 | 结束后必留 |
|---|---|---|
| Q1 | 接真实 Server，逐个接口核对响应并修兼容 | 录屏或截图：`doctor` 输出 + 一次真实 `remember` 响应 |
| Q2 | 补 E2E 测试，`pytest` 一次通过 | 截图：测试全绿 |
| Q3 | 按现有截图发布站点 | 截图：Sites 发布页 + 无痕窗口打开 |

同时做三件事：

1. **截 UID**：Qoder 账号/设置页 → 存 `evidence\qoder\uid.png`，并把 UID 填进 [qoder-log.md](qoder-log.md)。
2. **补记录表**：每完成一个会话，在 qoder-log 的表格里补一行（时间 / Qoder 承担 / 人工介入 / 证据文件 / commit）。
3. **别写"从零生成"**：如实写初版来源与 Qoder 接手的环节。

---

## 第 10 步：发布站点

1. Qoder Sites → 选 `qoder/site` → 发布。
2. 可见范围「任何人都可访问」（**必须个人账号**，企业版设不了公开）。
3. 无痕窗口打开确认。
4. 把网址记进 [submission-checklist.md](submission-checklist.md)。

---

## 第 11 步：代码仓库

```powershell
cd C:\Users\W\Desktop\oceanbase2026-powercontext\.qoder-wt
git log --oneline -5           # 确认提交都在 qoder/recallcoach 分支
git remote -v                  # 换成你自己的 fork/私有仓
git push -u origin qoder/recallcoach
```

预期：远端出现该分支。私有仓要给评审开放读取权限。

**推送前自查**：

```powershell
git diff --cached --name-only | Select-String -Pattern "\.env$|\.db$|\.runtime"
```

有输出就是误提交，先移除。

---

## 第 12 步：提交

在平台填：站点网址 + 仓库地址 + Qoder UID，上传 `evidence\` 里的材料，最后用无痕窗口再开一次站点。

提交后到评审结束前：不删站点；改内容要重新点「发布」。

---

## 需要我接手的信号

把下面任意一项发我，我接着改：

- 第 4 步或第 7 步的完整输出（含报错栈）；
- `doctor` 的输出；
- Qoder 会话里遇到的接口差异（真实响应 JSON 片段）；
- 站点截图，我看文案与排版是否需要调整。
