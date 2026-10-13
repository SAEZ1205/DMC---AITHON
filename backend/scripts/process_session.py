"""Procesa una sesión con el LLM una sola vez: capítulos, tramos clave, resumen y banco de quiz.

Uso:
  python scripts/process_session.py <session_id> [--force]
  python scripts/process_session.py --all [--force]

Si existe data/zoom/<session_id>/ se ingesta primero (participantes, chat, transcripción y video).
El resultado se guarda en la base y en data/processed/ (caché por transcripción: sesiones con la misma
transcripción no vuelven a llamar al modelo). --force ignora la caché.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import ClassSession  # noqa: E402
from app.services.processing import process_session  # noqa: E402
from app.services.zoom_ingest import ingest_session  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_id", nargs="?", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not args.all and args.session_id is None:
        ap.error("indica un session_id o --all")

    init_db()
    print(f"LLM: {'simulado' if config.LLM_MOCK else config.LLM_MODEL_CHAT} ({config.LLM_BASE_URL})")
    with Session(engine) as db:
        query = select(ClassSession).order_by(ClassSession.id)
        if not args.all:
            query = query.where(ClassSession.id == args.session_id)
        sessions = db.exec(query).all()
        if not sessions:
            sys.exit(f"No existe la sesión {args.session_id}")
        for i, s in enumerate(sessions):
            folder = config.ZOOM_DIR / str(s.id)
            if folder.exists():
                report = ingest_session(db, s, folder)
                print(f"Sesión {s.id}: ingesta {report}")
            result = process_session(db, s, force=args.force and i == 0)
            keys = sum(c["is_key"] for c in result["chapters"])
            print(f"Sesión {s.id}: {len(result['chapters'])} capítulos ({keys} clave), "
                  f"resumen de {len(result['summary'].split())} palabras, {len(result['quiz'])} preguntas")


if __name__ == "__main__":
    main()
