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
    assert first["decision"]["action"] == "skip"
    assert first["memory"]["status"] == "skipped"

    second = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 2},
    ).json()
    assert second["decision"]["action"] == "write"
    assert second["memory"]["status"] == "written"
    entry_id = second["memory"]["entry_id"]

    third = client.post(
        "/api/answer",
        json={"card_id": "db-022", "correct": False, "confidence": 2},
    ).json()
    assert third["decision"]["action"] == "revise"
    assert third["memory"]["status"] == "revised"
    assert third["memory"]["entry_id"] == entry_id
    assert third["memory"]["version"] == 2

    replay = client.get("/api/replay").json()
    assert len(replay["entries"]) == 1
    assert replay["entries"][0]["entry_id"] == entry_id


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
    assert result["memory"]["status"] == "skipped"
    assert gateway.store == {}


def test_session_end_writes_progress(client, gateway):
    today = client.get("/api/today").json()
    session_id = today["session_id"]
    client.post("/api/answer", json={"card_id": "db-022", "correct": True, "confidence": 4, "session_id": session_id})
    client.post("/api/answer", json={"card_id": "db-021", "correct": False, "confidence": 1, "session_id": session_id})

    summary = client.post("/api/session/end", json={"session_id": session_id}).json()
    assert summary["total"] == 2
    assert summary["correct"] == 1
    assert summary["memory"]["status"] == "written"
    kinds = [entry["kind"] for entry in gateway.store.values()]
    assert "progress" in kinds


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
