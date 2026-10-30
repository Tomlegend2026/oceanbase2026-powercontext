from datetime import date

from recallcoach.scheduler import due_score, review

TODAY = date(2026, 11, 1)
FRESH = {"due_at": "2026-11-01", "interval_days": 1, "ease": 2.5, "reps": 0, "lapses": 0}


def test_first_correct_sets_one_day():
    state = review(FRESH, correct=True, confidence=4, today=TODAY)
    assert state["reps"] == 1
    assert state["interval_days"] == 1
    assert state["due_at"] == "2026-11-02"


def test_second_correct_sets_six_days():
    state = review({**FRESH, "reps": 1}, correct=True, confidence=4, today=TODAY)
    assert state["interval_days"] == 6
    assert state["due_at"] == "2026-11-07"


def test_wrong_resets_interval_and_penalises_ease():
    state = review({**FRESH, "reps": 3, "interval_days": 15, "ease": 2.5}, correct=False, confidence=1, today=TODAY)
    assert state["lapses"] == 1
    assert state["reps"] == 0
    assert state["interval_days"] == 1
    assert state["ease"] == 2.3


def test_ease_never_below_floor():
    state = review({**FRESH, "ease": 1.3}, correct=False, confidence=1, today=TODAY)
    assert state["ease"] == 1.3


def test_easy_correct_raises_ease_but_caps():
    state = review({**FRESH, "ease": 3.0}, correct=True, confidence=5, today=TODAY)
    assert state["ease"] == 3.0


def test_due_score_grows_and_caps():
    assert due_score("2026-11-01", TODAY) == 0.2
    assert due_score("2026-10-30", TODAY) == 0.44
    assert due_score("2026-10-01", TODAY) == 1.0
