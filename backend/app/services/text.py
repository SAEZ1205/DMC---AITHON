"""Utilidades de texto: marcas de tiempo, fechas en español y búsqueda BM25 en la transcripción."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime

from rank_bm25 import BM25Okapi

TS_PATTERN = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,3}):(\d{2})\]")

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
          "octubre", "noviembre", "diciembre"]

STOPWORDS = set("""
a al algo como con cual cuales cuando de del el en es esa ese eso esta este esto fue ha hay la las le lo los mas me mi
no o para pero por que se si sin su sus te tu un una uno y ya yo qué cómo cuál cuándo dónde donde explico explicó
profe clase dijo dice hace hacer puede puedo sirve usar usa sobre tiene tienen ser son era vez veces
""".split())


def fmt_ts(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_citations(text: str) -> list[int]:
    out = []
    for h, m, s in TS_PATTERN.findall(text):
        total = int(h or 0) * 3600 + int(m) * 60 + int(s)
        if total not in out:
            out.append(total)
    return out


def fecha_larga(value: datetime) -> str:
    return f"{DAYS[value.weekday()]} {value.day} de {MONTHS[value.month - 1]}"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


RECOVERY_SPEED = 1.5


def key_time(minutes: int) -> str:
    """«~77 min de tramos clave (~52 min a 1.5×)»."""
    return f"~{minutes} min de tramos clave (~{math.ceil(minutes / RECOVERY_SPEED)} min a {RECOVERY_SPEED:g}×)"


def fecha_corta(value: datetime) -> str:
    return value.strftime("%d/%m")


def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", strip_accents(text.lower()))
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


class SegmentIndex:
    def __init__(self, docs: list[str]):
        self.docs = docs
        self.tokens = [tokenize(d) for d in docs]
        self.bm25 = BM25Okapi(self.tokens) if docs else None

    def search(self, query: str, k: int = 8) -> list[tuple[int, float]]:
        if not self.bm25:
            return []
        q = tokenize(query)
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [(i, float(scores[i])) for i in ranked if scores[i] > 0]
