"""Pone marcas de tiempo a un transcript.txt sin tiempos usando el audio del video (spec v2, 3.1).

Corre una sola vez y en local (no en Vercel). Requiere: pip install -r requirements-transcribe.txt

Uso: python scripts/align_transcript.py data/zoom/23 [--model small]

1. Transcribe el audio del video con faster-whisper (language="es", word_timestamps=True) solo para obtener
   los tiempos de cada palabra.
2. Alinea las palabras del .txt con las de Whisper (difflib.SequenceMatcher sobre palabras normalizadas).
   Cada frase del .txt toma el tiempo de su primera palabra alineada; las frases sin coincidencia toman el
   tiempo interpolado entre sus vecinas.
3. Conserva el texto del .txt (mejor ortografía y términos técnicos) y escribe transcript.vtt en la carpeta.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.text import strip_accents  # noqa: E402
from app.services.transcript_txt import _interpolate, to_vtt  # noqa: E402
from app.services.zoom_ingest import VttSegment  # noqa: E402


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9ñ]", "", strip_accents(word.lower()))


def sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+", flat) if s.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--model", default="small")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from faster_whisper import WhisperModel

    folder = args.folder
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8")) if (folder / "metadata.json").exists() else {}
    video = Path(meta.get("video_path") or folder / "recording.mp4")
    sents = sentences((folder / "transcript.txt").read_text(encoding="utf-8"))

    model = WhisperModel(args.model, device="auto", compute_type="int8")
    segments, _info = model.transcribe(str(video), language="es", word_timestamps=True, vad_filter=True)
    w_words, w_times = [], []
    for seg in segments:
        for w in seg.words or []:
            if norm(w.word):
                w_words.append(norm(w.word))
                w_times.append(w.start)
    print(f"Whisper: {len(w_words)} palabras con tiempo")

    t_words, owner = [], []
    for i, s in enumerate(sents):
        for w in s.split():
            if norm(w):
                t_words.append(norm(w))
                owner.append(i)
    starts: list[float | None] = [None] * len(sents)
    matcher = difflib.SequenceMatcher(a=t_words, b=w_words, autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            i = owner[block.a + k]
            if starts[i] is None:
                starts[i] = w_times[block.b + k]
    matched = sum(s is not None for s in starts)
    print(f"Frases alineadas: {matched}/{len(sents)}")

    words = [max(1, len(s.split())) for s in sents]
    times = _interpolate(starts, words)
    out = []
    for i, s in enumerate(sents):
        end = times[i + 1] if i + 1 < len(times) else times[i] + words[i] / 2.5
        out.append(VttSegment(round(times[i], 2), round(max(end, times[i] + 0.5), 2), "", s))
    (folder / "transcript.vtt").write_text(to_vtt(out), encoding="utf-8")
    print(f"Escrito {folder / 'transcript.vtt'}")


if __name__ == "__main__":
    main()
