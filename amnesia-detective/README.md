# 失忆侦探 · The Amnesiac

用 Dify + PowerContext 做的一个小游戏：你和一位**每次醒来都会失忆**的侦探一起破案。
他记得的只有 PowerContext 替他保管的案情记忆。

## 为什么好玩

- 侦探的"失忆"不是设定，而是 Dify 每个新会话的真实状态；
- 他能接上上一轮的线索，是因为 `recall` 真的从记忆库里检索；
- 玩家可以故意提旧线索，看侦探是否想得起来——记忆成了玩法本身。

## 结构

```
Dify Chatflow
  ├─ 工具 recall(case_id, query)   → 取回相关线索
  ├─ LLM 推理
  └─ 工具 remember(case_id, text)  → 存入本轮线索
        ↕
FastAPI（本地）  →  PowerContext Runtime（SQLite）
```

## 本机运行

在 PowerContext 仓库根目录准备好环境后：

```bash
# 1. 安装依赖（用仓库已有虚拟环境）
uv pip install --python .venv/Scripts/python.exe fastapi uvicorn

# 2. 指定产品源码和数据库，启动服务
export PYTHONPATH="<仓库路径>/src"
export AMNESIA_DB="sqlite+aiosqlite:///<仓库路径>/amnesia.db"
cd amnesia-detective
<仓库路径>/.venv/Scripts/python.exe -m uvicorn app:app --port 8077
```

自检：

```bash
curl -X POST http://127.0.0.1:8077/remember \
  -H "Content-Type: application/json" \
  -d '{"case_id":"red-lantern","text":"The victim wore a red lantern pin."}'

curl -X POST http://127.0.0.1:8077/recall \
  -H "Content-Type: application/json" \
  -d '{"case_id":"red-lantern","query":"lantern pin","limit":5}'
```

## 让 Dify 能访问

Dify Cloud 需要一个公网 HTTPS 地址：

```bash
cloudflared tunnel --url http://127.0.0.1:8077
# 输出形如 https://xxxx.trycloudflare.com
```

若 Dify 跑在本机 Docker 里，工具地址直接用 `http://host.docker.internal:8077`。

## 在 Dify 里配置

1. **工具 → 自定义工具 → 导入 OpenAPI**
   - Schema URL：`https://<你的隧道域名>/openapi.json`
   - 鉴权：无
   - 导入后会出现 `remember` 和 `recall` 两个工具
2. **新建 Chatflow**，节点顺序：
   - Start（输入 `case_id`、`question`）
   - Tool `recall`（case_id = start.case_id，query = start.question）
   - LLM（System Prompt 见下，把 recall 结果拼进上下文）
   - Tool `remember`（case_id = start.case_id，text = question + 侦探本轮结论）
   - Answer
3. **System Prompt**：

```
你是一位失忆的侦探。你只记得「已知线索」里列出的内容，其他一律声称想不起来。

规则：
1. 先复述你想起的线索，再推理；
2. 不能编造线索里没有的事实；
3. 如果线索不足以下结论，就直说要再找什么线索；
4. 语气克制，像在雨夜翻旧案卷。
```

## 三分钟演示脚本

1. 新会话输入：`案子 the-red-lantern。第一条线索：死者戴着红灯笼胸针。`
2. 关掉窗口，**新建一个会话**（模拟失忆），只输入：`你还记得红灯笼胸针吗？`
   → 侦探能复述出来，说明记忆跨会话生效。
3. 继续加两条线索（港口的小提琴声、11 点）。
4. 输入：`现在，谁最可疑？` → 侦探把三条线索串起来推理。
5. 收尾话术：*"他不是记住了，他只是把记忆放在了 PowerContext 里。"*

## 提交材料草稿

> **作品名**：失忆侦探 · The Amnesiac
> **用了什么**：Dify Chatflow + 自定义工具；PowerContext 作为长期记忆层（`/remember`、`/recall`）。
> **创意点**：把"跨会话记忆"做成游戏机制——侦探每次醒来都失忆，玩家靠记忆库帮他还原案情。
> **体验方式**：Dify 应用链接 + 本地 `uvicorn` 服务（PowerContext 内置 Runtime，SQLite 持久化）。
> **可复现**：仓库内 `amnesia-detective/app.py` 与 README 说明完整。
