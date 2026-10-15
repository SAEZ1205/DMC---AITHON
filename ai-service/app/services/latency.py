"""
Medición de latencia por etapa de cada pregunta del tutor (solo NÚMEROS y estados; jamás texto de preguntas, de la clase ni claves).

Cada solicitud abre una `LatencyTrace` (ContextVar: sirve a hilos concurrentes sin tocar firmas). Las etapas se miden con `time.perf_counter()`:

  guardian_ms        pertinencia (heurística; incluye la llamada al modelo si el guardián LLM dudó)
  retrieval_ms       búsqueda de segmentos y selección de evidencia
  prompt_ms          armado del prompt (mensajes, glosario, saneado)
  egress_ms          barrera final de privacidad (re-enmascarado + verificación de nombres)
  provider_ms        espera a NVIDIA (red + razonamiento + generación): NO incluye al guardián LLM
  guardian_provider_ms  espera a NVIDIA de la llamada del guardián (si hubo)
  verify_ms          parseo del JSON del modelo y verificación de citas
  persist_ms         guardado del turno y telemetría
  total_ms           de principio a fin dentro del backend
  first_text_ms      (solo streaming) hasta el primer texto visible

El navegador agrega por su cuenta el tiempo que percibe el estudiante; el backend no puede medirlo.
"""
import contextvars
import statistics
import threading
import time
from collections import Counter, deque
from contextlib import contextmanager
from typing import Any, Deque, Dict, Iterator, Optional

_TRACE: "contextvars.ContextVar[Optional[LatencyTrace]]" = contextvars.ContextVar("latency_trace", default=None)
MAX_SAMPLES = 200


class LatencyTrace:
    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self.ms: Dict[str, float] = {}
        self.counts: Dict[str, int] = {}
        self.first_text_ms: Optional[float] = None

    def add(self, name: str, ms: float) -> None:
        self.ms[name] = self.ms.get(name, 0.0) + ms

    def count(self, name: str, value: int) -> None:
        self.counts[name] = self.counts.get(name, 0) + int(value)

    def set_count(self, name: str, value: int) -> None:
        self.counts[name] = int(value)

    def mark_first_text(self) -> None:
        if self.first_text_ms is None:
            self.first_text_ms = (time.perf_counter() - self._t0) * 1000

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    def public(self, status: str) -> Dict[str, Any]:
        """Representación segura para la respuesta y las métricas: milisegundos redondeados, contadores y un estado."""
        out: Dict[str, Any] = {"status": status, "total_ms": round(self.elapsed_ms(), 1)}
        out.update({k: round(v, 1) for k, v in sorted(self.ms.items())})
        if self.first_text_ms is not None:
            out["first_text_ms"] = round(self.first_text_ms, 1)
        out["counts"] = dict(sorted(self.counts.items()))
        return out


def current() -> Optional[LatencyTrace]:
    return _TRACE.get()


@contextmanager
def trace_scope() -> Iterator[LatencyTrace]:
    """Abre la traza de una solicitud. Si ya hay una abierta (llamada anidada) se reutiliza."""
    existing = _TRACE.get()
    if existing is not None:
        yield existing
        return
    trace = LatencyTrace()
    token = _TRACE.set(trace)
    try:
        yield trace
    finally:
        _TRACE.reset(token)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Mide un bloque y lo suma a la traza activa; sin traza activa no hace nada."""
    trace = _TRACE.get()
    if trace is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        trace.add(name, (time.perf_counter() - start) * 1000)


def count(name: str, value: int) -> None:
    trace = _TRACE.get()
    if trace is not None:
        trace.count(name, value)


def set_count(name: str, value: int) -> None:
    trace = _TRACE.get()
    if trace is not None:
        trace.set_count(name, value)


class LatencyStats:
    """Últimas mediciones en memoria (se pierden al reiniciar a propósito: no hay nada que proteger ni migrar)."""

    def __init__(self, size: int = MAX_SAMPLES) -> None:
        self._samples: Deque[Dict[str, Any]] = deque(maxlen=size)
        self._lock = threading.Lock()

    def add(self, public: Dict[str, Any]) -> None:
        with self._lock:
            self._samples.append(public)

    def summary(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return None

        def series(key: str, only: Optional[str] = None):
            return [s[key] for s in samples if key in s and (only is None or s.get("status") == only)]

        def stats(values):
            if not values:
                return None
            ordered = sorted(values)
            return {"n": len(ordered), "median_ms": round(statistics.median(ordered), 1),
                    "p90_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1), "max_ms": round(ordered[-1], 1)}
        return {
            "samples": len(samples),
            "by_status": dict(Counter(s.get("status", "?") for s in samples)),
            "total": stats(series("total_ms")),
            "total_live": stats(series("total_ms", "live")),
            "provider": stats(series("provider_ms")),
            "first_text": stats(series("first_text_ms")),
            "prompt_chars": stats([s["counts"]["prompt_chars"] for s in samples if "prompt_chars" in s.get("counts", {})]),
        }
