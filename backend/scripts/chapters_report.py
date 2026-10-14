"""Tabla de capítulos de una sesión para revisarlos contra el video (criterio: 80 % de los límites a ≤ 2 min).

Uso:
  python scripts/chapters_report.py 23            # capítulos guardados en la base para la sesión número 23
  python scripts/chapters_report.py 23 --fresh    # los vuelve a calcular desde data/zoom/23/ sin tocar la base
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Chapter, ClassSession  # noqa: E402
from app.services import chaptering, materials  # noqa: E402
from app.services.real_session import load_metadata  # noqa: E402
from app.services.text import fmt_ts  # noqa: E402
from app.services.transcript_txt import parse_file  # noqa: E402
from app.services.zoom_ingest import parse_vtt  # noqa: E402


def fresh(number: int) -> list[dict]:
    folder = config.ZOOM_DIR / str(number)
    meta = load_metadata(folder)
    vtt, txt = folder / "transcript.vtt", folder / "transcript.txt"
    segments = parse_vtt(vtt) if vtt.exists() else parse_file(txt, meta.get("duration_s")).segments
    duration = float(meta.get("duration_s") or segments[-1].end)
    deck = next((materials.extract(p) for p in materials.material_files(folder / "materials")
                 if p.suffix.lower() in {".pptx", ".pdf"}), [])
    key = materials.load_key_slides(folder)
    for u in deck:
        u.is_key = u.unit_no in key
    return [d.as_dict() for d in chaptering.build_chapters(segments, duration, deck)]


def stored(number: int) -> list[dict]:
    with Session(engine) as db:
        s = db.exec(select(ClassSession).where(ClassSession.number == number)).first()
        if not s:
            sys.exit(f"No hay sesión {number} en la base")
        rows = db.exec(select(Chapter).where(Chapter.session_id == s.id).order_by(Chapter.start)).all()
        return [r.model_dump() for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("number", type=int)
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rows = fresh(args.number) if args.fresh else stored(args.number)
    print(f"{'#':>2}  {'inicio':>8}  {'fin':>8}  {'min':>4}  {'diap.':>7}  {'tipo':<9}  título")
    for i, c in enumerate(rows, 1):
        slides = f"{c['slide_from']}–{c['slide_to']}" if c.get("slide_from") else "—"
        kind = "logística" if c.get("logistics") else ("clave" if c["is_key"] else "")
        print(f"{i:>2}  {fmt_ts(c['start']):>8}  {fmt_ts(c['end']):>8}  {round((c['end'] - c['start']) / 60):>4}  "
              f"{slides:>7}  {kind:<9}  {c['title']}")
    total = rows[-1]["end"] if rows else 0
    key = sum(c["end"] - c["start"] for c in rows if c["is_key"])
    print(f"\n{len(rows)} capítulos; tramos clave {round(key / 60)} min ({key / total:.0%} de la clase)." if total else "")
    print("Revisión: abre el video en cada inicio y anota si el cambio de tema real está a ≤ 2 min.")


if __name__ == "__main__":
    main()
