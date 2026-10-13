"""Procesamiento de una sesión con el LLM, una sola vez: capítulos, tramos clave, resumen y banco de quiz."""
from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path

from sqlmodel import Session, delete, select

from .. import config
from ..models import Chapter, ClassSession, QuizItem, SessionSummary, TranscriptSegment
from . import llm
from .text import fmt_ts
from .zoom_ingest import parse_vtt, store_segments

log = logging.getLogger("dmc.processing")


def transcript_lines(segments: list[TranscriptSegment], with_seconds: bool = False) -> str:
    lines = []
    for s in segments:
        stamp = f"{int(s.start)}s" if with_seconds else fmt_ts(s.start)
        who = f"{s.speaker}: " if s.speaker else ""
        lines.append(f"[{stamp}] {who}{s.text}")
    return "\n".join(lines)


def chapters_text(chapters) -> str:
    """Acepta filas Chapter o dicts con las mismas claves."""
    lines = []
    for c in chapters:
        d = c if isinstance(c, dict) else c.model_dump()
        key = " (tramo clave)" if d["is_key"] else ""
        lines.append(f"- [{fmt_ts(d['start'])}–{fmt_ts(d['end'])}] {d['title']}{key}: {d.get('summary', '')}")
    return "\n".join(lines)


def balance_key_chapters(chapters: list[dict], duration: float, low: float = 0.30, high: float = 0.50) -> list[dict]:
    """Ajusta los tramos clave para que sumen entre 30 % y 50 % de la clase."""
    def share() -> float:
        return sum(c["end"] - c["start"] for c in chapters if c["is_key"]) / duration if duration else 0

    while share() > high and sum(c["is_key"] for c in chapters) > 1:
        shortest = min((c for c in chapters if c["is_key"]), key=lambda c: c["end"] - c["start"])
        shortest["is_key"] = False
    candidates = sorted((c for c in chapters if not c["is_key"]), key=lambda c: c["end"] - c["start"])
    while share() < low and candidates:
        nxt = candidates.pop()
        nxt["is_key"] = True
        if share() > high:
            nxt["is_key"] = False
    return chapters


# --- modo simulado ---

