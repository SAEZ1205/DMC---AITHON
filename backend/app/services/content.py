"""Contenido de una sesión sin LLM: capítulos, tramos clave y banco de quiz inicial (spec v2, 3.2–3.4).

- Sesión con contenido real (data/zoom/<n>/): capítulos por diapositivas o TextTiling (chaptering.py) y,
  si existe, el banco inicial de `quiz.json` (cada pregunta puede traer `status` y `source`; por defecto,
  aprobada y del docente).
- Sesión de muestra: capítulos y banco de la referencia `processed.json` junto al VTT de muestra, ya revisados
  por el equipo, para que el historial simulado de la cohorte siga midiendo los mismos tramos clave.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from sqlmodel import Session, delete, select

from ..models import Chapter, ClassSession, QuizItem, SessionSummary, TranscriptSegment
from . import chaptering, materials
from .zoom_ingest import parse_vtt, store_segments


def segments_of(db: Session, session: ClassSession) -> list[TranscriptSegment]:
    rows = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                   .order_by(TranscriptSegment.start)).all()
    if not rows and session.transcript_path and Path(session.transcript_path).suffix == ".vtt" \
            and Path(session.transcript_path).exists():
        store_segments(db, session, parse_vtt(Path(session.transcript_path)))
        db.commit()
        rows = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                       .order_by(TranscriptSegment.start)).all()
    return rows


def reference_of(session: ClassSession) -> dict | None:
    if session.real_content or not session.transcript_path:
        return None
    path = Path(session.transcript_path).with_name("processed.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def store_chapters(db: Session, session: ClassSession, drafts: list[dict]) -> None:
    db.exec(delete(Chapter).where(Chapter.session_id == session.id))
    for d in drafts:
        db.add(Chapter(session_id=session.id, start=d["start"], end=d["end"], title=d["title"],
                       summary=d.get("summary", ""), is_key=bool(d.get("is_key")), validated=False,
                       slide_from=d.get("slide_from"), slide_to=d.get("slide_to"),
                       source=d.get("source", "texttiling"), logistics=bool(d.get("logistics"))))


def store_quiz(db: Session, session: ClassSession, items: list[dict], status: str = "aprobada",
               source: str = "docente") -> None:
    db.exec(delete(QuizItem).where(QuizItem.session_id == session.id))
    rng = random.Random(session.id)
    for q in items:
        order = list(range(len(q["options"])))
        rng.shuffle(order)
        db.add(QuizItem(session_id=session.id, question=q["question"],
                        options_json=json.dumps([q["options"][i] for i in order], ensure_ascii=False),
                        answer_idx=order.index(q["answer_idx"]), explanation=q.get("explanation", ""),
                        minute=q.get("minute"), slide=q.get("slide"), status=q.get("status", status),
                        source=q.get("source", source)))


def build_chapters(db: Session, session: ClassSession) -> list[dict]:
    segments = segments_of(db, session)
    if not segments:
        raise ValueError(f"La sesión {session.id} no tiene transcripción")
    duration = (session.ends_at - session.starts_at).total_seconds()
    deck = materials.deck_units(db, session.id)
    return [d.as_dict() for d in chaptering.build_chapters(segments, duration, deck)]


def rebuild(db: Session, session: ClassSession) -> dict:
    reference = reference_of(session)
    segments_of(db, session)
    if reference:
        drafts = [{**c, "source": "muestra", "logistics": not c["is_key"] and bool(
                   set(chaptering.tokens(c["title"])) & (chaptering.LOGISTICS_WORDS | {"logistica", "cierre"}))}
                  for c in reference["chapters"]]
        store_chapters(db, session, drafts)
        store_quiz(db, session, reference["quiz"])
        summary = db.get(SessionSummary, session.id) or SessionSummary(session_id=session.id, text="")
        summary.text = reference.get("summary", "")
        db.add(summary)
    else:
        drafts = build_chapters(db, session)
        store_chapters(db, session, drafts)
        quiz_file = Path(session.transcript_path).with_name("quiz.json") if session.transcript_path else None
        if quiz_file and quiz_file.exists():
            store_quiz(db, session, json.loads(quiz_file.read_text(encoding="utf-8")))
    session.processed = True
    db.add(session)
    db.commit()
    total = max((d["end"] for d in drafts), default=0)
    key = sum(d["end"] - d["start"] for d in drafts if d["is_key"])
    return {"chapters": len(drafts), "key_share": round(key / total, 2) if total else 0}
