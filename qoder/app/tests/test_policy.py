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


def only(**overrides):
    """断言"只产生一条决策"时的快捷取法。"""
    decisions = decide(**overrides)
    assert len(decisions) == 1
    return decisions[0]


# ---- memory-design.md 第 10 节的 7 个场景 ----


def test_first_failure_is_not_written():
    decision = only(lapses_7d=1)
    assert decision.action == "skip"
    assert decision.reason == "lapses<2"


def test_second_failure_writes_weakness():
    decision = only(lapses_7d=2)
    assert decision.action == "write"
    assert decision.kind == "weakness"
    assert "[kp:db/ch6/mvcc-readview]" in decision.text


def test_third_failure_revises_instead_of_adding():
    decision = only(lapses_7d=3, active={("weakness", "db/ch6/mvcc-readview"): "e0001"})
    assert decision.action == "revise"
    assert decision.kind == "weakness"
    assert decision.target_entry_id == "e0001"


def test_existing_entry_turns_write_into_revise():
    decision = only(lapses_7d=3, active={("weakness", "db/ch6/mvcc-readview"): "e0001"})
    assert decision.action == "revise"
    assert decision.target_entry_id == "e0001"


def test_forbidden_content_is_skipped():
    decision = only(lapses_7d=2, cause="概念混淆：参见 https://example.com/token")
    assert decision.action == "skip"
    assert decision.reason == "forbidden"


def test_daily_cap_blocks_writes():
    assert only(writes_today=20).reason == "daily-cap"


def test_daily_cap_keeps_the_blocked_content():
    """第 4 节第 4 条：进"待确认"而不是静默 skip，所以文本必须留在决策里。"""
    decision = only(writes_today=20, lapses_7d=3)
    assert decision.action == "skip"
    assert decision.kind == "weakness"
    assert decision.text.startswith("[kp:db/ch6/mvcc-readview]"), decision.text
    assert decision.target_entry_id is None

    existing = only(writes_today=20, lapses_7d=3, active={("weakness", CARD["kp_id"]): "e0001"})
    assert existing.reason == "daily-cap"
    assert existing.target_entry_id == "e0001", "被拦下的 revise 要能追认回同一条目"


def test_daily_cap_does_not_claim_a_write_that_was_never_planned():
    """没打算写的分支照实回传自己的原因，不冒充成"今日写满"。"""
    assert only(writes_today=20, lapses_7d=1).reason == "lapses<2"
    assert only(writes_today=20, correct=True).reason == "correct"

    reasons = [item.reason for item in decide(writes_today=20, lapses_7d=1, cause="概念混淆：参见 https://example.com/x")]
    assert reasons == ["lapses<2", "forbidden"]


def test_daily_cap_blocks_both_branches_separately():
    """两条都被拦下时不合并：待确认列表里各留一条自己的文本。"""
    decisions = decide(
        writes_today=20,
        lapses_7d=3,
        cause="概念混淆：把 read view 的创建时机与锁的持有范围混为一谈",
    )
    assert [item.reason for item in decisions] == ["daily-cap", "daily-cap"]
    assert [item.kind for item in decisions] == ["weakness", "misconception"]
    assert len({item.text for item in decisions}) == 2


def test_personalization_off_never_produces_a_write():
    """off 是对照组：策略层不能产生任何 write/revise（memory-design.md 第 9 节）。"""
    decisions = decide(lapses_7d=5, cause="概念混淆：把 read view 和锁的范围搞混", personalization=False)
    assert [item.action for item in decisions] == ["skip"]
    assert decisions[0].reason == "personalization-off"

    end = policy.decide_session_end(
        chapter="db/ch6", total=10, correct=3, stuck=["MVCC"], next_card="db-022",
        active_progress_entry=None, writes_today=0, personalization=False,
    )
    assert [item.action for item in end] == ["skip"]


# ---- 两个分支独立判断（memory-design.md 第 3 节） ----


def test_misconception_does_not_wait_for_the_lapse_threshold():
    """第一次答错但用户在错因里确认了混淆对象 → 只写 misconception，不写 weakness。"""
    decision = only(lapses_7d=1, cause="概念混淆：把 read view 的创建时机与锁的持有范围混为一谈")
    assert decision.action == "write"
    assert decision.kind == "misconception"
    assert "第 1 次" in decision.text


def test_confusion_cause_produces_both_kinds_once_threshold_met():
    """过阈值后同一次答错同时产出薄弱点与混淆两条决策（第 4 节允许各存 1 条 active）。"""
    weakness, misconception = decide(lapses_7d=2, cause="概念混淆：read view 与锁的范围")
    assert weakness.kind == "weakness" and weakness.action == "write"
    assert misconception.kind == "misconception" and misconception.action == "write"


def test_confusion_without_an_object_only_writes_weakness():
    """没填混淆对象时不写 misconception，避免把我们的猜测当成用户的判断。"""
    decisions = decide(lapses_7d=2, cause="概念混淆")
    assert [item.kind for item in decisions] == ["weakness"]


def test_existing_misconception_is_revised_not_duplicated():
    decisions = decide(
        lapses_7d=3,
        cause="概念混淆：read view 与锁的范围",
        active={("weakness", "db/ch6/mvcc-readview"): "e0001", ("misconception", "db/ch6/mvcc-readview"): "e0002"},
    )
    assert [(item.action, item.kind, item.target_entry_id) for item in decisions] == [
        ("revise", "weakness", "e0001"),
        ("revise", "misconception", "e0002"),
    ]


def test_correct_answer_never_writes():
    assert only(correct=True, confidence=5).reason == "correct"


# ---- 会话结束 ----


def test_empty_session_writes_nothing():
    decisions = policy.decide_session_end(
        chapter="db/ch6", total=0, correct=0, stuck=[], next_card=None,
        active_progress_entry=None, writes_today=0,
    )
    assert [item.action for item in decisions] == ["skip"]


def test_session_end_writes_progress_then_revises():
    first = policy.decide_session_end(
        chapter="db/ch6", total=10, correct=7, stuck=["MVCC"], next_card="db-022",
        active_progress_entry=None, writes_today=0,
    )
    assert len(first) == 1 and first[0].action == "write" and first[0].kind == "progress"
    assert "[kp:db/ch6]" in first[0].text
    assert "下次从 db-022 开始" in first[0].text

    second = policy.decide_session_end(
        chapter="db/ch6", total=6, correct=6, stuck=[], next_card="db-023",
        active_progress_entry="e0009", writes_today=1,
    )
    assert len(second) == 1 and second[0].action == "revise"
    assert second[0].target_entry_id == "e0009"


def test_session_end_over_cap_keeps_the_progress_text():
    """会话结束也被单日上限挡住：留下待确认文本，但不给空会话编一个"被拦下"。"""
    blocked = policy.decide_session_end(
        chapter="db/ch6", total=10, correct=3, stuck=["MVCC"], next_card="db-022",
        active_progress_entry=None, writes_today=policy.DAILY_WRITE_CAP,
    )
    assert [item.action for item in blocked] == ["skip"]
    assert blocked[0].reason == "daily-cap"
    assert blocked[0].kind == "progress"
    assert "10 张里答对 3" in blocked[0].text

    empty = policy.decide_session_end(
        chapter="db/ch6", total=0, correct=0, stuck=[], next_card=None,
        active_progress_entry=None, writes_today=policy.DAILY_WRITE_CAP,
    )
    assert empty[0].reason == "empty-session"
