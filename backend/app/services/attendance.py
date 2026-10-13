"""Motor de asistencia efectiva (sección 6.4).

Una sesión cuenta como cumplida si el alumno asistió en vivo o la recuperó dentro del plazo
(tramos clave vistos + quiz aprobado)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .. import config
from ..models import Course
from .timeline import CourseData, SessionInfo, StudentData

EN_VIVO, RECUPERADA, POR_RECUPERAR, NO_CUMPLIDA = "en_vivo", "recuperada", "por_recuperar", "no_cumplida"
COMPLIED = {EN_VIVO, RECUPERADA}


# --- intervalos de Zoom ---

@dataclass
class LiveMetrics:
    attended: bool = False
    live_ratio: float = 0.0
    connected_s: float = 0.0
    late_min: float | None = None
    early_leave_min: float | None = None
    reconnects: int = 0
    gap_min: float = 0.0
    absent_ranges: list[tuple[float, float]] = field(default_factory=list)  # segundos desde el inicio de la clase


def merge_intervals(
    intervals: list[tuple[datetime, datetime]], gap_s: float = config.RECONNECT_GAP_MIN * 60
) -> tuple[list[tuple[datetime, datetime]], int, float]:
    """Une filas del mismo alumno. Un corte menor a `gap_s` es reconexión (se considera conectado);
    uno mayor es una salida real. Devuelve (intervalos, reconexiones, minutos fuera por salidas reales)."""
    if not intervals:
        return [], 0, 0.0
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    reconnects, gap_total = 0, 0.0
    for join, leave in ordered[1:]:
        cur = merged[-1]
        gap = (join - cur[1]).total_seconds()
        if gap < gap_s:
            if gap > 0:
                reconnects += 1
            cur[1] = max(cur[1], leave)
        else:
            gap_total += gap / 60
            merged.append([join, leave])
    return [(a, b) for a, b in merged], reconnects, gap_total


def live_metrics(intervals: list[tuple[datetime, datetime]], session: SessionInfo) -> LiveMetrics:
    if not intervals:
        return LiveMetrics(absent_ranges=[(0.0, session.duration_s)])
    merged, reconnects, gap_min = merge_intervals(intervals)
    start, end = session.starts_at, session.ends_at
    clipped = []
    for join, leave in merged:
        a, b = max(join, start), min(leave, end)
        if b > a:
            clipped.append(((a - start).total_seconds(), (b - start).total_seconds()))
    connected = sum(b - a for a, b in clipped)
    absent, cursor = [], 0.0
    for a, b in clipped:
        if a - cursor > 60:
            absent.append((cursor, a))
        cursor = max(cursor, b)
    if session.duration_s - cursor > 60:
        absent.append((cursor, session.duration_s))
    first_join, last_leave = merged[0][0], merged[-1][1]
    return LiveMetrics(
        attended=connected > 0,
        live_ratio=min(1.0, connected / session.duration_s) if session.duration_s else 0.0,
        connected_s=connected,
        late_min=max(0.0, (first_join - start).total_seconds() / 60),
        early_leave_min=max(0.0, (end - last_leave).total_seconds() / 60),
        reconnects=reconnects,
        gap_min=round(gap_min, 1),
        absent_ranges=absent,
    )


# --- cobertura de tramos clave ---

def union_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted((min(a, b), max(a, b)) for a, b in ranges):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def overlap(ranges: list[tuple[float, float]], targets: list[tuple[float, float]]) -> float:
    total = 0.0
    for a, b in ranges:
        for c, d in targets:
            lo, hi = max(a, c), min(b, d)
            if hi > lo:
                total += hi - lo
    return total


def key_targets(session: SessionInfo) -> list[tuple[float, float]]:
    return union_ranges(session.key_ranges) if session.key_ranges else [(0.0, session.duration_s)]


def key_coverage(watch: list[tuple[float, float, datetime]], session: SessionInfo, until: datetime | None = None) -> float:
    targets = key_targets(session)
    total = sum(b - a for a, b in targets)
    seen = union_ranges([(a, b) for a, b, at in watch if until is None or at <= until])
    return min(1.0, overlap(seen, targets) / total) if total else 0.0


def coverage_reached_at(
    watch: list[tuple[float, float, datetime]], session: SessionInfo, minimum: float, until: datetime
) -> tuple[float, datetime | None]:
    """Cobertura alcanzada hasta `until` y el instante en que se superó `minimum` (o None)."""
    targets = key_targets(session)
    total = sum(b - a for a, b in targets) or 1.0
    seen: list[tuple[float, float]] = []
    reached_at, coverage = None, 0.0
    for a, b, at in watch:
        if at > until:
            break
        seen = union_ranges(seen + [(a, b)])
        coverage = min(1.0, overlap(seen, targets) / total)
        if reached_at is None and coverage >= minimum - 1e-9:
            reached_at = at
    return coverage, reached_at


# --- estado por alumno y sesión ---

@dataclass
class Compliance:
    session_id: int
    number: int
    starts_at: datetime
    status: str
    deadline: datetime
    completed_at: datetime | None
    live: LiveMetrics
    key_coverage: float
    best_quiz: int | None

    @property
    def complied(self) -> bool:
        return self.status in COMPLIED


def evaluate_session(student: StudentData, session: SessionInfo, course: Course, now: datetime) -> Compliance:
    deadline = session.deadline(course.recovery_days)
    live = live_metrics(student.intervals.get(session.id, []), session)
    watch = student.watch.get(session.id, [])
    attempts = [(score, at) for score, at in student.quiz.get(session.id, []) if at <= now]
    best_quiz = max((s for s, _ in attempts), default=None)
    coverage_now = key_coverage(watch, session, until=now)

    if live.live_ratio >= course.live_min_ratio:
        return Compliance(session.id, session.number, session.starts_at, EN_VIVO, deadline, session.ends_at,
                          live, coverage_now, best_quiz)

    limit = min(now, deadline)
    _, coverage_at = coverage_reached_at(watch, session, course.key_coverage_min, limit)
    quiz_at = next((at for score, at in attempts if score >= course.quiz_pass_score and at <= limit), None)
    if coverage_at and quiz_at:
        status, completed = RECUPERADA, max(coverage_at, quiz_at)
    elif now <= deadline:
        status, completed = POR_RECUPERAR, None
    else:
        status, completed = NO_CUMPLIDA, None
    return Compliance(session.id, session.number, session.starts_at, status, deadline, completed,
                      live, coverage_now, best_quiz)


def evaluate_student(student: StudentData, data: CourseData, now: datetime) -> list[Compliance]:
    return [evaluate_session(student, s, data.course, now) for s in data.dictated(now)]


# --- indicadores ---

@dataclass
class Indicators:
    dictated: int
    complied: int
    live: int
    recovered: int
    pending: int
    failed: int
    total: int
    threshold: float
    effective: float
    recent_rate: float
    projection: float
    missing: int
    pending_items: list[Compliance]


def indicators(compliances: list[Compliance], course: Course) -> Indicators:
    total = course.total_sessions
    complied = sum(c.complied for c in compliances)
    decided = [c for c in compliances if c.status != POR_RECUPERAR]
    last = decided[-4:]
    rate = sum(c.complied for c in last) / len(last) if last else 1.0
    remaining = total - len(decided)
    projection = (complied + remaining * rate) / total if total else 0.0
    pending = [c for c in compliances if c.status == POR_RECUPERAR]
    return Indicators(
        dictated=len(compliances),
        complied=complied,
        live=sum(c.status == EN_VIVO for c in compliances),
        recovered=sum(c.status == RECUPERADA for c in compliances),
        pending=len(pending),
        failed=sum(c.status == NO_CUMPLIDA for c in compliances),
        total=total,
        threshold=course.attendance_threshold,
        effective=complied / len(compliances) if compliances else 0.0,
        recent_rate=rate,
        projection=min(1.0, projection),
        missing=max(0, math.ceil(course.attendance_threshold * total - 1e-9) - complied),
        pending_items=sorted(pending, key=lambda c: c.deadline),
    )


def certification(ind: Indicators, grade: float | None, project_status: str, finished: bool) -> dict:
    """Requisitos del certificado. Encuesta y pagos quedan fuera del MVP."""
    if finished:
        attendance_ok = ind.effective >= ind.threshold
        attendance_state = "cumplido" if attendance_ok else "no_cumplido"
    else:
        attendance_ok = ind.projection >= ind.threshold
        attendance_state = "en_camino" if attendance_ok else "en_riesgo"
    grade_state = "pendiente" if grade is None else ("cumplido" if grade >= config.MIN_GRADE else "no_cumplido")
    project_state = {"aprobado": "cumplido", "desaprobado": "no_cumplido"}.get(project_status, "pendiente")
    return {
        "certificable": finished and attendance_ok and grade_state == "cumplido" and project_state == "cumplido",
        "requirements": [
            {"key": "asistencia", "label": f"Asistencia efectiva ≥ {round(ind.threshold * 100)} %", "state": attendance_state},
            {"key": "nota", "label": f"Nota final ≥ {config.MIN_GRADE}", "state": grade_state,
             "value": grade},
            {"key": "proyecto", "label": "Proyecto aprobado", "state": project_state},
            {"key": "encuesta", "label": "Encuesta final", "state": "pendiente_verificar"},
            {"key": "pagos", "label": "Pagos al día", "state": "pendiente_verificar"},
        ],
    }
