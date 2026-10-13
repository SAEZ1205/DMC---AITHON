"""Criterio de aceptación del chat: 20 preguntas sobre la sesión de muestra.

Cuenta como correcta una respuesta que cita un minuto a menos de 2 minutos del momento esperado,
o que responde «Eso no se vio en esta clase…» cuando la pregunta no se trató en la clase.
Meta: 16 de 20 o más. Para medir el modelo real, configura LLM_API_KEY en backend/.env.

Uso: python scripts/eval_chat.py [--session 1]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import ClassSession, User  # noqa: E402
from app.services.chat import answer_question  # noqa: E402

NOT_FOUND = None
QUESTIONS: list[tuple[str, int | None]] = [
    ("¿Cuál es la diferencia entre la URL de test y la de producción del webhook?", 900),
    ("¿Qué método HTTP conviene usar para recibir formularios en un webhook?", 1110),
    ("¿Qué hago si la otra aplicación necesita una respuesta inmediata del webhook?", 1330),
    ("¿Cómo puedo proteger mi webhook para que no cualquiera envíe datos?", 1830),
    ("Me sale error 401 en el HTTP Request, ¿qué significa?", 2720),
    ("¿Cómo traigo todas las páginas de resultados de una API?", 2940),
    ("¿Para qué sirve la opción Batching?", 3150),
    ("¿Cómo se escribe una expresión en n8n?", 3320),
    ("¿Por qué un nodo se ejecuta varias veces en una misma ejecución?", 3720),
    ("¿Qué nodo reemplazó al nodo Set?", 3930),
    ("¿Hasta cuándo se puede entregar la tarea de hoy?", 4860),
    ("¿La asistencia cuenta si veo la grabación?", 5100),
    ("¿Qué diferencia hay entre Append Row y Update Row en Google Sheets?", 6000),
    ("¿Qué elementos se le conectan al nodo AI Agent?", 7740),
    ("¿Qué tipo de memoria usamos para el agente y con cuántos mensajes?", 8310),
    ("¿Para qué sirve el nodo Error Trigger?", 9600),
    ("¿Cómo instalo Kubernetes en mi laptop?", NOT_FOUND),
    ("¿Cómo conecto n8n con Salesforce?", NOT_FOUND),
    ("¿Cuál es la capital de Francia?", NOT_FOUND),
    ("¿Qué es una red neuronal convolucional?", NOT_FOUND),
]
TOLERANCE_S = 120


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", type=int, default=1)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    with Session(engine) as db:
        session = db.get(ClassSession, args.session)
        tester = db.exec(select(User).where(User.role == "coordinador")).first()
        ok = 0
        for question, expected in QUESTIONS:
            res = answer_question(db, session, tester.id, question, config.now())
            if expected is None:
                good = "no se vio en esta clase" in res.answer.lower()
            else:
                good = any(abs(c - expected) <= TOLERANCE_S for c in res.citations)
            ok += good
            print(f"[{'OK' if good else 'X '}] {question}\n      → {res.answer[:220]}")
    print(f"\nResultado: {ok}/20 (meta ≥ 16) con LLM {'simulado' if config.LLM_MOCK else config.LLM_MODEL_CHAT}")
    sys.exit(0 if ok >= 16 else 1)


if __name__ == "__main__":
    main()
