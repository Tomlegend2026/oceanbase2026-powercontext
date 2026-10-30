from recallcoach import policy

CARD = {"kp_id": "db/ch6/mvcc-readview", "kp_name": "MVCC 与 read view 创建时机"}


def decide(**overrides):
    params = dict(
        card=CARD,
        correct=False,
        confidence=1,
        cause=None,
        lapses_7d=2,
        active={},
        writes_today=0,
    )
    params.update(overrides)
    return policy.decide_answer(**params)


def test_first_failure_is_not_written():
    assert decide(lapses_7d=1).action == "skip"
    assert decide(lapses_7d=1).reason == "lapses<2"


def test_second_failure_writes_weakness():
    decision = decide(lapses_7d=2)
    assert decision.action == "write"
    assert decision.kind == "weakness"
    assert "[kp:db/ch6/mvcc-readview]" in decision.text


def test_existing_entry_turns_write_into_revise():
    decision = decide(lapses_7d=3, active={("weakness", "db/ch6/mvcc-readview"): "e0001"})
    assert decision.action == "revise"
    assert decision.target_entry_id == "e0001"


def test_misconception_kind_when_cause_says_so():
    decision = decide(lapses_7d=2, cause="概念混淆：把 read view 和锁的范围搞混")
    assert decision.kind == "misconception"
    assert "read view" in decision.text


def test_correct_answer_never_writes():
    assert decide(correct=True, confidence=5).reason == "correct"


def test_daily_cap_blocks_writes():
    assert decide(writes_today=20).reason == "daily-cap"


def test_forbidden_content_is_skipped():
    decision = decide(lapses_7d=2, cause="概念混淆：参见 https://example.com/token")
    assert decision.action == "skip"
    assert decision.reason == "forbidden"


def test_empty_session_writes_nothing():
    decision = policy.decide_session_end(
        chapter="db/ch6", total=0, correct=0, stuck=[], next_card=None,
        active_progress_entry=None, writes_today=0,
    )
    assert decision.action == "skip"


def test_session_end_writes_progress_then_revises():
    first = policy.decide_session_end(
        chapter="db/ch6", total=10, correct=7, stuck=["MVCC"], next_card="db-022",
        active_progress_entry=None, writes_today=0,
    )
    assert first.action == "write" and first.kind == "progress"
    assert "[kp:db/ch6]" in first.text

    second = policy.decide_session_end(
        chapter="db/ch6", total=6, correct=6, stuck=[], next_card="db-023",
        active_progress_entry="e0009", writes_today=1,
    )
    assert second.action == "revise" and second.target_entry_id == "e0009"
