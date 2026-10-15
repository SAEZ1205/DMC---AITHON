"""Criterios de aceptación del chat tutor (spec v2, sección 8) sobre la sesión real.

Mide cuatro cosas:
  1. Minuto: ≥ 16 de 20 preguntas de la clase citan un minuto a ±90 s del esperado (tomado de la transcripción).
  2. Primer turno: 20 de 20 respuestas terminan con una pregunta al alumno y no superan 90 palabras.
  3. Quiz: 0 de 5 preguntas copiadas del quiz aprobado reciben la respuesta correcta antes de aprobarlo
     (se piden todas las pistas y la explicación).
  4. Fuera de la clase: 5 de 5 reciben «Eso no se vio en esta clase…».

Corre sobre una copia temporal de la base (no deja mensajes en la demo), con un alumno que aún no aprueba el quiz.
Para medir el modelo real, configura LLM_API_KEY en backend/.env.

Uso: python scripts/eval_chat.py [--session 23]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

QUESTIONS: list[tuple[str, int]] = [
    ("¿Por qué conviene automatizar un proceso que es propenso a errores?", 686),
    ("¿Dónde corren Zapier y Make, y dónde puede correr n8n?", 1183),
    ("¿Cómo cobra Zapier por las ejecuciones?", 1189),
    ("¿Qué significa instalar n8n en modo self-hosted?", 1094),
    ("¿Qué es un RPA?", 1470),
    ("¿Qué diferencia hay entre RPA y web scraping?", 1640),
    ("¿Qué hace Docker?", 2343),
    ("¿Qué es una aplicación containerizada?", 2388),
    ("¿Qué es WSL y para qué se instala?", 2722),
    ("¿Cómo valido que WSL quedó instalado?", 2779),
    ("¿Qué hago si Docker dice que la virtualización no está habilitada?", 4290),
    ("¿La versión free de n8n necesita licencia?", 5107),
    ("¿Qué pasa si cierro la terminal donde levanté n8n?", 5576),
    ("¿Dónde guarda n8n las credenciales?", 6892),
    ("¿Puedo reutilizar una credencial de Google Sheets en otro workflow?", 6928),
    ("¿Qué tipos de triggers hay en n8n?", 7028),
    ("¿Qué nodos de lógica existen además del If?", 7093),
    ("¿Qué datos devuelve la API del clima que usamos?", 7446),
    ("¿Cómo se escribe una expresión en n8n?", 8335),
    ("¿Cómo conecto Gmail para enviar el correo?", 9034),
]
OUT_OF_CLASS = [
    "¿Cómo instalo Kubernetes en mi laptop?",
    "¿Cómo conecto n8n con Salesforce?",
    "¿Cuál es la capital de Francia?",
    "¿Qué es una red neuronal convolucional?",
    "¿Cómo se entrena un modelo de regresión logística?",
]
TOLERANCE_S = 90


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", type=int, default=23, help="número de la sesión (por defecto, la sesión real)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from app import config as base_config
    source = base_config.DATA_DIR / "dmc.db"
    if not source.exists():
        sys.exit("No existe data/dmc.db: corre primero scripts/seed_demo.py")
    tmp = Path(tempfile.mkdtemp()) / "dmc.db"
    shutil.copy(source, tmp)
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp.as_posix()}"
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]

    from sqlmodel import Session, select

    from app import config
    from app.db import engine
    from app.models import ClassSession, Enrollment, User
    from app.services import llm, quiz as quiz_service, tutor_chat
    from app.services.chat import answer_question

    with Session(engine) as db:
        session = db.exec(select(ClassSession).where(ClassSession.number == args.session)).first()
        students = db.exec(select(User).join(Enrollment, Enrollment.user_id == User.id)
                           .where(Enrollment.course_id == session.course_id, User.role == config.STUDENT_ROLE)
                           .order_by(User.id)).all()
        tester = next(u for u in students if not tutor_chat.passed_quiz(db, session, u.id))
        now = config.now()
        print(f"Sesión {session.number} «{session.title}» · alumno de prueba: {tester.name} (sin quiz aprobado)\n")

        minute_ok = first_ok = 0
        print("1-2. Preguntas de la clase (minuto esperado ±90 s; primer turno con pregunta y ≤ 90 palabras)")
        for question, expected in QUESTIONS:
            res = answer_question(db, session, tester.id, question, now)
            cited = [c["sec"] for c in res.citations]
            good_min = any(abs(c - expected) <= TOLERANCE_S for c in cited)
            good_turn = res.answer.rstrip().endswith("?") and len(res.answer.split()) <= config.CHAT_MAX_WORDS
            minute_ok += good_min
            first_ok += good_turn
            print(f"[{'OK' if good_min else 'X '}|{'OK' if good_turn else 'X '}] {question} "
                  f"(esperado {tutor_chat.fmt_ts(expected)}, citó {', '.join(map(tutor_chat.fmt_ts, cited)) or '—'})")
            print(f"        → {res.answer[:200]}")

        print("\n3. Preguntas copiadas del quiz aprobado (pistas y explicación pedidas)")
        leaks = 0
        approved = quiz_service.approved_items(db, session.id)[:5]
        for item in approved:
            res = answer_question(db, session, tester.id, item.question, now)
            replies = [res.answer]
            for action in ("hint", "hint", "explain"):
                if res.thread_id is None:
                    break
                res = answer_question(db, session, tester.id, "", now, thread_id=res.thread_id, action=action)
                replies.append(res.answer)
            leaked = any(tutor_chat.leaks_answer(r, item) for r in replies)
            leaks += leaked
            print(f"[{'X ' if leaked else 'OK'}] {item.question[:90]} (nivel final {res.level})")

        print("\n4. Preguntas fuera de la clase")
        out_ok = 0
        for question in OUT_OF_CLASS:
            res = answer_question(db, session, tester.id, question, now)
            good = res.answer.startswith("Eso no se vio en esta clase")
            out_ok += good
            print(f"[{'OK' if good else 'X '}] {question} → {res.answer[:90]}")

    passed = minute_ok >= 16 and first_ok == 20 and leaks == 0 and out_ok == 5
    print(f"\nMinuto a ±90 s: {minute_ok}/20 (meta ≥ 16) · Primer turno con pregunta y ≤ 90 palabras: {first_ok}/20 "
          f"(meta 20) · Quiz revelado: {leaks}/{len(approved)} (meta 0) · Fuera de la clase: {out_ok}/5 (meta 5)")
    print(f"LLM {'simulado' if config.LLM_MOCK else config.LLM_MODEL_CHAT} · llamadas por uso: {llm.usage()}")
    shutil.rmtree(tmp.parent, ignore_errors=True)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
