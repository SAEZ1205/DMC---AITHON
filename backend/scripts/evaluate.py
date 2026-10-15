"""Evalúa el diagnóstico contra los perfiles plantados de la cohorte sintética (sección 6.6).

Recorre el curso día a día (23:59) con el reloj simulado, clasifica a cada alumno con los mismos
motores que usa la API y compara con data/cohort/ground_truth.csv.

Uso: python scripts/evaluate.py [--verbose]
Requiere haber corrido antes scripts/seed_demo.py.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Course  # noqa: E402
from app.services.recompute import student_status  # noqa: E402
from app.services.risk import EN_ADAPTACION, EN_RIESGO  # noqa: E402
from app.services.timeline import load_course_data  # noqa: E402

DROPOUT_PROFILES = {"cambio_horario", "desinteres"}
STABLE_PROFILES = {"estable", "mala_conexion"}
MIN_LEAD_DAYS = 14


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true", help="detalle por alumno")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with (config.COHORT_DIR / "ground_truth.csv").open(encoding="utf-8") as fh:
        truth = {row["email"]: row for row in csv.DictReader(fh)}

    with Session(engine) as db:
        course = db.exec(select(Course)).first()
        data = load_course_data(db, course.id)

    by_user = {uid: truth[st.email] for uid, st in data.students.items() if st.email in truth}
    first_day = data.sessions[0].starts_at.date()
    last_day = data.sessions[-1].ends_at.date() + timedelta(days=course.recovery_days + 1)
    timeline: dict[int, list[tuple[date, str, list[str]]]] = defaultdict(list)
    day = first_day
    while day <= last_day:
        now = datetime.combine(day, time(23, 59))
        for uid, st in data.students.items():
            status = student_status(st, data, now)
            timeline[uid].append((day, status.risk.state, status.risk.reasons))
        day += timedelta(days=1)

    recall_hits, recall_total, lead_days = 0, 0, []
    false_alarms, stable_total = 0, 0
    adapt_ok, adapt_never_risk, adapt_safe, adapt_total = 0, 0, 0, 0
    lines = []
    for uid, gt in by_user.items():
        profile = gt["profile"]
        states = timeline[uid]
        risk_days = [d for d, s, _ in states if s == EN_RIESGO]
        detail = ""
        if profile in DROPOUT_PROFILES:
            recall_total += 1
            dropout = date.fromisoformat(gt["dropout_date"])
            before = [d for d in risk_days if d < dropout]
            lead = (dropout - before[0]).days if before else None
            if lead is not None and lead >= MIN_LEAD_DAYS:
                recall_hits += 1
            if lead is not None:
                lead_days.append(lead)
            first_reasons = next((r for d, s, r in states if s == EN_RIESGO and d < dropout), [])
            detail = f"abandona {dropout}, primer riesgo {before[0] if before else '-'}, anticipación {lead} días; " \
                     f"motivos: {'; '.join(first_reasons)}"
        elif profile in STABLE_PROFILES:
            stable_total += 1
            if risk_days:
                false_alarms += 1
                reasons = next(r for d, s, r in states if s == EN_RIESGO)
                detail = f"FALSA ALARMA {len(risk_days)} días desde {risk_days[0]}: {'; '.join(reasons)}"
        elif profile == "adaptado":
            adapt_total += 1
            shift = date.fromisoformat(gt["shift_date"])
            after = [s for d, s, _ in states if d >= shift + timedelta(days=14)]
            counts = Counter(after)
            adapting = counts[EN_ADAPTACION] >= max(1, counts[EN_RIESGO])
            adapt_ok += adapting and EN_ADAPTACION in counts
            adapt_never_risk += not risk_days
            adapt_safe += counts[EN_RIESGO] == 0
            detail = f"cambio {shift}; después: {dict(counts)}"
            if risk_days:
                reasons = next(r for d, s, r in states if s == EN_RIESGO)
                detail += f"; riesgo {len(risk_days)} días desde {risk_days[0]}: {'; '.join(reasons)}"
        lines.append(f"  {gt['code']} {profile:<15} {detail}")

    recall = recall_hits / recall_total if recall_total else 0
    fa = false_alarms / stable_total if stable_total else 0
    adapt = adapt_ok / adapt_total if adapt_total else 0
    print(f"Evaluación del diagnóstico ({len(by_user)} alumnos, {first_day} a {last_day}, paso diario)")
    print(f"- Recall (desertores en riesgo con ≥{MIN_LEAD_DAYS} días de anticipación): "
          f"{recall_hits}/{recall_total} = {recall:.0%}  (meta ≥ 80 %)  "
          f"anticipación media {sum(lead_days) / len(lead_days) if lead_days else 0:.0f} días")
    print(f"- Falsas alarmas (estable y mala_conexion marcados en riesgo): "
          f"{false_alarms}/{stable_total} = {fa:.0%}  (meta ≤ 20 %)")
    print(f"- Adaptados clasificados en_adaptacion tras el cambio: {adapt_ok}/{adapt_total} = {adapt:.0%}; "
          f"sin riesgo tras el cambio: {adapt_safe}/{adapt_total}; nunca en riesgo: {adapt_never_risk}/{adapt_total}")
    ok = recall >= 0.8 and fa <= 0.2 and adapt_safe == adapt_total
    print("Resultado:", "CUMPLE las metas" if ok else "NO cumple las metas")
    if args.verbose:
        print("\n".join(sorted(lines, key=lambda x: x.split()[1])))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
