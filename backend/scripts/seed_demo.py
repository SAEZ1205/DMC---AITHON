"""Crea la base de demostración: 1 curso con 52 sesiones, 30 alumnos de la cohorte sintética,
docente, tutor, coordinador y la Asistente Académica del curso.

Uso: python scripts/seed_demo.py [--regen] [--students 30] [--seed 42]

- Genera la cohorte si no existe (o si se pasa --regen) e ingesta sus exportaciones tipo Zoom.
- El contenido de cada clase (transcripción, capítulos, resumen, quiz) sale de la sesión de muestra
  data/sample/session_demo/, salvo que exista data/zoom/<session_id>/ con exportaciones reales.
- Al final recalcula cumplimiento, estados y alertas con el reloj actual (SIM_NOW).
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402

TOPICS = [
    "Introducción a n8n y primeros flujos", "Nodos, ítems y ejecuciones", "Triggers y Schedule Trigger",
    "Webhooks, HTTP Request y expresiones", "Google Sheets y Gmail", "Lógica: IF, Switch y Merge",
    "Transformación de datos con Edit Fields y Code", "APIs REST y autenticación", "Bases de datos con n8n",
    "Manejo de errores y monitoreo", "Introducción a IA generativa", "AI Agent y herramientas",
    "Memoria y contexto en agentes", "RAG con documentos", "Clasificación y extracción con LLM",
    "Chatbots en WhatsApp y Telegram", "Automatización de reportes", "Integración con CRM",
    "Flujos de aprobación", "Despliegue con Docker", "Variables de entorno y seguridad",
    "Escalamiento y colas", "Buenas prácticas y versionado", "Casos de negocio", "Taller de proyecto final",
    "Presentación de proyectos",
]
STAFF = [
    ("Lincoln Villanueva", "lincoln.villanueva@dmc.pe", "docente"),
    ("Michela López", "michela.lopez@dmc.pe", "asistente_academico"),
    ("Rosa Huamán", "rosa.huaman@dmc.pe", "tutor"),
    ("Jorge Salas", "jorge.salas@dmc.pe", "coordinador"),
]


def reset_database() -> None:
    db_path = Path(config.DATABASE_URL.replace("sqlite:///", ""))
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(db_path) + suffix)
        if target.exists():
            target.unlink()


def ensure_env() -> None:
    env, example = BACKEND / ".env", BACKEND / ".env.example"
    if not env.exists() and example.exists():
        shutil.copy(example, env)
        print("Se creó backend/.env a partir de .env.example (agrega tu LLM_API_KEY para usar Kimi).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regen", action="store_true", help="regenera la cohorte sintética")
    ap.add_argument("--students", type=int, default=30)
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    ensure_env()
    reset_database()
    if args.regen or not (config.COHORT_DIR / "platform_events.json").exists():
        from generate_cohort import DEFAULT_START, generate

        generate(args.students, args.weeks, args.seed, DEFAULT_START, config.COHORT_DIR)
    seed()


def seed() -> None:
    from sqlmodel import Session

    from app.db import engine, init_db
    from app.models import (ChatMessage, ClassSession, Course, Enrollment, Heartbeat, MicroAnswer, QuizAttempt,
                            User, WatchRange)
    from app.services.processing import process_session
    from app.services.recompute import recompute_course
    from app.services.zoom_ingest import ingest_session

    init_db()
    cohort = config.COHORT_DIR
    with (cohort / "sessions.csv").open(encoding="utf-8") as fh:
        sessions_rows = list(csv.DictReader(fh))
    with (cohort / "students.csv").open(encoding="utf-8") as fh:
        students_rows = list(csv.DictReader(fh))

    with Session(engine) as db:
        staff = {}
        for name, email, role in STAFF:
            user = User(name=name, email=email, role=role)
            db.add(user)
            staff[role] = user
        db.flush()
        first = datetime.fromisoformat(sessions_rows[0]["starts_at"])
        last = datetime.fromisoformat(sessions_rows[-1]["starts_at"])
        course = Course(
            name="Especialización en n8n e IA: Automatización Inteligente Low-Code - Edición - 7",
            start_date=first.date(), end_date=last.date(), total_sessions=len(sessions_rows),
            schedule="Martes y jueves, 7:30 p. m. a 10:30 p. m.", teacher_id=staff["docente"].id,
            academic_assistant_id=staff["asistente_academico"].id, **config.COURSE_DEFAULTS,
        )
        db.add(course)
        db.flush()

        by_email: dict[str, User] = {}
        for row in students_rows:
            user = User(name=row["name"], email=row["email"], role=config.STUDENT_ROLE)
            db.add(user)
            db.flush()
            by_email[row["email"]] = user
            db.add(Enrollment(user_id=user.id, course_id=course.id))

        sample_vtt = config.SAMPLE_SESSION_DIR / "transcript.vtt"
        sample_mp4 = config.SAMPLE_SESSION_DIR / "recording.mp4"
        sessions: dict[int, ClassSession] = {}
        for row in sessions_rows:
            n = int(row["number"])
            topic = TOPICS[(n - 1) // 2 % len(TOPICS)]
            s = ClassSession(course_id=course.id, number=n, title=f"{topic} ({'teoría' if n % 2 else 'práctica'})",
                             starts_at=datetime.fromisoformat(row["starts_at"]),
                             ends_at=datetime.fromisoformat(row["ends_at"]),
                             transcript_path=str(sample_vtt), video_path=str(sample_mp4) if sample_mp4.exists() else None)
            db.add(s)
            db.flush()
            sessions[n] = s
        db.commit()

        for n, s in sessions.items():
            ingest_session(db, s, cohort / "zoom" / str(n), store_transcript=False)
            real = config.ZOOM_DIR / str(s.id)
            if real.exists():
                ingest_session(db, s, real, store_transcript=True, attendance=False)
                print(f"Sesión {s.id}: contenido tomado de {real}")

        events = json.loads((cohort / "platform_events.json").read_text(encoding="utf-8"))
        for e in events["watch"]:
            db.add(WatchRange(user_id=by_email[e["email"]].id, session_id=sessions[e["session"]].id,
                              start_s=e["start_s"], end_s=e["end_s"], at=datetime.fromisoformat(e["at"])))
        for e in events["quiz"]:
            db.add(QuizAttempt(user_id=by_email[e["email"]].id, session_id=sessions[e["session"]].id,
                               score=e["score"], at=datetime.fromisoformat(e["at"])))
        for e in events["heartbeats"]:
            db.add(Heartbeat(user_id=by_email[e["email"]].id, course_id=course.id, page=e["page"],
                             active_seconds=e["active_seconds"], at=datetime.fromisoformat(e["at"])))
        for e in events["questions"]:
            db.add(ChatMessage(user_id=by_email[e["email"]].id, session_id=sessions[e["session"]].id, role="user",
                               text=e["text"], kind=e["kind"], at=datetime.fromisoformat(e["at"])))
        for e in events["micro"]:
            db.add(MicroAnswer(user_id=by_email[e["email"]].id, course_id=course.id, question=e["question"],
                               answer=e["answer"], topic=e["topic"], sentiment=e["sentiment"], summary=e["summary"],
                               at=datetime.fromisoformat(e["at"])))
        db.commit()

        for s in sessions.values():
            process_session(db, s)
        now = config.now()
        result = recompute_course(db, course.id, now)

    counts: dict[str, int] = {}
    for st in result.values():
        counts[st.risk.state] = counts.get(st.risk.state, 0) + 1
    print(f"Base creada en {config.DATABASE_URL} con reloj {now:%Y-%m-%d %H:%M}. Estados: {counts}")
    print(f"LLM: {'simulado' if config.LLM_MOCK else config.LLM_MODEL_CHAT}.")


if __name__ == "__main__":
    main()
