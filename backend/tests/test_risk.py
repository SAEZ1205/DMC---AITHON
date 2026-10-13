from datetime import timedelta

from app.services.recompute import student_status
from app.services.risk import ACTIVO, EN_ADAPTACION, EN_RIESGO, INACTIVO
from app.services.timeline import StudentData
from helpers import START, make_data, make_session

N = 16
SESSIONS = [make_session(i + 1, START + timedelta(days=(i // 2) * 7 + (i % 2) * 2)) for i in range(N)]
H = timedelta(hours=1)
M = timedelta(minutes=1)


def live(st: StudentData, s, late=0, early=0):
    st.intervals[s.id].append((s.starts_at + late * M, s.ends_at - early * M))


def recover(st: StudentData, s, days=1):
    at = s.starts_at + timedelta(days=days)
    st.watch[s.id].append((0.0, 10800.0, at))
    st.quiz[s.id].append((4, at + H))


def now_after(n):
    return SESSIONS[n - 1].ends_at + timedelta(days=5)


def test_stable_student_is_active():
    st = StudentData(1)
    for s in SESSIONS[:12]:
        live(st, s)
    status = student_status(st, make_data(SESSIONS, [st]), now_after(12))
    assert status.risk.state == ACTIVO


def test_schedule_change_without_recovery_is_at_risk_with_reasons():
    st = StudentData(1)
    for s in SESSIONS[:8]:
        live(st, s)
    for s in SESSIONS[8:12]:
        live(st, s, late=45, early=40)
    status = student_status(st, make_data(SESSIONS, [st]), now_after(12))
    assert status.risk.state == EN_RIESGO
    assert status.current.late_shift and status.current.pattern_drop
    assert any("tarde" in r for r in status.risk.reasons)


def test_student_who_recovers_is_adapting_not_at_risk():
    st = StudentData(1)
    for s in SESSIONS[:8]:
        live(st, s)
    for s in SESSIONS[8:12]:
        recover(st, s)
    status = student_status(st, make_data(SESSIONS, [st]), now_after(12))
    assert status.current.mode_shift
    assert status.risk.state == EN_ADAPTACION


def test_inactive_after_14_days_without_activity():
    st = StudentData(1)
    for s in SESSIONS[:4]:
        live(st, s)
    status = student_status(st, make_data(SESSIONS, [st]), SESSIONS[3].ends_at + timedelta(days=15))
    assert status.risk.state == INACTIVO
