from datetime import date, datetime, timedelta

from app import config
from app.models import Course
from app.services.timeline import CourseData, SessionInfo, StudentData

START = datetime(2026, 10, 6, 19, 30)  # martes


def make_course(total: int = 10, **overrides) -> Course:
    params = {**config.COURSE_DEFAULTS, **overrides}
    return Course(id=1, name="Curso de prueba", start_date=START.date(), end_date=date(2026, 12, 31),
                  total_sessions=total, schedule="mar/jue", **params)


def make_session(number: int = 1, start: datetime = START, key_ranges=None) -> SessionInfo:
    return SessionInfo(id=number, number=number, title=f"Sesión {number}", starts_at=start,
                       ends_at=start + timedelta(hours=3),
                       key_ranges=key_ranges if key_ranges is not None else [(1800.0, 3600.0), (5400.0, 7200.0)])


def make_data(sessions: list[SessionInfo], students: list[StudentData], total: int | None = None) -> CourseData:
    return CourseData(make_course(total or len(sessions)), sessions, {s.user_id: s for s in students})
