"""Resumen estructurado sin LLM (spec v2, 3.3): por capítulo, viñetas de sus diapositivas y 1–2 frases clave
de la transcripción elegidas por centralidad BM25, cada una con su minuto."""
from __future__ import annotations

import json
import re

import numpy as np
from rank_bm25 import BM25Okapi
from sqlmodel import Session, select

from ..models import Chapter, ClassSession, MaterialUnit, TranscriptSegment
from . import chaptering, materials

MAX_BULLETS = 4
MIN_WORDS, MAX_WORDS = 8, 45
MAX_SENTENCES_PER_CHAPTER = 250


def sentences(segments: list[TranscriptSegment]) -> list[tuple[float, str]]:
    """Une los segmentos y corta en . ? ! ; cada frase lleva el inicio del segmento donde empieza."""
    out: list[tuple[float, str]] = []
    buf, start = [], None
    for seg in segments:
        for piece in re.split(r"(?<=[.?!])\s+", seg.text.strip()):
            if not piece:
                continue
            if start is None:
                start = seg.start
            buf.append(piece)
            if re.search(r"[.?!]$", piece):
                out.append((start, " ".join(buf)))
                buf, start = [], None
    if buf:
        out.append((start or 0.0, " ".join(buf)))
    return [(t, " ".join(s.split())) for t, s in out]


def slide_bullets(units: list[MaterialUnit], limit: int = MAX_BULLETS) -> list[str]:
    bullets, seen = [], set()
    for u in units:
        for line in u.text.splitlines()[1:] if u.title else u.text.splitlines():
            line = line.strip(" •-–·\t")
            key = line.lower()
            if len(line) < 12 or len(line) > 160 or key in seen or key == (u.title or "").lower():
                continue
            seen.add(key)
            bullets.append(line)
            if len(bullets) >= limit:
                return bullets
    return bullets


def key_sentences(chapter_sentences: list[tuple[float, str]], slide_text: str, count: int) -> list[dict]:
    pool = [(t, s) for t, s in chapter_sentences if MIN_WORDS <= len(s.split()) <= MAX_WORDS]
    if len(pool) > MAX_SENTENCES_PER_CHAPTER:
        step = len(pool) / MAX_SENTENCES_PER_CHAPTER
        pool = [pool[int(i * step)] for i in range(MAX_SENTENCES_PER_CHAPTER)]
    toks = [chaptering.tokens(s) for _, s in pool]
    keep = [i for i, t in enumerate(toks) if len(t) >= 3]
    if not keep:
        return []
    pool, toks = [pool[i] for i in keep], [toks[i] for i in keep]
    bm25 = BM25Okapi(toks)
    centrality = np.zeros(len(pool))
    for j, q in enumerate(toks):
        scores = np.clip(bm25.get_scores(q), 0, None)
        scores[j] = 0
        centrality += scores
    centrality /= max(1, len(pool) - 1)
    score = centrality / (centrality.max() or 1)
    slide_toks = chaptering.tokens(slide_text)
    if slide_toks:
        to_slide = np.clip(bm25.get_scores(slide_toks), 0, None)
        score = 0.5 * score + 0.5 * to_slide / (to_slide.max() or 1)
    density = np.array([len(t) / len(s.split()) for t, (_, s) in zip(toks, pool)])
    score = score * (0.5 + density)
    chosen: list[int] = []
    for i in np.argsort(-score):
        if all(abs(pool[i][0] - pool[k][0]) >= 60 for k in chosen):
            chosen.append(int(i))
        if len(chosen) >= count:
            break
    return [{"start": pool[i][0], "text": pool[i][1]} for i in sorted(chosen, key=lambda k: pool[k][0])]


def build(db: Session, session: ClassSession) -> dict:
    chapters = db.exec(select(Chapter).where(Chapter.session_id == session.id).order_by(Chapter.start)).all()
    segments = db.exec(select(TranscriptSegment).where(TranscriptSegment.session_id == session.id)
                       .order_by(TranscriptSegment.start)).all()
    deck = {u.unit_no: u for u in materials.deck_units(db, session.id)}
    all_sentences = sentences(segments)
    out, dirty = [], False
    for ch in chapters:
        units = [deck[n] for n in range(ch.slide_from, (ch.slide_to or ch.slide_from) + 1) if n in deck] \
            if ch.slide_from else []
        if ch.slide_from and ch.slide_to and ch.slide_to < ch.slide_from:
            units = [deck[n] for n in range(ch.slide_to, ch.slide_from + 1) if n in deck]
        cached = json.loads(ch.key_sentences_json or "[]")
        if not cached:
            inside = [(t, s) for t, s in all_sentences if ch.start <= t < ch.end]
            count = 2 if ch.end - ch.start >= 10 * 60 else 1
            cached = key_sentences(inside, " ".join(f"{u.title} {u.text}" for u in units), count)
            if cached:
                ch.key_sentences_json = json.dumps(cached, ensure_ascii=False)
                db.add(ch)
                dirty = True
        out.append({"id": ch.id, "title": ch.title, "start": ch.start, "end": ch.end, "is_key": ch.is_key,
                    "logistics": ch.logistics, "slide_from": ch.slide_from, "slide_to": ch.slide_to,
                    "bullets": slide_bullets(units), "key_sentences": cached,
                    "text": "" if units else ch.summary})
    if dirty:
        db.commit()
    key_min = round(sum(c.end - c.start for c in chapters if c.is_key) / 60)
    n = len(chapters)
    header = (f"Esta clase tiene {n} {'capítulo' if n == 1 else 'capítulos'}; "
              f"los tramos clave suman {key_min} min.") if n else ""
    return {"header": header, "chapters": out, "approx_times": session.approx_times}
