"""Conversión de resultados de los motores a JSON para la API."""
from __future__ import annotations

from datetime import datetime

from .models import Course
from .services.attendance import EN_VIVO, NO_CUMPLIDA, POR_RECUPERAR, RECUPERADA, Compliance, Indicators
from .services.text import fecha_corta, fecha_larga

STATUS_LABELS = {
    EN_VIVO: "En vivo",
    RECUPERADA: "Recuperada",
    POR_RECUPERAR: "Por recuperar",
    NO_CUMPLIDA: "No cumplida",
    "proxima": "Próxima",
}


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def compliance_json(c: Compliance, now: datetime) -> dict:
    return {
        "session_id": c.session_id,
        "number": c.number,
        "status": c.status,
        "status_label": STATUS_LABELS[c.status],
        "deadline": iso(c.deadline),
        "deadline_text": fecha_larga(c.deadline),
        "deadline_short": fecha_corta(c.deadline),
        "days_left": max(0, (c.deadline - now).days) if c.status == POR_RECUPERAR else None,
        "completed_at": iso(c.completed_at),
        "live_ratio": round(c.live.live_ratio, 3),
        "key_coverage": round(c.key_coverage, 3),
        "key_minutes_left": c.key_minutes_left,
        "best_quiz": c.best_quiz,
    }


def indicators_json(ind: Indicators) -> dict:
    return {
        "dictated": ind.dictated,
        "complied": ind.complied,
        "live": ind.live,
        "recovered": ind.recovered,
        "pending": ind.pending,
        "failed": ind.failed,
        "total": ind.total,
        "remaining_sessions": ind.total - ind.dictated,
        "threshold": ind.threshold,
        "effective": round(ind.effective, 4),
        "projection": round(ind.projection, 4),
        "recent_rate": round(ind.recent_rate, 4),
        "missing": ind.missing,
        "pending_key_minutes": ind.pending_key_minutes,
    }


def banner(number: int, c: Compliance | None, course: Course) -> dict | None:
    if c is None:
        return None
    key_min = course.key_coverage_min
    pct = round(c.key_coverage * 100)
    if c.status == EN_VIVO:
        return {"tone": "ok", "text": f"Sesión {number} – Asististe en vivo ({round(c.live.live_ratio * 100)} % "
                                      "de la clase). Esta sesión ya cuenta para tu asistencia."}
    if c.status == RECUPERADA:
        return {"tone": "ok", "text": f"Sesión {number} – Recuperada el {fecha_larga(c.completed_at)}. "
                                      "Esta sesión ya cuenta para tu asistencia."}
    if c.status == NO_CUMPLIDA:
        return {"tone": "bad", "text": f"Sesión {number} – El plazo para recuperarla venció el {fecha_larga(c.deadline)}. "
                                       "Puedes repasarla, pero ya no suma a tu asistencia."}
    live = "No asististe en vivo" if not c.live.attended else \
        f"Estuviste {round(c.live.live_ratio * 100)} % de la clase en vivo (mínimo {round(course.live_min_ratio * 100)} %)"
    coverage_ok = c.key_coverage >= key_min - 1e-9
    quiz_ok = c.best_quiz is not None and c.best_quiz >= course.quiz_pass_score
    if coverage_ok and not quiz_ok:
        todo = "ya viste los tramos clave; solo falta aprobar el quiz"
    elif quiz_ok and not coverage_ok:
        todo = f"ya aprobaste el quiz; te falta ver los tramos clave ({pct} % de {round(key_min * 100)} %)"
    else:
        todo = f"ve los tramos clave ({pct} %) y aprueba el quiz"
    return {"tone": "warn", "text": f"Sesión {number} – {live}. Para que cuente: {todo}. "
                                    f"Vence el {fecha_larga(c.deadline)}."}
