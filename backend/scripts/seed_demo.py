"""Crea la base de demostración: 1 curso con 52 sesiones, 30 alumnos de la cohorte sintética,
docente, tutor, coordinador y la Asistente Académica del curso.

Uso: python scripts/seed_demo.py [--regen] [--students 30] [--seed 42]

- Genera la cohorte si no existe (o si se pasa --regen) e ingesta sus exportaciones tipo Zoom.
- El contenido de cada clase (transcripción, capítulos, resumen, quiz) sale de la sesión de muestra
  data/sample/session_demo/, salvo que exista data/zoom/<session_id>/ con exportaciones reales.
- Recalcula cumplimiento, estados y alertas día a día durante las 4 semanas previas al reloj actual
  (SIM_NOW) y luego en SIM_NOW, como lo habría hecho el recálculo programado.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402

SAMPLE_TITLE = "Contenido de muestra"
STAFF = [
    ("Lincoln Villanueva", "lincoln.villanueva@dmc.pe", "docente"),
    ("Michela López", "michela.lopez@dmc.pe", "asistente_academico"),
    ("Rosa Huamán", "rosa.huaman@dmc.pe", "tutor"),
    ("Jorge Salas", "jorge.salas@dmc.pe", "coordinador"),
]
HISTORY_DAYS = 28  # recálculo diario previo a SIM_NOW para que alertas y escalamientos tengan historia
REAL_QUESTIONS = [  # preguntas simuladas de la cohorte sobre la sesión real
    "¿En qué se diferencia n8n de Zapier?",
    "No entiendo qué es self-hosted",
    "¿Para qué sirve Docker si ya tengo n8n en la nube?",
    "¿Qué es un contenedor?",
    "¿Por qué hay que instalar WSL?",
    "Me sale error de virtualización al abrir Docker",
    "¿Cómo levanto n8n con docker run?",
    "¿Qué pasa con mis workflows si cierro la terminal?",
    "¿Cómo creo la credencial de Google Sheets?",
    "¿Qué es un trigger?",
    "¿Cómo funciona el nodo If?",
    "¿Qué devuelve la API del clima?",
    "No entiendo cómo leer el JSON de la API",
    "¿Cómo armo una expresión con los datos del nodo anterior?",
    "¿Cómo envío el correo con Gmail desde n8n?",
    "¿Qué es una API key?",
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
    sys.stdout.reconfigure(encoding="utf-8")

    ensure_env()
    reset_database()
    if args.regen or not (config.COHORT_DIR / "platform_events.json").exists():
        from generate_cohort import DEFAULT_START, generate

        generate(args.students, args.weeks, args.seed, DEFAULT_START, config.COHORT_DIR)
    seed()


def seed_threads(db, sessions, real_sessions: set[int], now: datetime) -> None:
    """Hilos del tutor para las preguntas de contenido de la cohorte simulada («Temas que más cuestan»).
    El capítulo sale de la evidencia de la pregunta en la clase; si no aparece (preguntas de muestra en una sesión
    real), se asigna un tramo clave. El nivel alcanzado es simulado: cada capítulo tiene su propia dificultad."""
    import random

    from sqlmodel import select

    from app.models import Chapter, ChatMessage, ChatThread, WatchRange
    from app.services import tutor_chat

    rng = random.Random(23)
    for s in sessions:
        chapters = db.exec(select(Chapter).where(Chapter.session_id == s.id).order_by(Chapter.start)).all()
        keys = [c for c in chapters if c.is_key and not c.logistics]
        if not keys:
            continue
        if s.number in real_sessions:
            viewers = sorted(set(db.exec(select(WatchRange.user_id).where(WatchRange.session_id == s.id)).all()))
            span = max(1.0, (now - s.ends_at).total_seconds())
            for text in REAL_QUESTIONS:
                db.add(ChatMessage(user_id=rng.choice(viewers), session_id=s.id, role="user", text=text,
                                   kind="contenido", at=s.ends_at + timedelta(seconds=rng.uniform(0, span))))
            db.flush()
        difficulty = {c.id: rng.random() for c in chapters}
        corpus = tutor_chat.corpus_of(db, s)
        questions = db.exec(select(ChatMessage).where(ChatMessage.session_id == s.id, ChatMessage.role == "user",
                                                      ChatMessage.kind == "contenido")).all()
        for m in questions:
            ev = tutor_chat.find_evidence(corpus, chapters, m.text)
            chapter = ev.chapter if ev.found and ev.chapter else rng.choice(keys)
            d = difficulty[chapter.id]
            level = rng.choices([1, 2, 3, 4], weights=[1.0 - d, 0.6, 0.2 + d, 0.1 + d])[0]
            correct = level < 4 and rng.random() < 0.6
            thread = ChatThread(user_id=m.user_id, session_id=s.id, question=m.text, chapter_id=chapter.id,
                                level=level, attempts=max(0, level - 1) + correct,
                                evaluation="correcta" if correct else None, closed=True, created_at=m.at)
            db.add(thread)
            db.flush()
            m.thread_id = thread.id
            db.add(m)
    db.commit()


def seed() -> None:
    from sqlmodel import Session

    from app.db import engine, init_db
    from app.models import (ChatMessage, ClassSession, Course, Enrollment, Heartbeat, MicroAnswer, QuizAttempt,
                            User, WatchRange)
    from app.services import content
    from app.services.real_session import apply_to_session, has_real_content
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
            s = ClassSession(course_id=course.id, number=n, title=SAMPLE_TITLE,
                             starts_at=datetime.fromisoformat(row["starts_at"]),
                             ends_at=datetime.fromisoformat(row["ends_at"]),
                             transcript_path=str(sample_vtt), video_path=str(sample_mp4) if sample_mp4.exists() else None)
            db.add(s)
            db.flush()
            sessions[n] = s
        db.commit()

        real_sessions: set[int] = set()
        for n, s in sessions.items():
            ingest_session(db, s, cohort / "zoom" / str(n), store_transcript=False)
            real = config.ZOOM_DIR / str(n)
            if has_real_content(real):
                apply_to_session(db, s, real)
                real_sessions.add(n)
                print(f"Sesión {n}: contenido real tomado de {real} («{s.title}»)")

        # la cohorte se simuló con clases de 3 h; en una sesión real más larga, sus rangos vistos se escalan
        scale = {}
        for row in sessions_rows:
            n = int(row["number"])
            simulated = (datetime.fromisoformat(row["ends_at"]) - datetime.fromisoformat(row["starts_at"])).total_seconds()
            scale[n] = (sessions[n].ends_at - sessions[n].starts_at).total_seconds() / simulated
        events = json.loads((cohort / "platform_events.json").read_text(encoding="utf-8"))
        for e in events["watch"]:
            k = scale[e["session"]]
            db.add(WatchRange(user_id=by_email[e["email"]].id, session_id=sessions[e["session"]].id,
                              start_s=round(e["start_s"] * k, 1), end_s=round(e["end_s"] * k, 1),
                              at=datetime.fromisoformat(e["at"])))
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
            content.rebuild(db, s)
        now = config.now()
        seed_threads(db, list(sessions.values()), real_sessions, now)
        for days in range(HISTORY_DAYS, 0, -1):
            day = datetime.combine((now - timedelta(days=days)).date(), time(23, 59))
            if day >= first:
                recompute_course(db, course.id, day)
        result = recompute_course(db, course.id, now)

    counts: dict[str, int] = {}
    for st in result.values():
        counts[st.risk.state] = counts.get(st.risk.state, 0) + 1
    print(f"Base creada en {config.DATABASE_URL} con reloj {now:%Y-%m-%d %H:%M}. Estados: {counts}")
    print(f"LLM: {'simulado' if config.LLM_MOCK else config.LLM_MODEL_CHAT}.")


if __name__ == "__main__":
    main()
