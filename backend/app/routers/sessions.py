"""Vista de clase: datos de la sesión, video, transcripción, chat y quiz."""
from __future__ import annotations

import json
import random
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import config
from ..db import get_db
from ..deps import current_user, get_course, get_session_or_404
from ..models import Chapter, ChatMessage, QuizAttempt, QuizItem, SessionSummary, TranscriptSegment, User, WatchRange
from ..services import summary as summary_service
from ..services.attendance import live_metrics, overlap, union_ranges
from ..services.chat import answer_question
from ..services.recompute import compute_course, recompute_course
from ..services.text import parse_citations
from ..views import banner, compliance_json, iso

router = APIRouter(tags=["sesiones"])


def _chapter_json(ch: Chapter, seen: list[tuple[float, float]]) -> dict:
    length = ch.end - ch.start
    return {"id": ch.id, "start": ch.start, "end": ch.end, "title": ch.title, "summary": ch.summary,
            "is_key": ch.is_key, "validated": ch.validated,
            "seen": round(overlap(seen, [(ch.start, ch.end)]) / length, 3) if length else 0}


@router.get("/sessions/{session_id}")
def session_detail(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    s = get_session_or_404(db, session_id)
    course = get_course(db, s.course_id)
    now = config.now()
    duration = (s.ends_at - s.starts_at).total_seconds()
    chapters = db.exec(select(Chapter).where(Chapter.session_id == s.id).order_by(Chapter.start)).all()
    summary = db.get(SessionSummary, s.id)
    seen = union_ranges([(w.start_s, w.end_s) for w in db.exec(
        select(WatchRange).where(WatchRange.user_id == user.id, WatchRange.session_id == s.id, WatchRange.at <= now))])

    compliance, missed = None, None
    if user.role == config.STUDENT_ROLE:
        data, statuses = compute_course(db, course.id, user_ids=[user.id])
        st = statuses.get(user.id)
        info = next((x for x in data.sessions if x.id == s.id), None)
        if st and info and info.ends_at <= now:
            compliance = next(c for c in st.compliances if c.session_id == s.id)
            lm = live_metrics(st.student.intervals.get(s.id, []), info)
            absent = [(a, b) for a, b in lm.absent_ranges if b - a > 60]
            if absent:
                missed = {
                    "absent_ranges": absent,
                    "absent_minutes": round(sum(b - a for a, b in absent) / 60),
                    "attended": lm.attended,
                    "chapter_ids": [ch.id for ch in chapters if overlap(absent, [(ch.start, ch.end)]) > 0],
                }
    video_url = s.video_url or (f"/media/{s.id}.mp4" if s.video_path and Path(s.video_path).exists() else None)
    return {
        "session": {"id": s.id, "number": s.number, "title": s.title, "course_id": s.course_id,
                    "starts_at": iso(s.starts_at), "ends_at": iso(s.ends_at), "duration_s": duration,
                    "has_video": bool(video_url), "video_url": video_url,
                    "approx_times": s.approx_times, "real_content": s.real_content,
                    "processed": s.processed},
        "course": {"key_coverage_min": course.key_coverage_min, "quiz_pass_score": course.quiz_pass_score,
                   "questions_per_quiz": config.QUIZ_QUESTIONS_PER_ATTEMPT},
        "chapters": [_chapter_json(ch, seen) for ch in chapters],
        "keys_validated": bool(chapters) and all(ch.validated for ch in chapters if ch.is_key),
        "summary": summary.text if summary else "",
        "watched_ranges": seen,
        "compliance": compliance_json(compliance, now) if compliance else None,
        "missed": missed,
        "banner": banner(s.number, compliance, course),
    }


@router.get("/sessions/{session_id}/transcript")
def transcript(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    get_session_or_404(db, session_id)
    rows = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session_id)
                   .order_by(TranscriptSegment.start)).all()
    return [{"start": r.start, "end": r.end, "speaker": r.speaker, "text": r.text} for r in rows]


