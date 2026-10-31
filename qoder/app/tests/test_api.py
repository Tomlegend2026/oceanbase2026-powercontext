from datetime import date, datetime

from recallcoach import db as dbmod

TODAY = date(2026, 11, 1)


def test_health_reports_cards_and_scope(client):
    payload = client.get("/api/health").json()
    assert payload["cards"] == 28
    assert payload["pc_ready"] is True
    assert payload["scope_id"]
    assert payload["date"] == "2026-11-01"


def test_today_returns_ten_due_cards(client):
    payload = client.get("/api/today").json()
    assert payload["mode"] == "on"
    assert payload["degraded"] is False
    assert len(payload["items"]) == 10
    assert payload["session_id"]


def test_two_failures_write_one_memory_then_revise(client):
    first = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 1},
    ).json()
    assert first["decision"][0]["action"] == "skip"
    assert first["memory"][0]["status"] == "skipped"

    second = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 2},
    ).json()
    assert [item["action"] for item in second["decision"]] == ["write"]
    assert second["memory"][0]["status"] == "written"
    entry_id = second["memory"][0]["entry_id"]

    third = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 2},
    ).json()
    assert third["decision"][0]["action"] == "revise"
    assert third["memory"][0]["status"] == "revised"
    assert third["memory"][0]["entry_id"] == entry_id
    assert third["memory"][0]["version"] == 2

    replay = client.get("/api/replay").json()
    assert len(replay["entries"]) == 1
    assert replay["entries"][0]["entry_id"] == entry_id


def test_off_session_neither_reads_nor_writes_memory(client, gateway):
    """personalization=off 的会话是对照组：不 search、不 prepare、更不写记忆。"""
    today = client.get("/api/today?personalization=off").json()
    assert today["mode"] == "off"
    gateway.calls.clear()

    for confidence in (1, 2, 3):
        result = client.post(
            "/api/answer",
            json={"card_id": "db-022", "correct": False, "confidence": confidence, "session_id": today["session_id"]},
        ).json()
        assert result["personalization"] is False
        assert [item["reason"] for item in result["decision"]] == ["personalization-off"]
        assert [item["status"] for item in result["memory"]] == ["skipped"]

    assert gateway.store == {}, "off 会话不能留下任何记忆"
    assert [call[0] for call in gateway.calls] == [], "off 会话不该再调用记忆接口"

    summary = client.post("/api/session/end", json={"session_id": today["session_id"]}).json()
    assert summary["personalization"] is False
    assert summary["progress_entry_id"] is None
    assert gateway.store == {}


def test_confusion_cause_writes_weakness_and_misconception(client, gateway):
    """错因下拉确认了混淆对象时，同一个 kp 允许各存 1 条 active 薄弱点与混淆。"""
    cause = "概念混淆：把 read view 的创建时机与锁的持有范围混为一谈"
    first = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 1, "cause": cause},
    ).json()
    # 第 1 次：没过 lapses 阈值，只有用户确认过的混淆被记下
    assert [(item["action"], item["kind"]) for item in first["decision"]] == [("write", "misconception")]

    second = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 1, "cause": cause},
    ).json()
    # 第 2 次：薄弱点新增，混淆已存在 → 修订而不是再写一条
    assert [(item["action"], item["kind"]) for item in second["decision"]] == [
        ("write", "weakness"),
        ("revise", "misconception"),
    ]
    assert [item["status"] for item in second["memory"]] == ["written", "revised"]

    entries = client.get("/api/replay").json()["entries"]
    assert {entry["kind"] for entry in entries} == {"weakness", "misconception"}
    assert len(entries) == 2, "同一 kp 的两类记忆各 1 条，不重复堆积"



def test_first_confusion_writes_only_misconception(client, gateway):
    """混淆分支不套 lapses 阈值：第一次答错但填了混淆对象也写，只写这一条。"""
    result = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 1, "cause": "概念混淆：read view 与锁的范围"},
    ).json()
    assert [item["kind"] for item in result["decision"]] == ["misconception"]
    assert {entry["kind"] for entry in gateway.store.values()} == {"misconception"}



def test_memory_changes_ranking_and_retire_restores_it(client):
    for _ in range(2):
        client.post("/api/answer", json={"card_id": "db-022", "correct": False, "confidence": 1})

    compare = client.get("/api/compare?limit=5").json()
    assert compare["diff"], "存在薄弱点记忆时，个性化与基线应出现位次差异"
    ranked_on = [item["id"] for item in compare["on"]]
    assert ranked_on[0] == "db-022"

    entry_id = client.get("/api/replay").json()["entries"][0]["entry_id"]
    retired = client.post(f"/api/memory/{entry_id}/retire", json={"reason": "已掌握"}).json()
    assert retired["ok"] is True

    after = client.get("/api/compare?limit=5").json()
    assert after["diff"] == []


