from datetime import timedelta

import pytest

from app.services.attendance import (
    EN_VIVO,
    NO_CUMPLIDA,
    POR_RECUPERAR,
    RECUPERADA,
    Compliance,
    LiveMetrics,
    evaluate_session,
    indicators,
    key_coverage,
    live_metrics,
    merge_intervals,
)
from app.services.timeline import StudentData
from helpers import START, make_course, make_session

H = timedelta(hours=1)
M = timedelta(minutes=1)


def full_watch(at):
    return [(0.0, 10800.0, at)]


def test_en_vivo_with_enough_connection():
    s = make_session()
    st = StudentData(1, intervals={1: [(START, START + 2.5 * H)]})
    c = evaluate_session(st, s, make_course(), now=START + 4 * H)
    assert c.status == EN_VIVO
    assert c.live.live_ratio == pytest.approx(2.5 / 3)
    assert c.live.early_leave_min == pytest.approx(30)


def test_short_disconnections_count_as_connected():
    s = make_session()
    rows = [(START, START + H), (START + H + 40 * timedelta(seconds=1), START + 2 * H),
            (START + 2 * H + M, START + 3 * H)]
    merged, reconnects, gap_min = merge_intervals(rows)
    assert len(merged) == 1 and reconnects == 2 and gap_min == 0
    lm = live_metrics(rows, s)
    assert lm.live_ratio == pytest.approx(1.0)


def test_real_exit_is_not_connection_problem():
    s = make_session()
    rows = [(START, START + H), (START + H + 10 * M, START + 3 * H)]
    lm = live_metrics(rows, s)
    assert lm.reconnects == 0 and lm.gap_min == pytest.approx(10)
    assert lm.absent_ranges == [(3600.0, 4200.0)]


def test_late_and_early_leave_below_min_ratio_is_not_live():
    s = make_session()
    st = StudentData(1, intervals={1: [(START + 45 * M, START + 3 * H - 40 * M)]})
    c = evaluate_session(st, s, make_course(), now=START + 4 * H)
    assert c.status == POR_RECUPERAR
    assert c.live.late_min == pytest.approx(45)


def test_recovered_within_deadline():
    s = make_session()
    watched_at = START + timedelta(days=2)
    quiz_at = START + timedelta(days=3)
    st = StudentData(1, watch={1: full_watch(watched_at)}, quiz={1: [(2, quiz_at - H), (4, quiz_at)]})
    c = evaluate_session(st, s, make_course(), now=START + timedelta(days=10))
    assert c.status == RECUPERADA
    assert c.completed_at == quiz_at  # cuenta la fecha del último hito


def test_recovered_outside_deadline_is_not_complied():
    s = make_session()
    late = START + timedelta(days=9)
    st = StudentData(1, watch={1: full_watch(late)}, quiz={1: [(5, late)]})
    c = evaluate_session(st, s, make_course(), now=START + timedelta(days=10))
    assert c.status == NO_CUMPLIDA
    assert c.deadline.date() == (START + timedelta(days=7)).date()


def test_pending_while_deadline_open_and_then_failed():
    s = make_session()
    st = StudentData(1, watch={1: [(1800.0, 3600.0, START + timedelta(days=1))]})
    assert evaluate_session(st, s, make_course(), now=START + timedelta(days=2)).status == POR_RECUPERAR
    assert evaluate_session(st, s, make_course(), now=START + timedelta(days=8, hours=6)).status == NO_CUMPLIDA


def test_quiz_alone_is_not_enough():
    s = make_session()
    st = StudentData(1, watch={1: [(1800.0, 3600.0, START + timedelta(days=1))]},
                     quiz={1: [(5, START + timedelta(days=1))]})
    c = evaluate_session(st, s, make_course(), now=START + timedelta(days=2))
    assert c.status == POR_RECUPERAR
    assert c.key_coverage == pytest.approx(0.5)


def test_key_minutes_left_subtract_what_was_watched():
    s = make_session(key_ranges=[(0.0, 1200.0), (3000.0, 3600.0)])  # 30 min de tramos clave
    st = StudentData(1, watch={1: [(0.0, 600.0, START + timedelta(days=1))]})
    c = evaluate_session(st, s, make_course(), now=START + timedelta(days=2))
    assert c.key_remaining_s == pytest.approx(1200.0)
    assert c.key_minutes_left == 20
    assert indicators([c], make_course()).pending_key_minutes == 20


def test_key_coverage_only_counts_key_ranges():
    s = make_session(key_ranges=[(0.0, 1000.0)])
    watch = [(500.0, 2000.0, START), (0.0, 400.0, START)]
    assert key_coverage(watch, s) == pytest.approx(0.9)


def _c(number, status):
    return Compliance(number, number, START, status, START, None, LiveMetrics(), 0.0, None)


def test_projection_and_missing_sessions():
    course = make_course(total=20, attendance_threshold=0.8)
    statuses = [EN_VIVO] * 6 + [NO_CUMPLIDA, EN_VIVO, NO_CUMPLIDA, RECUPERADA, POR_RECUPERAR]
    ind = indicators([_c(i + 1, s) for i, s in enumerate(statuses)], course)
    assert ind.dictated == 11 and ind.complied == 8 and ind.pending == 1
    assert ind.effective == pytest.approx(8 / 11)
    # últimas 4 decididas: NO_CUMPLIDA, EN_VIVO, NO_CUMPLIDA, RECUPERADA -> tasa 0.5
    assert ind.recent_rate == pytest.approx(0.5)
    # restantes = 20 - 10 decididas = 10 -> (8 + 10 * 0.5) / 20
    assert ind.projection == pytest.approx(13 / 20)
    assert ind.missing == 16 - 8
    assert [c.number for c in ind.pending_items] == [11]


def test_projection_without_decided_sessions_is_optimistic():
    ind = indicators([], make_course(total=52))
    assert ind.projection == pytest.approx(1.0)
    assert ind.missing == 42
