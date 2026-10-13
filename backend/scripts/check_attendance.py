"""Criterio de aceptación: la asistencia efectiva coincide con un cálculo manual (sección 12).

Toma 5 alumnos de la cohorte (uno por perfil, y al menos uno con una recuperación fuera de plazo),
recalcula cada sesión dictada directamente desde las filas crudas (Zoom, rangos vistos, quizzes)
con las reglas escritas de la forma más simple posible, y las compara con el motor de la API.

Reglas:
- En vivo: tiempo conectado dentro del horario ≥ 70 % (cortes < 2 min cuentan como conectado).
- Recuperada: antes del plazo (fecha de la clase + 7 días, 23:59) vio ≥ 90 % de los tramos clave
  y aprobó el quiz con ≥ 3/5.
- Por recuperar: no cumplió todavía y el plazo sigue abierto. No cumplida: el plazo venció.

Uso: python scripts/check_attendance.py [--detail]
Requiere haber corrido antes scripts/seed_demo.py.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Chapter, ClassSession, Course, QuizAttempt, User, WatchRange, ZoomInterval  # noqa: E402
from app.services.attendance import evaluate_student  # noqa: E402
from app.services.timeline import load_course_data  # noqa: E402

PROFILES = ["estable", "mala_conexion", "cambio_horario", "desinteres", "adaptado"]


def connected_seconds(rows: list[ZoomInterval], start: datetime, end: datetime) -> float:
    rows = sorted(rows, key=lambda r: r.join_at)
    blocks: list[list[datetime]] = []
    for r in rows:
        if blocks and (r.join_at - blocks[-1][1]).total_seconds() < 120:
            blocks[-1][1] = max(blocks[-1][1], r.leave_at)
        else:
            blocks.append([r.join_at, r.leave_at])
    return sum(max(0.0, (min(b, end) - max(a, start)).total_seconds()) for a, b in blocks)


def seen_key_seconds(seen: list[tuple[float, float]], keys: list[tuple[float, float]]) -> float:
    marks = set()
    for a, b in seen:
        marks.update(range(int(a), int(b)))
    return sum(1 for a, b in keys for s in range(int(a), int(b)) if s in marks)


def manual_status(session: ClassSession, course: Course, zoom: list[ZoomInterval], watch: list[WatchRange],
                  quizzes: list[QuizAttempt], keys: list[tuple[float, float]], now: datetime) -> tuple[str, dict]:
    duration = (session.ends_at - session.starts_at).total_seconds()
    live = connected_seconds(zoom, session.starts_at, session.ends_at) / duration
    deadline = datetime.combine(session.starts_at.date() + timedelta(days=course.recovery_days), time(23, 59, 59))
    limit = min(now, deadline)
    keys = keys or [(0.0, duration)]
    key_total = sum(b - a for a, b in keys)

    coverage_ok_at, seen = None, []
    for w in sorted(watch, key=lambda w: w.at):
        if w.at > limit:
            break
        seen.append((w.start_s, w.end_s))
        if coverage_ok_at is None and seen_key_seconds(seen, keys) / key_total >= course.key_coverage_min - 0.001:
            coverage_ok_at = w.at
    quiz_ok_at = next((q.at for q in sorted(quizzes, key=lambda q: q.at)
                       if q.score >= course.quiz_pass_score and q.at <= limit), None)
    all_seen = [(w.start_s, w.end_s) for w in watch if w.at <= now]
    detail = {
        "live": live,
        "coverage": seen_key_seconds(all_seen, keys) / key_total,
        "best_quiz": max((q.score for q in quizzes if q.at <= now), default=None),
        "deadline": deadline,
        "late_recovery": False,
    }
    if live >= course.live_min_ratio:
        return "en_vivo", detail
    if coverage_ok_at and quiz_ok_at:
        return "recuperada", detail
    full_cov = seen_key_seconds(all_seen, keys) / key_total >= course.key_coverage_min - 0.001
    passed_late = any(q.score >= course.quiz_pass_score and deadline < q.at <= now for q in quizzes)
    late_watch = any(w.at > deadline for w in watch if w.at <= now)
    detail["late_recovery"] = now > deadline and full_cov and (passed_late or late_watch) and any(
        q.score >= course.quiz_pass_score and q.at <= now for q in quizzes)
    return ("por_recuperar" if now <= deadline else "no_cumplida"), detail


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detail", action="store_true", help="muestra todas las sesiones de cada alumno")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    now = config.now()

    with (config.COHORT_DIR / "ground_truth.csv").open(encoding="utf-8") as fh:
        profile_by_email = {row["email"]: row["profile"] for row in csv.DictReader(fh)}

    with Session(engine) as db:
        course = db.exec(select(Course)).first()
        sessions = db.exec(select(ClassSession).where(ClassSession.course_id == course.id,
                                                      ClassSession.ends_at <= now).order_by(ClassSession.number)).all()
        keys: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for ch in db.exec(select(Chapter).where(Chapter.is_key == True)).all():  # noqa: E712
            keys[ch.session_id].append((ch.start, ch.end))
        students = db.exec(select(User).where(User.role == config.STUDENT_ROLE).order_by(User.id)).all()

        def raw(model, uid):
            out = defaultdict(list)
            for row in db.exec(select(model).where(model.user_id == uid)).all():
                out[row.session_id].append(row)
            return out

        manual: dict[int, dict[int, tuple[str, dict]]] = {}
        for st in students:
            zoom, watch, quiz = raw(ZoomInterval, st.id), raw(WatchRange, st.id), raw(QuizAttempt, st.id)
            manual[st.id] = {s.id: manual_status(s, course, zoom[s.id], watch[s.id], quiz[s.id], keys[s.id], now)
                             for s in sessions}

        chosen: list[User] = []
        late = next((st for st in students if any(d["late_recovery"] for _, d in manual[st.id].values())), None)
        if late:
            chosen.append(late)
        for profile in PROFILES:
            if any(profile_by_email.get(st.email) == profile for st in chosen):
                continue
            st = next((st for st in students if profile_by_email.get(st.email) == profile), None)
            if st:
                chosen.append(st)

        data = load_course_data(db, course.id, [st.id for st in chosen])
        engine_status = {st.id: {c.session_id: c.status for c in evaluate_student(data.students[st.id], data, now)}
                         for st in chosen}

    print(f"Asistencia efectiva: cálculo manual vs motor ({len(sessions)} sesiones dictadas al {now:%Y-%m-%d %H:%M})\n")
    mismatches = 0
    number = {s.id: s.number for s in sessions}
    for st in chosen:
        rows = manual[st.id]
        counts = defaultdict(int)
        for status, _ in rows.values():
            counts[status] += 1
        diff = [sid for sid, (status, _) in rows.items() if engine_status[st.id].get(sid) != status]
        mismatches += len(diff)
        complied = counts["en_vivo"] + counts["recuperada"]
        print(f"- {st.name} ({profile_by_email.get(st.email, '?')}): en vivo {counts['en_vivo']}, "
              f"recuperadas {counts['recuperada']}, por recuperar {counts['por_recuperar']}, "
              f"no cumplidas {counts['no_cumplida']} → efectiva {complied}/{len(rows)} = "
              f"{complied / len(rows):.0%}  {'COINCIDE' if not diff else f'DIFIERE en {len(diff)} sesiones'}")
        shown = [sid for sid, (status, d) in rows.items()
                 if args.detail or sid in diff or d["late_recovery"] or status in {"recuperada", "por_recuperar"}]
        for sid in shown[-6:] if not args.detail else shown:
            status, d = rows[sid]
            quiz = "-" if d["best_quiz"] is None else f"{d['best_quiz']}/5"
            note = "  ← recuperada fuera de plazo: no cuenta" if d["late_recovery"] else ""
            engine_note = "" if engine_status[st.id].get(sid) == status else f"  [motor: {engine_status[st.id].get(sid)}]"
            print(f"    sesión {number[sid]:>2}: en vivo {d['live']:>4.0%} · tramos clave {d['coverage']:>4.0%} · "
                  f"quiz {quiz:>3} · plazo {d['deadline']:%d/%m} → {status}{note}{engine_note}")
    if not late:
        print("\nAviso: ningún alumno de la cohorte tiene una recuperación fuera de plazo.")
    print(f"\nResultado: {'COINCIDE en todas las sesiones' if not mismatches else f'{mismatches} diferencias'}")
    sys.exit(1 if mismatches or not late else 0)


if __name__ == "__main__":
    main()
