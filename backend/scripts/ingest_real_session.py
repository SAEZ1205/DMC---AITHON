"""Ingesta de una sesión real entregada por el equipo (spec v2, 3.1).

Uso:
  python scripts/ingest_real_session.py "<carpeta>" --session 23
  python scripts/ingest_real_session.py "<carpeta>" --session 23 --no-db   # solo normaliza

<carpeta> puede venir tal cual (video .mp4, TRANSCRIPCION.txt o .vtt, Material/ con subcarpetas) o ser
data/zoom/<n>/ ya normalizada. Se copia a data/zoom/<n>/ (ignorado por Git): transcript.txt|vtt, materials/,
metadata.json (título, fecha, duración y ruta local del video; el video no se copia), quiz.json y
key_slides.json si existen. Luego, si la base existe: aplica el contenido a la sesión <n> (título, duración,
transcripción, materiales), arma capítulos, tramos clave y quiz, y recalcula el curso.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import ClassSession  # noqa: E402
from app.services import content  # noqa: E402
from app.services.real_session import apply_to_session, normalize  # noqa: E402
from app.services.recompute import recompute_course  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--session", type=int, required=True, help="número de la sesión en el curso de demo")
    ap.add_argument("--no-db", action="store_true", help="solo normaliza la carpeta")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    dest = normalize(args.folder, args.session)
    meta = json.loads((dest / "metadata.json").read_text(encoding="utf-8"))
    print(f"Carpeta normalizada: {dest}")
    print(f"  título: {meta['title']}\n  grabada: {meta.get('recorded_at')}\n  duración: {meta.get('duration_s')} s"
          f"\n  video: {meta.get('video_path') or meta.get('video_url') or 'sin video'}")
    if args.no_db:
        return

    init_db()
    with Session(engine) as db:
        s = db.exec(select(ClassSession).where(ClassSession.number == args.session)).first()
        if s is None:
            print("La base no tiene esa sesión: corre primero scripts/seed_demo.py (que ya aplica la carpeta).")
            return
        report = apply_to_session(db, s, dest)
        content.rebuild(db, s)
        recompute_course(db, s.course_id, config.now())
        print(f"Sesión {s.number} actualizada: {report}")


if __name__ == "__main__":
    main()
