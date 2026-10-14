"""Estados del alumno y motivos en lenguaje claro (sección 6.5)."""
from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..models import Course
from .attendance import Indicators
from .features import CurrentFeatures

ACTIVO, EN_ADAPTACION, EN_RIESGO, INACTIVO = "activo", "en_adaptacion", "en_riesgo", "inactivo"
STATES = (ACTIVO, EN_ADAPTACION, EN_RIESGO, INACTIVO)

TOPIC_LABELS = {
    "tiempo": "falta de tiempo",
    "trabajo": "el trabajo",
    "contenido": "el contenido",
    "ritmo": "el ritmo del curso",
    "satisfaccion": "su satisfacción con el curso",
    "otro": "otro tema",
}


@dataclass
class RiskResult:
    state: str
    reasons: list[str]


def _pct(value: float) -> str:
    return f"{round(value * 100)} %"


def classify(cur: CurrentFeatures, ind: Indicators, course: Course) -> RiskResult:
    if cur.days_since_activity >= config.INACTIVE_DAYS:
        return RiskResult(INACTIVO, [f"Sin actividad hace {int(cur.days_since_activity)} días"])

    reasons: list[str] = []
    if ind.dictated and cur.projection < course.attendance_threshold:
        reasons.append(f"Proyección {_pct(cur.projection)} < {_pct(course.attendance_threshold)}")
    if cur.consecutive_failed:
        a, b = cur.consecutive_failed
        reasons.append(f"2 clases seguidas sin cumplir (sesiones {a} y {b})")
    if cur.unrecovered and cur.unrecovered_balance >= max(config.MIN_UNRECOVERED, cur.recovered_recent):
        nums = [str(c.number) for c in cur.unrecovered]
        missed = f"la sesión {nums[0]}" if len(nums) == 1 else f"las sesiones {', '.join(nums[:-1])} y {nums[-1]}"
        if cur.late_shift:
            reasons.append(
                f"Llega {round(cur.recent_late)} min tarde y sale {round(cur.recent_early)} min antes "
                f"en las últimas 3 clases; no recuperó {missed}"
            )
        if cur.pattern_drop:
            reasons.append(
                f"Su asistencia en vivo bajó a {_pct(cur.recent_live)} en las últimas 3 clases "
                f"(antes {_pct(cur.previous_live)}) y no recuperó {missed}"
            )
    if cur.quiz_trend is not None and cur.quiz_trend < 0 and cur.quiz_recent_mean is not None \
            and cur.quiz_recent_mean < course.quiz_pass_score:
        reasons.append(f"Notas de quiz a la baja (promedio de las últimas {config.QUIZ_TREND_WINDOW}: "
                       f"{cur.quiz_recent_mean:.1f}/5)")
    if cur.micro and cur.micro.topic in {"tiempo", "trabajo"} and cur.micro.sentiment == "negativo":
        detail = f": «{cur.micro.summary}»" if cur.micro.summary else ""
        reasons.append(f"En la micro-pregunta mencionó {TOPIC_LABELS[cur.micro.topic]}{detail}")
    if reasons:
        if cur.study_hour_mode is not None and cur.previous_study_hour_mode is not None \
                and abs(cur.study_hour_mode - cur.previous_study_hour_mode) >= 3:
            reasons.append(
                f"Cambió su hora habitual de estudio ({cur.previous_study_hour_mode}:00 → {cur.study_hour_mode}:00)"
            )
        return RiskResult(EN_RIESGO, reasons)

    if cur.mode_shift and cur.projection >= course.attendance_threshold:
        return RiskResult(EN_ADAPTACION, [
            f"Pasó a recuperar las clases con las grabaciones y va al día (proyección {_pct(cur.projection)})"
        ])
    return RiskResult(ACTIVO, [f"Va al día (proyección {_pct(cur.projection)})"])
