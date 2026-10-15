"""Recalcula el contenido de una sesión sin LLM: capítulos, tramos clave y banco inicial del quiz.

Uso:
  python scripts/process_session.py <session_id>
  python scripts/process_session.py --all

Si existe data/zoom/<session_id>/ se ingesta primero (participantes, chat, transcripción, video y, si hay
transcript.txt o materials/, el contenido real de la sesión). Los capítulos vuelven a quedar sin validar.
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
from app.services import content  # noqa: E402
from app.services.real_session import apply_to_session, has_real_content  # noqa: E402
from app.services.zoom_ingest import ingest_session  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_id", nargs="?", type=int)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not args.all and args.session_id is None:
        ap.error("indica un session_id o --all")

    init_db()
    with Session(engine) as db:
        query = select(ClassSession).order_by(ClassSession.id)
        if not args.all:
            query = query.where(ClassSession.id == args.session_id)
        sessions = db.exec(query).all()
        if not sessions:
            sys.exit(f"No existe la sesión {args.session_id}")
        for s in sessions:
            folder = config.ZOOM_DIR / str(s.id)
            if folder.exists():
                print(f"Sesión {s.id}: ingesta {ingest_session(db, s, folder)}")
                if has_real_content(folder):
                    apply_to_session(db, s, folder)
                db.commit()
            report = content.rebuild(db, s)
            print(f"Sesión {s.id}: {report['chapters']} capítulos, tramos clave {round(report['key_share'] * 100)} %")


if __name__ == "__main__":
    main()