def test_revise_updates_version(client):
    for _ in range(2):
        client.post("/api/answer", json={"card_id": "db-022", "correct": False, "confidence": 1})
    entry_id = client.get("/api/replay").json()["entries"][0]["entry_id"]

    result = client.post(
        f"/api/memory/{entry_id}/revise",
        json={"text": "[kp:db/ch6/mvcc-readview] 只需要记住 read view 在首次快照读时创建。"},
    ).json()
    assert result["ok"] is True
    assert result["version"] == 2

    entries = client.get("/api/replay").json()["entries"]
    assert "首次快照读" in entries[0]["text"]


def test_revise_and_retire_return_reordered_queue(client):
    """改写/退休都要带回重排后的队列与位次变化，前端不必再自己请求一次。"""
    for _ in range(2):
        client.post("/api/answer", json={"card_id": "db-022", "correct": False, "confidence": 1})
    entry_id = client.get("/api/replay").json()["entries"][0]["entry_id"]

    revised = client.post(
        f"/api/memory/{entry_id}/revise",
        json={"text": "[kp:db/ch6/mvcc-readview] 仍然薄弱：read view 的创建时机。"},
    ).json()
    assert revised["ok"] is True
    assert [item["id"] for item in revised["items"][:1]] == ["db-022"], "改写后仍应保持首位"
    assert revised["moved"] == []

    retired = client.post(f"/api/memory/{entry_id}/retire", json={"reason": "已掌握"}).json()
    assert retired["ok"] is True
    assert retired["state"] == "retired"
    assert retired["items"][0]["id"] != "db-022", "退休后 db-022 应从前位回落"
    moved = {row["card_id"]: row for row in retired["moved"]}
    assert "db-022" in moved
    assert moved["db-022"]["before"] == 1



def test_offline_mode_degrades_without_writing(client, gateway):
    gateway.online = False
    today = client.get("/api/today").json()
    assert today["mode"] == "offline"
    assert today["degraded"] is True
    assert today["items"], "离线时仍应能按到期排序出题"

    result = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 1, "session_id": today["session_id"]},
    ).json()
    assert result["decision"][0]["action"] == "skip"
    assert [item["status"] for item in result["memory"]] == ["skipped"]
    assert gateway.store == {}


def test_two_offline_failures_still_do_not_write(client, gateway):
    """离线时答错两次也不写：等 Server 回来再按事实补，不伪造记忆。"""
    gateway.online = False
    for _ in range(2):
        client.post("/api/answer", json={"card_id": "db-022", "correct": False, "confidence": 1})
    assert gateway.store == {}


def test_session_end_writes_progress(client, gateway):
    today = client.get("/api/today").json()
    session_id = today["session_id"]
    client.post("/api/answer", json={"card_id": "db-022", "correct": True, "confidence": 4, "session_id": session_id})
    client.post("/api/answer", json={"card_id": "db-021", "correct": False, "confidence": 1, "session_id": session_id})

    summary = client.post("/api/session/end", json={"session_id": session_id}).json()
    assert summary["total"] == 2
    assert summary["correct"] == 1
    assert summary["memory"][0]["status"] == "written"
    assert summary["progress_entry_id"] == summary["memory"][0]["entry_id"]
    kinds = [entry["kind"] for entry in gateway.store.values()]
    assert "progress" in kinds


def test_session_end_reports_next_start(client):
    """小结要能回答"下次从哪张开始"：优先本轮没答对的那张。"""
    today = client.get("/api/today").json()
    session_id = today["session_id"]
    answered = [item["id"] for item in today["items"][:2]]
    client.post("/api/answer", json={"card_id": answered[0], "correct": True, "confidence": 5, "session_id": session_id})
    client.post("/api/answer", json={"card_id": answered[1], "correct": False, "confidence": 1, "session_id": session_id})

    summary = client.post("/api/session/end", json={"session_id": session_id}).json()
    assert summary["next_start"] == answered[1]

    progress_text = summary["memory"][0]["decision"]["text"]
    assert f"下次从 {answered[1]} 开始" in progress_text



def test_evidence_report_mentions_memory(client):
    for _ in range(2):
        client.post("/api/answer", json={"card_id": "db-022", "correct": False, "confidence": 1})
    payload = client.get("/api/evidence").json()
    assert "RecallCoach 证据报告" in payload["report_markdown"]
    assert "db-022" in payload["report_markdown"]


def test_lapses_window_counts_only_recent(service):
    now = datetime(2026, 11, 1, 9, 0, 0)
    dbmod.add_answer(
        service.conn,
        card_id="db-022",
        session_id="s-old",
        correct=False,
        confidence=1,
        latency_ms=0,
        cause=None,
        now=datetime(2026, 9, 1, 9, 0, 0),
    )
    recent = dbmod.lapses_in_window(service.conn, "db/ch6/mvcc-readview", now.replace(day=26), now)
    assert recent == 0