@router.get("/sessions/{session_id}/summary")
def session_summary(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return summary_service.build(db, get_session_or_404(db, session_id))


@router.get("/sessions/{session_id}/video")
def video(session_id: int, db: Session = Depends(get_db)):
    s = get_session_or_404(db, session_id)
    if not s.video_path or not Path(s.video_path).exists():
        raise HTTPException(404, "La sesión no tiene video")
    return FileResponse(s.video_path, media_type="video/mp4")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


@router.post("/sessions/{session_id}/chat")
def chat(session_id: int, body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    s = get_session_or_404(db, session_id)
    res = answer_question(db, s, user.id, body.message.strip(), config.now())
    return {"answer": res.answer, "citations": res.citations, "kind": res.kind}


@router.get("/sessions/{session_id}/chat")
def chat_history(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.exec(select(ChatMessage).where(ChatMessage.session_id == session_id, ChatMessage.user_id == user.id)
                   .order_by(ChatMessage.at, ChatMessage.id)).all()
    return [{"role": r.role, "text": r.text, "citations": parse_citations(r.text) if r.role == "assistant" else []}
            for r in rows[-config.CHAT_HISTORY_TURNS * 4:]]


@router.get("/sessions/{session_id}/quiz")
def get_quiz(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    get_session_or_404(db, session_id)
    items = db.exec(select(QuizItem).where(QuizItem.session_id == session_id)).all()
    if not items:
        raise HTTPException(404, "La sesión aún no tiene quiz")
    rng = random.Random()
    picked = rng.sample(items, min(config.QUIZ_QUESTIONS_PER_ATTEMPT, len(items)))
    out = []
    for q in picked:
        options = [{"idx": i, "text": t} for i, t in enumerate(json.loads(q.options_json))]
        rng.shuffle(options)
        out.append({"id": q.id, "question": q.question, "options": options})
    best = db.exec(select(QuizAttempt.score).where(QuizAttempt.user_id == user.id,
                                                   QuizAttempt.session_id == session_id)).all()
    return {"items": out, "best": max(best) if best else None, "attempts": len(best)}


class QuizIn(BaseModel):
    answers: dict[int, int]


@router.post("/sessions/{session_id}/quiz")
def post_quiz(session_id: int, body: QuizIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    s = get_session_or_404(db, session_id)
    course = get_course(db, s.course_id)
    if not body.answers or len(body.answers) > config.QUIZ_QUESTIONS_PER_ATTEMPT:
        raise HTTPException(400, f"Envía las respuestas de {config.QUIZ_QUESTIONS_PER_ATTEMPT} preguntas")
    items = {q.id: q for q in db.exec(select(QuizItem).where(QuizItem.session_id == s.id,
                                                            QuizItem.id.in_(list(body.answers)))).all()}
    if len(items) != len(body.answers):
        raise HTTPException(400, "Hay preguntas que no pertenecen a esta sesión")
    results, score = [], 0
    for item_id, chosen in body.answers.items():
        q = items[item_id]
        correct = chosen == q.answer_idx
        score += correct
        results.append({"id": q.id, "question": q.question, "chosen": chosen, "correct_idx": q.answer_idx,
                        "correct": correct, "options": json.loads(q.options_json), "explanation": q.explanation})
    now = config.now()
    db.add(QuizAttempt(user_id=user.id, session_id=s.id, score=score, answers_json=json.dumps(body.answers), at=now))
    db.commit()

    c = None
    if user.role == config.STUDENT_ROLE:
        st = recompute_course(db, course.id, now, user_ids=[user.id]).get(user.id)
        c = next((x for x in st.compliances if x.session_id == s.id), None) if st else None
    return {"score": score, "total": len(results), "passed": score >= course.quiz_pass_score,
            "pass_score": course.quiz_pass_score, "results": results,
            "compliance": compliance_json(c, now) if c else None, "banner": banner(s.number, c, course)}
