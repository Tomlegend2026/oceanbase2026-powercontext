from datetime import datetime

from recallcoach import db as dbmod

NOW = datetime(2026, 11, 1, 9, 0, 0)


def course(service):
    return dbmod.course_id(service.conn)


def test_reuse_session_keeps_the_same_round(service):
    first = dbmod.reuse_session(service.conn, course(service), "normal", NOW)
    again = dbmod.reuse_session(service.conn, course(service), "normal", NOW)
    assert again["id"] == first["id"], "重复刷新今日队列不该凭空多出一轮会话"
    assert dbmod.session_mode(service.conn, first["id"]) == "normal"


def test_mode_switch_closes_the_previous_round(service):
    on = dbmod.reuse_session(service.conn, course(service), "normal", NOW)
    off = dbmod.reuse_session(service.conn, course(service), "ab_off", NOW)
    assert off["id"] != on["id"], "对照组与实验组不能混在同一个 session"
    ended = service.conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (on["id"],)).fetchone()
    assert ended["ended_at"]


def test_unknown_session_has_no_mode(service):
    assert dbmod.session_mode(service.conn, "s-does-not-exist") is None


def test_switching_back_starts_a_new_round(client, gateway):
    """界面开关从 on 切到 off：新一轮会话是 ab_off，答题不再写记忆。"""
    on = client.get("/api/today").json()
    for confidence in (1, 2):
        client.post(
            "/api/answer",
            json={"card_id": "db-022", "correct": False, "confidence": confidence, "session_id": on["session_id"]},
        )
    assert len(gateway.store) == 1, "on 组答错两次应写下 1 条薄弱点"
    gateway.calls.clear()

    off = client.get("/api/today?personalization=off").json()
    assert off["mode"] == "off"
    assert off["session_id"] != on["session_id"]

    for confidence in (1, 2):
        result = client.post(
            "/api/answer",
            json={"card_id": "db-022", "correct": False, "confidence": confidence, "session_id": off["session_id"]},
        ).json()
        assert result["personalization"] is False

    assert len(gateway.store) == 1, "off 组继续答错不该再写新的记忆"
    assert [call[0] for call in gateway.calls] == []