def _mock_result(segments: list[TranscriptSegment], duration: float) -> dict:
    n = max(5, min(9, int(duration // 1200) or 5))
    step = duration / n
    chapters = []
    for i in range(n):
        a, b = i * step, (i + 1) * step if i < n - 1 else duration
        inside = [s for s in segments if a <= s.start < b]
        first = inside[0].text if inside else "Contenido de la clase"
        title = " ".join(first.split()[:7]).rstrip(",.;:")
        chapters.append({"start": round(a), "end": round(b), "title": f"Parte {i + 1}: {title}",
                         "summary": " ".join(s.text for s in inside[:2])[:240], "is_key": i % 2 == 1})
    summary = "\n\n".join(f"**{c['title']}.** {c['summary']} [{fmt_ts(c['start'])}]" for c in chapters)
    quiz = []
    for c in chapters:
        others = [o for o in chapters if o is not c]
        if len(others) < 3:
            break
        distractors = random.Random(int(c["start"])).sample(others, 3)
        options = [c["title"].split(": ", 1)[-1]] + [o["title"].split(": ", 1)[-1] for o in distractors]
        quiz.append({"question": f"¿Qué tema se trató a partir del minuto [{fmt_ts(c['start'])}]?",
                     "options": options, "answer_idx": 0,
                     "explanation": f"En [{fmt_ts(c['start'])}] se vio: {c['summary'][:120]}"})
    return {"chapters": chapters, "summary": summary, "quiz": quiz[: config.QUIZ_BANK_SIZE]}


# --- modo real ---

def _llm_result(segments: list[TranscriptSegment], duration: float) -> dict:
    transcript = transcript_lines(segments, with_seconds=True)
    chapters_out = llm.complete_json([{"role": "user", "content": llm.load_prompt(
        "chapters", DURATION=fmt_ts(duration), DURATION_S=str(int(duration)), TRANSCRIPT=transcript)}], llm.ChaptersOut)
    chapters = [c.model_dump() for c in chapters_out.chapters]
    chapters.sort(key=lambda c: c["start"])
    chapters[0]["start"], chapters[-1]["end"] = 0, duration
    chapters = balance_key_chapters(chapters, duration)

    ch_text = chapters_text(chapters)
    readable = transcript_lines(segments)
    summary = llm.complete_json([{"role": "user", "content": llm.load_prompt(
        "summary", CHAPTERS=ch_text, TRANSCRIPT=readable)}], llm.SummaryOut).summary

    key_segments = [s for s in segments if any(c["is_key"] and c["start"] <= s.start < c["end"] for c in chapters)]
    quiz_out = llm.complete_json([{"role": "user", "content": llm.load_prompt(
        "quiz", N=str(config.QUIZ_BANK_SIZE), KEY_CONTENT=transcript_lines(key_segments))}], llm.QuizOut)
    return {"chapters": chapters, "summary": summary, "quiz": [q.model_dump() for q in quiz_out.items]}


def _cache_path(transcript_path: str | None, segments: list[TranscriptSegment]) -> Path:
    source = Path(transcript_path).read_bytes() if transcript_path and Path(transcript_path).exists() \
        else "\n".join(s.text for s in segments).encode()
    mode = "mock" if config.LLM_MOCK else config.LLM_MODEL_CHAT
    return config.PROCESSED_DIR / f"{hashlib.sha1(source).hexdigest()[:16]}-{mode}.json"


def process_session(db: Session, session: ClassSession, force: bool = False) -> dict:
    segments = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                       .order_by(TranscriptSegment.start)).all()
    if not segments and session.transcript_path and Path(session.transcript_path).exists():
        store_segments(db, session, parse_vtt(Path(session.transcript_path)))
        db.commit()
        segments = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                           .order_by(TranscriptSegment.start)).all()
    if not segments:
        raise ValueError(f"La sesión {session.id} no tiene transcripción")
    duration = (session.ends_at - session.starts_at).total_seconds()

    cache = _cache_path(session.transcript_path, segments)
    reference = Path(session.transcript_path).with_name("processed.json") if session.transcript_path else None
    if cache.exists() and not force:
        result = json.loads(cache.read_text(encoding="utf-8"))
    elif config.LLM_MOCK and reference and reference.exists():
        result = json.loads(reference.read_text(encoding="utf-8"))
    elif config.LLM_MOCK:
        result = _mock_result(segments, duration)
    else:
        log.info("Procesando sesión %s con %s", session.id, config.LLM_MODEL_CHAT)
        result = _llm_result(segments, duration)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    save_result(db, session, result)
    return result


def save_result(db: Session, session: ClassSession, result: dict) -> None:
    db.exec(delete(Chapter).where(Chapter.session_id == session.id))
    db.exec(delete(QuizItem).where(QuizItem.session_id == session.id))
    for c in result["chapters"]:
        db.add(Chapter(session_id=session.id, start=c["start"], end=c["end"], title=c["title"],
                       summary=c.get("summary", ""), is_key=bool(c.get("is_key")), validated=False))
    existing = db.get(SessionSummary, session.id)
    if existing:
        existing.text = result["summary"]
        db.add(existing)
    else:
        db.add(SessionSummary(session_id=session.id, text=result["summary"]))
    rng = random.Random(session.id)
    for q in result["quiz"]:
        order = list(range(len(q["options"])))
        rng.shuffle(order)
        db.add(QuizItem(session_id=session.id, question=q["question"],
                        options_json=json.dumps([q["options"][i] for i in order], ensure_ascii=False),
                        answer_idx=order.index(q["answer_idx"]), explanation=q.get("explanation", "")))
    session.processed = True
    db.add(session)
    db.commit()
