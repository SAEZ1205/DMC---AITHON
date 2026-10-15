"""Transcripción en texto plano (.txt) con o sin marcas de tiempo (sección 3.1 de la spec v2).

Formatos reconocidos (una marca puede ser m:ss, mm:ss o h:mm:ss, con o sin fracción):
  [hh:mm:ss] Nombre: texto        hh:mm:ss Nombre: texto
  Nombre hh:mm:ss  (texto en la línea siguiente, formato de Zoom)
  hh:mm:ss --> hh:mm:ss  (bloques VTT pegados como texto)
  hh:mm:ss  (sola en una línea, texto en las siguientes)
Si al menos el 80 % de los bloques tiene tiempo, se usa; si no, el tiempo se reparte en proporción
a la cantidad de palabras sobre la duración de la clase (minutos aproximados).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .zoom_ingest import VttSegment

TS = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?"
RE_ARROW = re.compile(rf"^\s*({TS})\s*-->\s*({TS})")
RE_BARE = re.compile(rf"^\s*[\[(]?({TS})[\])]?\s*$")
RE_LEADING = re.compile(rf"^\s*[\[(]?({TS})[\])]?\s+(.+)$")
RE_TRAILING = re.compile(rf"^\s*(.{{1,60}}?)\s+[\[(]?({TS})[\])]?\s*$")
RE_SPEAKER = re.compile(r"^([^:.?!¿¡]{1,40}):\s+(.+)$")
MIN_TIMED_RATIO = 0.80
WORDS_PER_SECOND = 2.5


@dataclass
class _Block:
    start: float | None
    speaker: str
    lines: list[str]
    end: float | None = None


@dataclass
class ParsedTranscript:
    segments: list[VttSegment]
    approx_times: bool
    timed_ratio: float


def parse_ts(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def _split_speaker(text: str) -> tuple[str, str]:
    m = RE_SPEAKER.match(text)
    if m and not m.group(1).strip().isdigit():
        return m.group(1).strip(), m.group(2).strip()
    return "", text.strip()


def _looks_like_name(value: str) -> bool:
    words = value.split()
    return 0 < len(words) <= 6 and all(w[:1].isupper() for w in words)


def _blocks(lines: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    current: _Block | None = None

    def open_block(start: float | None, speaker: str = "", text: str = "") -> None:
        nonlocal current
        current = _Block(start, speaker, [text] if text else [])
        blocks.append(current)

    for raw in lines:
        line = raw.strip().lstrip("\ufeff")
        if not line:
            if current is not None and current.lines:
                current = None
            continue
        if line.upper() == "WEBVTT" or line.isdigit():
            continue
        if m := RE_ARROW.match(line):
            open_block(parse_ts(m.group(1)))
            current.end = parse_ts(m.group(2))
        elif m := RE_BARE.match(line):
            open_block(parse_ts(m.group(1)))
        elif m := RE_LEADING.match(line):
            speaker, text = _split_speaker(m.group(2))
            open_block(parse_ts(m.group(1)), speaker, text)
        elif (m := RE_TRAILING.match(line)) and _looks_like_name(m.group(1)):
            open_block(parse_ts(m.group(2)), m.group(1).strip())
        else:
            if current is None:
                open_block(None)
            if not current.lines and not current.speaker:
                current.speaker, line = _split_speaker(line)
            current.lines.append(line)
    return [b for b in blocks if b.lines]


def parse_text(text: str, duration_s: float | None = None) -> ParsedTranscript:
    blocks = _blocks(text.splitlines())
    if not blocks:
        return ParsedTranscript([], False, 0.0)
    timed = sum(b.start is not None for b in blocks)
    ratio = timed / len(blocks)
    words = [max(1, len(" ".join(b.lines).split())) for b in blocks]

    if ratio >= MIN_TIMED_RATIO:
        approx = False
        starts = _interpolate([b.start for b in blocks], words)
    else:
        approx = True
        total = sum(words)
        span = duration_s or total / WORDS_PER_SECOND
        starts, acc = [], 0
        for w in words:
            starts.append(span * acc / total)
            acc += w

    segments = []
    for i, (block, start) in enumerate(zip(blocks, starts)):
        if block.end is not None and not approx and block.end > start:
            end = block.end
        elif i + 1 < len(starts):
            end = max(start + 0.5, starts[i + 1])
        elif approx and duration_s:
            end = max(duration_s, start + 0.5)
        else:
            end = start + words[i] / WORDS_PER_SECOND
            if duration_s:
                end = min(max(end, start + 0.5), max(duration_s, start + 0.5))
        segments.append(VttSegment(round(start, 2), round(end, 2), block.speaker, " ".join(block.lines)))
    return ParsedTranscript(segments, approx, ratio)


def _interpolate(starts: list[float | None], words: list[int]) -> list[float]:
    """Completa los bloques sin tiempo con el tiempo interpolado entre sus vecinos (o por palabras en los extremos)."""
    out = list(starts)
    n = len(out)
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < n and out[j] is None:
            j += 1
        prev = out[i - 1] if i > 0 else None
        nxt = out[j] if j < n else None
        if prev is None and nxt is None:
            prev = 0.0
        for k in range(i, j):
            if prev is not None and nxt is not None:
                out[k] = prev + (nxt - prev) * (k - i + 1) / (j - i + 1)
            elif prev is not None:
                out[k] = out[k - 1] + words[k - 1] / WORDS_PER_SECOND if k > 0 else prev
            else:
                out[k] = max(0.0, nxt - sum(words[k:j]) / WORDS_PER_SECOND)
        i = j
    # el texto puede traer marcas desordenadas: se fuerza orden creciente
    for k in range(1, n):
        out[k] = max(out[k], out[k - 1])
    return [float(x) for x in out]


def parse_file(path: Path, duration_s: float | None = None) -> ParsedTranscript:
    return parse_text(path.read_text(encoding="utf-8", errors="replace"), duration_s)


def to_vtt(segments: list[VttSegment]) -> str:
    def stamp(s: float) -> str:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}"

    out = ["WEBVTT", ""]
    for i, seg in enumerate(segments, 1):
        who = f"{seg.speaker}: " if seg.speaker else ""
        out += [str(i), f"{stamp(seg.start)} --> {stamp(seg.end)}", f"{who}{seg.text}", ""]
    return "\n".join(out)
