"""Variables de diagnóstico por alumno (sección 6.5)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .. import config
from .attendance import EN_VIVO, NO_CUMPLIDA, POR_RECUPERAR, RECUPERADA, Compliance, Indicators
from .timeline import CourseData, MicroRecord, StudentData


@dataclass
class SessionFeatures:
    session_id: int
    number: int
    live_ratio: float
    late_min: float | None
    early_leave_min: float | None
    reconnects: int
    gap_min: float
    chat_msgs: int
    voice_turns: int
    key_coverage: float
    quiz_score: int | None
    status: str


@dataclass
class CurrentFeatures:
    last_activity_at: datetime
    days_since_activity: float
    active_min_week: float
    study_hour_mode: int | None
    previous_study_hour_mode: int | None
    questions_content: int
    questions_logistics: int
    quiz_score: int | None
    quiz_trend: float | None
    quiz_recent_mean: float | None
    micro: MicroRecord | None
    projection: float
    pattern_drop: bool = False
    late_shift: bool = False
    mode_shift: bool = False
    recent_live: float | None = None
    previous_live: float | None = None
    recent_late: float | None = None
    recent_early: float | None = None
    previous_late_early: float | None = None
    unrecovered: list[Compliance] = field(default_factory=list)
    recovered_recent: int = 0  # entre las últimas sesiones perdidas ya resueltas
    unrecovered_balance: int = 0
    consecutive_failed: tuple[int, int] | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _slope(values: list[float]) -> float | None:
    n = len(values)
    if n < 3:
        return None
    xs = range(n)
    mx, my = (n - 1) / 2, sum(values) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, values)) / den if den else 0.0


def session_features(student: StudentData, compliances: list[Compliance]) -> list[SessionFeatures]:
    out = []
    for c in compliances:
        out.append(SessionFeatures(
            session_id=c.session_id,
            number=c.number,
            live_ratio=round(c.live.live_ratio, 3),
            late_min=None if c.live.late_min is None else round(c.live.late_min, 1),
            early_leave_min=None if c.live.early_leave_min is None else round(c.live.early_leave_min, 1),
            reconnects=c.live.reconnects,
            gap_min=c.live.gap_min,
            chat_msgs=len(student.zoom_chat.get(c.session_id, [])),
            voice_turns=student.voice.get(c.session_id, 0),
            key_coverage=round(c.key_coverage, 3),
            quiz_score=c.best_quiz,
            status=c.status,
        ))
    return out


def last_activity(student: StudentData, data: CourseData, now: datetime) -> datetime:
    candidates: list[datetime] = []
    for ivs in student.intervals.values():
        candidates += [min(leave, now) for join, leave in ivs if join <= now]
    for lst in student.watch.values():
        candidates += [at for _, _, at in lst if at <= now]
    for lst in student.quiz.values():
        candidates += [at for _, at in lst if at <= now]
    for lst in student.zoom_chat.values():
        candidates += [at for at in lst if at <= now]
    candidates += [at for at, _ in student.questions if at <= now]
    start = datetime.combine(data.course.start_date, datetime.min.time())
    return max(candidates) if candidates else start


def _hour_mode(times: list[datetime]) -> int | None:
    if not times:
        return None
    return Counter(t.hour for t in times).most_common(1)[0][0]


def current_features(
    student: StudentData, data: CourseData, compliances: list[Compliance], ind: Indicators, now: datetime
) -> CurrentFeatures:
    course = data.course
    last_at = last_activity(student, data, now)
    week_ago = now - timedelta(days=7)
    active_min = sum(sec for at, sec in student.heartbeats if week_ago < at <= now) / 60

    async_times = [at for lst in student.watch.values() for _, _, at in lst if at <= now]
    async_times += [at for lst in student.quiz.values() for _, at in lst if at <= now]
    window = timedelta(days=config.STUDY_HOUR_WINDOW_DAYS)
    recent_async = [t for t in async_times if now - window < t]
    prev_async = [t for t in async_times if now - 2 * window < t <= now - window]

    questions = [kind for at, kind in student.questions if at <= now]
    quiz_by_session = [c.best_quiz for c in compliances if c.best_quiz is not None]
    recent_quiz = quiz_by_session[-config.QUIZ_TREND_WINDOW:]
    micro_since = now - timedelta(days=config.MICRO_SIGNAL_DAYS)
    answered = [m for m in student.micro if m.answered and micro_since <= m.at <= now]

    cur = CurrentFeatures(
        last_activity_at=last_at,
        days_since_activity=round((now - last_at).total_seconds() / 86400, 1),
        active_min_week=round(active_min, 1),
        study_hour_mode=_hour_mode(recent_async),
        previous_study_hour_mode=_hour_mode(prev_async),
        questions_content=questions.count("contenido"),
        questions_logistics=questions.count("logistica"),
        quiz_score=recent_quiz[-1] if recent_quiz else None,
        quiz_trend=_slope([float(x) for x in recent_quiz]),
        quiz_recent_mean=_mean([float(x) for x in recent_quiz]),
        micro=answered[-1] if answered else None,
        projection=ind.projection,
    )

    w = config.PATTERN_WINDOW
    recent, previous = compliances[-w:], compliances[:-w]
    if len(recent) == w and len(previous) >= config.PATTERN_MIN_PREVIOUS:
        cur.recent_live = _mean([c.live.live_ratio for c in recent])
        cur.previous_live = _mean([c.live.live_ratio for c in previous])
        cur.pattern_drop = cur.recent_live < config.PATTERN_DROP_FACTOR * cur.previous_live

        recent_att = [c for c in recent if c.live.attended]
        prev_att = [c for c in previous if c.live.attended]
        if len(recent_att) >= 2 and len(prev_att) >= config.PATTERN_MIN_PREVIOUS:
            cur.recent_late = _mean([c.live.late_min for c in recent_att])
            cur.recent_early = _mean([c.live.early_leave_min for c in recent_att])
            cur.previous_late_early = _mean([c.live.late_min + c.live.early_leave_min for c in prev_att])
            cur.late_shift = cur.recent_late + cur.recent_early > cur.previous_late_early + config.LATE_SHIFT_MIN

        live_share = sum(c.status == EN_VIVO for c in previous) / len(previous)
        cur.mode_shift = (
            sum(c.status == RECUPERADA for c in recent) >= config.MODE_SHIFT_MIN_RECOVERED
            and live_share >= config.MODE_SHIFT_HISTORY_LIVE
        )

    def unrecovered(c: Compliance) -> bool:
        stale = (
            c.status == POR_RECUPERAR
            and (now - c.starts_at).days >= config.UNRECOVERED_DAYS
            and c.key_coverage < 0.5
            and (c.best_quiz is None or c.best_quiz < course.quiz_pass_score)
        )
        return c.status == NO_CUMPLIDA or stale

    cur.unrecovered = [c for c in recent if unrecovered(c)]
    missed = [c for c in compliances if c.status == RECUPERADA or unrecovered(c)][-config.RECOVERY_BALANCE_WINDOW:]
    cur.unrecovered_balance = sum(not (c.status == RECUPERADA) for c in missed)
    cur.recovered_recent = sum(c.status == RECUPERADA for c in missed)

    tail = compliances[-config.CONSECUTIVE_WINDOW:]
    for a, b in zip(tail, tail[1:]):
        if a.status == NO_CUMPLIDA and b.status == NO_CUMPLIDA:
            cur.consecutive_failed = (a.number, b.number)
    return cur
