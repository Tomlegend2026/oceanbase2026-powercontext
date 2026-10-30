from datetime import date

from recallcoach.scoring import rank

TODAY = date(2026, 11, 1)


def card(card_id: str, kp: str, chapter: str) -> dict:
    return {"id": card_id, "kp_id": kp, "chapter": chapter, "kp_name": kp, "due_at": "2026-11-01"}


CARDS = [
    card("db-001", "db/ch1/fk", "db/ch1"),
    card("db-002", "db/ch1/projection", "db/ch1"),
    card("db-003", "db/ch1/natural-join", "db/ch1"),
    card("db-022", "db/ch6/mvcc-readview", "db/ch6"),
]


def test_without_hits_order_follows_due_then_id():
    ranked = rank(CARDS, hits=[], answers_today={}, today=TODAY)
    assert [item["id"] for item in ranked] == ["db-001", "db-002", "db-003", "db-022"]
    assert all(item["breakdown"]["weakness"] == 0 for item in ranked)


def test_weakness_hit_moves_card_to_top():
    hits = [{"entry_id": "e0001", "kind": "weakness", "kp_id": "db/ch6/mvcc-readview"}]
    ranked = rank(CARDS, hits=hits, answers_today={}, today=TODAY)
    assert ranked[0]["id"] == "db-022"
    assert ranked[0]["breakdown"]["weakness"] == 1.2
    assert ranked[0]["memory_hits"][0]["entry_id"] == "e0001"
    assert ranked[0]["score"] == 1.3


def test_progress_hit_boosts_matching_chapter():
    hits = [{"entry_id": "e0002", "kind": "progress", "kp_id": "db/ch6"}]
    ranked = rank(CARDS, hits=hits, answers_today={}, today=TODAY)
    by_id = {item["id"]: item for item in ranked}
    assert by_id["db-022"]["breakdown"]["progress"] == 0.3
    assert by_id["db-001"]["breakdown"]["progress"] == 0


def test_practiced_today_applies_fatigue():
    ranked = rank(CARDS, hits=[], answers_today={"db/ch1/fk": 2}, today=TODAY)
    by_id = {item["id"]: item for item in ranked}
    assert by_id["db-001"]["breakdown"]["fatigue"] == -0.4
    assert by_id["db-001"]["score"] == -0.3


def test_score_equals_sum_of_breakdown():
    hits = [
        {"entry_id": "e0001", "kind": "weakness", "kp_id": "db/ch6/mvcc-readview"},
        {"entry_id": "e0003", "kind": "misconception", "kp_id": "db/ch6/mvcc-readview"},
    ]
    ranked = rank(CARDS, hits=hits, answers_today={"db/ch6/mvcc-readview": 1}, today=TODAY)
    top = ranked[0]
    assert round(sum(top["breakdown"].values()), 3) == top["score"]
