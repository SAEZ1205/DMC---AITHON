"""Capítulos y tramos clave sin LLM (spec v2, 3.2).

1. La transcripción se agrupa en ventanas de ~60 s.
2. Con diapositivas: cada ventana se compara con cada diapositiva por BM25 (minúsculas, sin tildes, sin
   stopwords). Los puntajes se suavizan con una ventana deslizante de 3 y se asigna la secuencia de
   diapositivas de mayor puntaje que avanza en orden (programación dinámica); una diapositiva anterior solo
   reaparece si se repasa 2 min o más.
3. Sin diapositivas: segmentación tipo TextTiling (coseno de TF entre bloques de 3 ventanas, cortes en los
   valles más profundos) y título con los 3 términos de mayor TF-IDF.
4. Tamaño: se unen capítulos de menos de 4 min y se dividen los de más de 35 min; objetivo 5 a 12.
5. Los capítulos de logística nunca son tramo clave. Tramos clave: los de diapositivas marcadas por el
   docente o, sin marca, los de mayor densidad de términos del material (30–45 % de la clase).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from rank_bm25 import BM25Okapi

from .text import tokenize

WINDOW_S = 60.0
MIN_CHAPTER_S = 4 * 60
MAX_CHAPTER_S = 35 * 60
MIN_CHAPTERS, MAX_CHAPTERS = 5, 12
REVIEW_MIN_S = 2 * 60
SWITCH_PENALTY = 0.35
MIN_REL_SCORE = 0.2
KEY_SHARE_LOW, KEY_SHARE_HIGH = 0.30, 0.45
LOGISTICS_WORDS = {"pausa", "receso", "break", "tarea", "asistencia", "certificado"}
LOGISTICS_SLIDE_WORDS = LOGISTICS_WORDS | {"agenda", "acuerdo", "perfil", "docente", "gracias", "bienvenida"}
LOGISTICS_DENSITY = 0.02

SPOKEN_STOPWORDS = set("""
entonces bueno okay ok pues vamos aqui aca ahi eh ah mm mmm este esta estos estas eso esos esas ese
digamos tenemos hacemos estamos estoy esta estan van voy va vas ver vemos verdad cosa cosas bien asi ahora
solo tambien todo todos toda todas otro otra otros otras mismo misma algo nada muy mas menos poco mucho
porque cuando donde como cual quien les nos los las sus del una unos unas uno dos tres hay haber han hemos
he has fue fueron era eran seria sido estar estado tener tiene tienen tengo hacer hace hago hecho decir digo
dice dicen puede pueden puedo podemos quiero quieren quiere sea sean vez veces luego despues antes ya si no
alli alguien chicos chicas gente profe profesor clase favor gracias mejor claro exacto listo perfecto dale
""".split())


def tokens(text: str) -> list[str]:
    out = []
    for w in tokenize(text):
        if w in SPOKEN_STOPWORDS or w.isdigit():
            continue
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        out.append(w)
    return out


@dataclass
class SlideInfo:
    no: int
    title: str
    text: str
    is_key: bool = False
    toks: list[str] = field(default_factory=list)
    logistics: bool = False


@dataclass
class Draft:
    start: float
    end: float
    title: str
    source: str
    slide_from: int | None = None
    slide_to: int | None = None
    logistics: bool = False
    is_key: bool = False

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def is_logistics_slide(title: str, text: str = "") -> bool:
    raw = tokenize(f"{title} {' '.join(text.split()[:12])}")
    words = {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in raw} | set(raw)
    return bool(words & LOGISTICS_SLIDE_WORDS)


def make_slides(units) -> list[SlideInfo]:
    """`units`: filas MaterialUnit o cualquier objeto con unit_no, title, text e is_key."""
    slides = []
    for u in units:
        s = SlideInfo(u.unit_no, u.title or f"Diapositiva {u.unit_no}", u.text or "", bool(getattr(u, "is_key", False)))
        s.toks = tokens(f"{s.title} {s.title} {s.text}")
        s.logistics = is_logistics_slide(s.title, s.text)
        slides.append(s)
    return slides


def make_windows(segments, duration: float) -> list[list[str]]:
    n = max(1, math.ceil(duration / WINDOW_S))
    texts: list[list[str]] = [[] for _ in range(n)]
    for seg in segments:
        texts[min(n - 1, max(0, int(seg.start // WINDOW_S)))].append(seg.text)
    return [tokens(" ".join(t)) for t in texts]


# --- anclaje a diapositivas ---

def _score_matrix(windows: list[list[str]], slides: list[SlideInfo]) -> np.ndarray:
    bm25 = BM25Okapi([s.toks or ["_vacio_"] for s in slides])
    raw = np.array([bm25.get_scores(w) if w else np.zeros(len(slides)) for w in windows], dtype=float)
    raw = np.clip(raw, 0, None)
    best = raw.max(axis=1)
    ref = np.percentile(best[best > 0], 90) if (best > 0).any() else 1.0
    rel = raw / (ref or 1.0)
    rel[best / (ref or 1.0) < MIN_REL_SCORE] = 0
    kernel = np.array([1.0, 1.0, 1.0]) / 3
    return np.array([np.convolve(rel[:, j], kernel, mode="same") for j in range(rel.shape[1])]).T


def _monotonic_path(scores: np.ndarray) -> list[int]:
    w_count, s_count = scores.shape
    dp = np.zeros_like(scores)
    back = np.zeros(scores.shape, dtype=int)
    dp[0] = scores[0]
    back[0] = np.arange(s_count)
    for w in range(1, w_count):
        prev = dp[w - 1]
        run_best, run_arg = -np.inf, 0
        for s in range(s_count):
            stay = prev[s]
            jump = run_best - SWITCH_PENALTY
            if stay >= jump:
                dp[w, s], back[w, s] = stay + scores[w, s], s
            else:
                dp[w, s], back[w, s] = jump + scores[w, s], run_arg
            if prev[s] > run_best:
                run_best, run_arg = prev[s], s
    path = [int(np.argmax(dp[-1]))]
    for w in range(w_count - 1, 0, -1):
        path.append(int(back[w, path[-1]]))
    return path[::-1]


def _apply_reviews(path: list[int], scores: np.ndarray) -> list[int]:
    """Una diapositiva anterior vuelve a aparecer si es la más parecida durante 2 min o más (un repaso)."""
    need = max(2, math.ceil(REVIEW_MIN_S / WINDOW_S))
    best = scores.argmax(axis=1)
    out = list(path)
    i = 0
    while i < len(out):
        j = i
        while j < len(out) and best[j] == best[i]:
            j += 1
        s = int(best[i])
        if s < out[i] and j - i >= need and all(scores[k, s] > scores[k, out[k]] + 0.1 for k in range(i, j)):
            out[i:j] = [s] * (j - i)
        i = max(j, i + 1)
    return out


def align_slides(windows: list[list[str]], slides: list[SlideInfo]) -> tuple[list[int], list[int]]:
    """Índice de diapositiva por ventana: (camino en orden creciente, camino con repasos)."""
    scores = _score_matrix(windows, slides)
    path = _monotonic_path(scores)
    return path, _apply_reviews(path, scores)


# --- TextTiling ---

def depth_scores(windows: list[list[str]], block: int = 3) -> np.ndarray:
    """Profundidad del valle de similitud en cada frontera i (entre la ventana i-1 y la i)."""
    n = len(windows)
    vocab = {t: k for k, t in enumerate(sorted({t for w in windows for t in w}))}
    if n < 2 or not vocab:
        return np.zeros(n)
    tf = np.zeros((n, len(vocab)))
    for i, w in enumerate(windows):
        for t, c in Counter(w).items():
            tf[i, vocab[t]] = c
    sims = np.zeros(n)
    for i in range(1, n):
        a, b = tf[max(0, i - block):i].sum(0), tf[i:i + block].sum(0)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        sims[i] = float(a @ b / (na * nb)) if na and nb else 0.0
    depth = np.zeros(n)
    for i in range(1, n):
        left = sims[i]
        for k in range(i - 1, 0, -1):
            if sims[k] < left:
                break
            left = sims[k]
        right = sims[i]
        for k in range(i + 1, n):
            if sims[k] < right:
                break
            right = sims[k]
        depth[i] = (left - sims[i]) + (right - sims[i])
    return depth


def _texttiling_cuts(windows: list[list[str]], duration: float) -> list[int]:
    min_w = math.ceil(MIN_CHAPTER_S / WINDOW_S)
    target = min(MAX_CHAPTERS, max(MIN_CHAPTERS, round(duration / 1500)))
    depth = depth_scores(windows)
    cuts: list[int] = []
    for i in sorted(range(1, len(windows)), key=lambda k: (-depth[k], k)):
        if len(cuts) >= target - 1:
            break
        if i < min_w or len(windows) - i < min_w or any(abs(i - c) < min_w for c in cuts):
            continue
        cuts.append(i)
    return sorted(cuts)


def _tfidf_titles(windows: list[list[str]], ranges: list[tuple[int, int]]) -> list[str]:
    docs = [Counter(t for w in windows[a:b] for t in w if len(t) > 3) for a, b in ranges]
    df = Counter(t for d in docs for t in d)
    titles = []
    for d in docs:
        ranked = sorted(d, key=lambda t: (-d[t] * math.log((1 + len(docs)) / (1 + df[t])), t))[:3]
        title = " · ".join(ranked) if ranked else "Contenido de la clase"
        titles.append(title[:1].upper() + title[1:])
    return titles


# --- reglas de tamaño ---

def _merge_small(ranges: list[tuple[int, int]], labels: list[int] | None, logistic: list[bool]) -> list[tuple[int, int]]:
    min_w = math.ceil(MIN_CHAPTER_S / WINDOW_S)

    def is_log(r: tuple[int, int]) -> bool:
        return sum(logistic[r[0]:r[1]]) * 2 > (r[1] - r[0])

    ranges = list(ranges)
    while len(ranges) > 1:
        sizes = [b - a for a, b in ranges]
        small = [i for i, s in enumerate(sizes) if s < min_w]
        if not small and len(ranges) <= MAX_CHAPTERS:
            break
        if not small:
            pairs = [k for k in range(len(ranges) - 1) if is_log(ranges[k]) == is_log(ranges[k + 1])] \
                or list(range(len(ranges) - 1))
            k = min(pairs, key=lambda p: (sizes[p] + sizes[p + 1], p))
            ranges[k:k + 2] = [(ranges[k][0], ranges[k + 1][1])]
            continue
        i = min(small, key=lambda k: (sizes[k], k))
        if i == 0:
            j = 1
        elif i == len(ranges) - 1:
            j = i - 1
        else:
            left, right = ranges[i - 1], ranges[i + 1]
            same = [n for n, r in ((i - 1, left), (i + 1, right)) if is_log(r) == is_log(ranges[i])]
            j = same[0] if len(same) == 1 else (i - 1 if sizes[i - 1] <= sizes[i + 1] else i + 1)
        a, b = min(i, j), max(i, j)
        ranges[a:b + 1] = [(ranges[a][0], ranges[b][1])]
    return ranges


def _split_large(ranges: list[tuple[int, int]], depth: np.ndarray, force_min: bool) -> list[tuple[int, int]]:
    min_w = math.ceil(MIN_CHAPTER_S / WINDOW_S)
    max_w = math.floor(MAX_CHAPTER_S / WINDOW_S)
    ranges = list(ranges)
    while True:
        too_big = [i for i, (a, b) in enumerate(ranges) if b - a > max_w]
        if not too_big and not (force_min and len(ranges) < MIN_CHAPTERS):
            return ranges
        if len(ranges) >= MAX_CHAPTERS and not too_big:
            return ranges
        i = too_big[0] if too_big else max(range(len(ranges)), key=lambda k: ranges[k][1] - ranges[k][0])
        a, b = ranges[i]
        candidates = list(range(a + min_w, b - min_w + 1))
        if not candidates:
            return ranges
        cut = max(candidates, key=lambda k: (depth[k], -abs(k - (a + b) / 2)))
        ranges[i:i + 1] = [(a, cut), (cut, b)]


def _ranges_from_labels(labels: list[int]) -> list[tuple[int, int]]:
    ranges, start = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            ranges.append((start, i))
            start = i
    return ranges


# --- capítulos ---

def build_chapters(segments, duration: float, units=()) -> list[Draft]:
    windows = make_windows(segments, duration)
    slides = make_slides(units) if len(units) >= 3 else []
    depth = depth_scores(windows)

    if slides:
        path, idx = align_slides(windows, slides)
        logistic = [slides[k].logistics for k in idx]
        ranges = _ranges_from_labels(idx)
        source = "diapositivas"
    else:
        idx = None
        cuts = _texttiling_cuts(windows, duration)
        bounds = [0, *cuts, len(windows)]
        ranges = list(zip(bounds[:-1], bounds[1:]))
        logistic = [bool(set(w) & {"pausa", "receso", "break"}) and len(w) < 25 for w in windows]
        source = "texttiling"

    ranges = _merge_small(ranges, idx, logistic)
    ranges = _split_large(ranges, depth, force_min=True)
    ranges = _merge_small(ranges, idx, logistic)

    if slides:
        titles, spans = [], []
        for a, b in ranges:
            counts = Counter(idx[a:b])
            dominant = min(counts, key=lambda k: (-counts[k], k))
            titles.append(slides[dominant].title)
            spans.append((slides[path[a]].no, slides[path[b - 1]].no))
    else:
        titles, spans = _tfidf_titles(windows, ranges), [(None, None)] * len(ranges)

    seen: Counter = Counter()
    drafts = []
    for (a, b), title, (sf, st) in zip(ranges, titles, spans):
        seen[title] += 1
        if seen[title] > 1:
            title = f"{title} (parte {seen[title]})"
        words = [t for w in windows[a:b] for t in w]
        log_density = sum(t in LOGISTICS_WORDS for t in words) / max(1, len(words))
        is_log = sum(logistic[a:b]) * 2 > (b - a) or log_density >= LOGISTICS_DENSITY
        drafts.append(Draft(start=a * WINDOW_S, end=min(duration, b * WINDOW_S), title=title, source=source,
                            slide_from=sf, slide_to=st, logistics=is_log))
    if drafts:
        drafts[0].start, drafts[-1].end = 0.0, float(duration)
    mark_key(drafts, windows, slides, idx)
    return drafts


def mark_key(drafts: list[Draft], windows: list[list[str]], slides: list[SlideInfo], idx: list[int] | None) -> None:
    for d in drafts:
        d.is_key = False
    key_nos = {s.no for s in slides if s.is_key}
    if key_nos and idx is not None:
        for d in drafts:
            a, b = int(d.start // WINDOW_S), max(int(d.start // WINDOW_S) + 1, math.ceil(d.end / WINDOW_S))
            on_key = sum(slides[k].no in key_nos for k in idx[a:b])
            d.is_key = not d.logistics and on_key / max(1, b - a) >= 0.4
        return
    duration = drafts[-1].end if drafts else 0
    if not duration:
        return
    if slides:
        doc_freq = Counter(t for s in slides if not s.logistics for t in set(s.toks))
        content_slides = max(1, sum(not s.logistics for s in slides))
        vocab = {t for t, c in doc_freq.items() if c <= max(2, content_slides // 2) and len(t) > 2}
    else:
        df = Counter(t for w in windows for t in set(w))
        vocab = {t for t, c in df.items() if 2 <= c <= max(2, len(windows) // 4) and len(t) > 3}

    def density(d: Draft) -> float:
        a, b = int(d.start // WINDOW_S), math.ceil(d.end / WINDOW_S)
        words = [t for w in windows[a:b] for t in w]
        return sum(t in vocab for t in words) / max(1, len(words))

    share = 0.0
    for d in sorted((d for d in drafts if not d.logistics), key=lambda d: (-density(d), d.start)):
        part = (d.end - d.start) / duration
        if share >= KEY_SHARE_LOW:
            break
        if share + part > KEY_SHARE_HIGH and share > 0:
            continue
        d.is_key = True
        share += part


def key_share(drafts) -> float:
    total = max((d.end for d in drafts), default=0)
    return sum(d.end - d.start for d in drafts if d.is_key) / total if total else 0.0
