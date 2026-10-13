"""Genera una cohorte sintética con perfiles conocidos, en el mismo formato que exporta Zoom.

Uso: python scripts/generate_cohort.py --students 30 --weeks 26 --seed 42

Salida en data/cohort/:
  students.csv, ground_truth.csv, sessions.csv
  zoom/<n>/participants.csv, chat.txt, transcript.vtt   (n = número de sesión)
  platform_events.json  (video visto, quizzes, heartbeats, preguntas al chat, micro-preguntas)
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from faker import Faker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402

PROFILE_SHARES = [("estable", 12), ("cambio_horario", 6), ("adaptado", 5), ("mala_conexion", 4), ("desinteres", 3)]
TEACHER = "Lincoln Villanueva"
CLASS_START, CLASS_HOURS = (19, 30), 3
DEFAULT_START = date(2026, 7, 21)  # martes
ZOOM_DT = "%m/%d/%Y %I:%M:%S %p"

CHAT_TEXTS = [
    "Buenas noches", "Profe, ¿puede repetir la última parte?", "Listo, me funcionó", "¿Se comparte la grabación?",
    "Gracias", "Tengo un error en el nodo HTTP", "¿El webhook va en producción o test?", "Excelente explicación",
    "¿Dónde descargo el flujo?", "Ya lo tengo", "Se cortó un momento el audio", "¿Esto entra en el proyecto?",
]
VOICE_TEXTS = [
    "Profe, una consulta sobre el ejercicio", "¿Ese nodo también sirve para Google Sheets?",
    "A mí me sale un error 401", "Ya me funcionó, gracias", "¿Cómo se ve eso en producción?",
]
QUESTION_TEXTS = {
    "contenido": ["¿Qué diferencia hay entre la URL de test y la de producción?", "¿Cómo se usa una expresión?"],
    "logistica": ["¿Cuándo se entrega el proyecto?", "¿Dónde está el material de la clase?"],
}
MICRO_TEXTS = {
    ("tiempo", "negativo"): "Cambié de trabajo y ahora salgo muy tarde, no llego a las clases en vivo.",
    ("trabajo", "negativo"): "Me cambiaron el horario en el trabajo y se me cruza con la clase.",
    ("trabajo", "neutro"): "Ahora veo las grabaciones al día siguiente por el trabajo, me acomoda.",
    ("tiempo", "positivo"): "Con las grabaciones puedo ponerme al día los fines de semana.",
    ("contenido", "negativo"): "Siento que el curso no es lo que esperaba, me cuesta engancharme.",
    ("ritmo", "negativo"): "Va muy rápido y me estoy quedando atrás.",
    ("satisfaccion", "negativo"): "No le estoy encontrando utilidad para mi trabajo.",
    ("contenido", "positivo"): "Todo bien, los ejercicios prácticos ayudan mucho.",
    ("ritmo", "neutro"): "El ritmo está bien, a veces necesito repasar.",
    ("satisfaccion", "positivo"): "Me está gustando mucho el curso.",
}


@dataclass
class Student:
    code: str
    name: str
    email: str
    profile: str
    shift_day: int | None = None
    dropout_day: int | None = None
    dropout_session: int | None = None
    last_micro: datetime | None = None
    micro_idx: int = 0


@dataclass
class Events:
    watch: list = field(default_factory=list)
    quiz: list = field(default_factory=list)
    heartbeats: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    micro: list = field(default_factory=list)


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return ".".join(part for part in "".join(c if c.isalnum() or c == " " else " " for c in s).split())


def build_students(n: int, rng: np.random.Generator, fake: Faker) -> list[Student]:
    total = sum(c for _, c in PROFILE_SHARES)
    counts = [max(1, round(c * n / total)) for _, c in PROFILE_SHARES]
    counts[0] += n - sum(counts)
    profiles = [p for (p, _), c in zip(PROFILE_SHARES, counts) for _ in range(c)]
    rng.shuffle(profiles)
    students, emails = [], set()
    for i, profile in enumerate(profiles, start=1):
        while True:
            name = f"{fake.first_name()} {fake.last_name()}"
            email = f"{slug(name)}@correo.pe"
            if email not in emails:
                emails.add(email)
                break
        st = Student(code=f"A{i:03d}", name=name, email=email, profile=profile)
        if profile == "cambio_horario":
            st.shift_day, st.dropout_day = int(rng.integers(60, 71)), int(rng.integers(100, 141))
        elif profile == "adaptado":
            st.shift_day = int(rng.integers(60, 76))
        elif profile == "desinteres":
            st.shift_day, st.dropout_day = int(rng.integers(70, 86)), int(rng.integers(125, 151))
        students.append(st)
    return students


def session_times(start: date, weeks: int) -> list[tuple[datetime, datetime]]:
    out = []
    for w in range(weeks):
        for offset in (0, 2):  # martes y jueves
            day = start + timedelta(days=7 * w + offset)
            s = datetime(day.year, day.month, day.day, *CLASS_START)
            out.append((s, s + timedelta(hours=CLASS_HOURS)))
    return out


def live_rows(rng, start, end, late, early, cuts=0, real_exit=False) -> list[tuple[datetime, datetime]]:
    join = start + timedelta(minutes=late)
    leave = end - timedelta(minutes=early)
    if leave <= join + timedelta(minutes=5):
        return []
    total = (leave - join).total_seconds()
    breaks: list[tuple[float, float]] = []
    for _ in range(cuts):
        breaks.append((rng.uniform(0.05, 0.95) * total, rng.uniform(15, 100)))
    if real_exit:
        breaks.append((rng.uniform(0.2, 0.8) * total, rng.uniform(180, 360)))
    breaks.sort()
    rows, cursor = [], join
    for at, gap in breaks:
        cut = join + timedelta(seconds=at)
        if cut <= cursor + timedelta(seconds=30):
            continue
        rows.append((cursor, cut))
        cursor = cut + timedelta(seconds=gap)
    if cursor < leave:
        rows.append((cursor, leave))
    return rows


def score_choice(rng, probs=(0.03, 0.17, 0.40, 0.40)) -> int:
    return int(rng.choice([2, 3, 4, 5], p=probs))


def add_quiz(ev: Events, st: Student, n: int, score: int, at: datetime, rng, micro_topic=None):
    ev.quiz.append({"email": st.email, "session": n, "score": score, "at": at.isoformat()})
    ev.heartbeats.append({"email": st.email, "page": "recording", "active_seconds": int(rng.integers(240, 600)),
                          "at": at.isoformat()})
    if micro_topic and (st.last_micro is None or at - st.last_micro >= timedelta(days=config.MICRO_EVERY_DAYS)):
        st.last_micro = at
        question = config.MICRO_QUESTIONS[st.micro_idx % len(config.MICRO_QUESTIONS)]
        st.micro_idx += 1
        answered = rng.random() < 0.65
        topic, sentiment = micro_topic
        ev.micro.append({
            "email": st.email, "question": question, "at": (at + timedelta(minutes=2)).isoformat(),
            "answer": MICRO_TEXTS[(topic, sentiment)] if answered else None,
            "topic": topic if answered else None, "sentiment": sentiment if answered else None,
            "summary": MICRO_TEXTS[(topic, sentiment)][:90] if answered else None,
        })


def add_recovery(ev: Events, st: Student, n: int, start: datetime, duration_s: float, rng,
                 delay_days: float, micro_topic=None, partial=False):
    at = start + timedelta(days=delay_days)
    at = at.replace(hour=int(rng.choice([7, 12, 13, 20, 21, 22])), minute=int(rng.integers(0, 60)))
    if at < start + timedelta(hours=CLASS_HOURS):
        at = start + timedelta(hours=CLASS_HOURS, minutes=30)
    if partial:
        a = rng.uniform(0, 0.6) * duration_s
        ev.watch.append({"email": st.email, "session": n, "start_s": round(a, 1),
                         "end_s": round(a + rng.uniform(0.1, 0.3) * duration_s, 1), "at": at.isoformat()})
        return
    chunks = int(rng.integers(2, 5))
    edges = np.linspace(0, duration_s, chunks + 1)
    t = at
    for a, b in zip(edges[:-1], edges[1:]):
        ev.watch.append({"email": st.email, "session": n, "start_s": round(float(a), 1),
                         "end_s": round(float(b), 1), "at": t.isoformat()})
        t += timedelta(minutes=int(rng.integers(8, 25)))
    ev.heartbeats.append({"email": st.email, "page": "recording", "active_seconds": int(rng.integers(1200, 2700)),
                          "at": t.isoformat()})
    for _ in range(int(rng.integers(0, 3))):
        kind = "contenido" if rng.random() < 0.75 else "logistica"
        ev.questions.append({"email": st.email, "session": n, "kind": kind,
                             "text": str(rng.choice(QUESTION_TEXTS[kind])), "at": (t - timedelta(minutes=5)).isoformat()})
    if rng.random() < 0.15:
        add_quiz(ev, st, n, 2, t, rng)
        t += timedelta(minutes=6)
    add_quiz(ev, st, n, score_choice(rng, (0.0, 0.25, 0.4, 0.35)), t, rng, micro_topic)


def behaviour(st: Student, day: int, rng) -> dict:
    """Parámetros de la sesión según el perfil y el momento del curso."""
    b = {"p_live": 0.94, "late": max(0.0, rng.normal(1, 3)), "early": max(0.0, rng.normal(0, 3)),
         "cuts": int(rng.integers(0, 2)), "real_exit": False, "p_recover": 0.96, "recover_delay": rng.uniform(0.5, 4),
         "p_late_recover": 0.0,
         "p_quiz": 0.6, "quiz_probs": (0.03, 0.17, 0.40, 0.40), "chat": 2.0, "voice": 0.8,
         "micro": [("contenido", "positivo"), ("ritmo", "neutro"), ("satisfaccion", "positivo")]}
    p = st.profile
    if p == "estable" and rng.random() < 0.01:
        b["micro"] = [("tiempo", "negativo")]
    if p == "mala_conexion":
        b.update(p_live=0.92, cuts=int(rng.integers(4, 10)), real_exit=rng.random() < 0.15)
    elif p == "cambio_horario" and day >= st.shift_day:
        b.update(p_live=0.75, late=max(25.0, rng.normal(45, 10)), early=max(20.0, rng.normal(40, 12)),
                 p_recover=0.08, p_late_recover=0.25, p_quiz=0.2, quiz_probs=(0.3, 0.4, 0.3, 0.0), chat=0.5, voice=0.1,
                 micro=[("tiempo", "negativo"), ("trabajo", "negativo")])
    elif p == "adaptado" and day >= st.shift_day:
        b.update(p_live=0.12, p_recover=0.98, recover_delay=rng.uniform(0.4, 3.0), chat=0.5,
                 micro=[("trabajo", "neutro"), ("tiempo", "positivo")])
    elif p == "desinteres" and day >= st.shift_day:
        f = min(1.0, (day - st.shift_day) / max(1, st.dropout_day - st.shift_day))
        mean = 4.5 - 3.2 * f
        probs = np.exp(-((np.array([2, 3, 4, 5]) - mean) ** 2) / 0.8)
        b.update(p_live=0.92 - 0.55 * f, early=max(0.0, rng.normal(80 * f, 15)), late=max(0.0, rng.normal(12 * f, 5)),
                 p_recover=0.8 * (1 - f), p_quiz=0.6 - 0.3 * f, quiz_probs=tuple(probs / probs.sum()),
                 chat=2 * (1 - f), voice=0.8 * (1 - f),
                 micro=[("contenido", "negativo"), ("ritmo", "negativo"), ("satisfaccion", "negativo")]
                 if f > 0.25 else b["micro"])
    return b


def vtt_time(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--students", type=int, default=30)
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    ap.add_argument("--out", type=Path, default=config.COHORT_DIR)
    args = ap.parse_args()
    generate(args.students, args.weeks, args.seed, args.start, args.out)


def generate(n_students: int, weeks: int, seed: int, start: date, out: Path) -> None:
    rng = np.random.default_rng(seed)
    late_rng = np.random.default_rng(seed + 1)  # aparte, para no alterar el resto de la cohorte
    fake = Faker("es_ES")
    Faker.seed(seed)
    if out.exists():
        shutil.rmtree(out)
    (out / "zoom").mkdir(parents=True)

    students = build_students(n_students, rng, fake)
    sessions = session_times(start, weeks)
    for st in students:
        if st.dropout_day is not None:
            st.dropout_session = next((i + 1 for i, (s, _) in enumerate(sessions)
                                       if (s.date() - start).days >= st.dropout_day), None)
    ev = Events()

    for idx, (s_start, s_end) in enumerate(sessions):
        n = idx + 1
        day = (s_start.date() - start).days
        duration_s = (s_end - s_start).total_seconds()
        folder = out / "zoom" / str(n)
        folder.mkdir()
        part_rows = [(TEACHER, "lincoln.villanueva@dmc.pe", s_start - timedelta(minutes=10), s_end + timedelta(minutes=5))]
        chat_lines: list[tuple[datetime, str, str]] = []
        voice: list[tuple[float, str, str]] = []

        for st in students:
            if st.dropout_session and n >= st.dropout_session:
                continue
            b = behaviour(st, day, rng)
            attended = rng.random() < b["p_live"]
            rows = live_rows(rng, s_start, s_end, b["late"], b["early"], b["cuts"], b["real_exit"]) if attended else []
            for join, leave in rows:
                email = st.email if rng.random() > 0.05 else ""
                part_rows.append((st.name, email, join, leave))
            connected = sum((b2 - a2).total_seconds() for a2, b2 in rows)
            live_ok = connected / duration_s >= config.COURSE_DEFAULTS["live_min_ratio"]
            if rows:
                for _ in range(int(rng.poisson(b["chat"]))):
                    a, z = rows[0][0], rows[-1][1]
                    at = a + (z - a) * float(rng.uniform(0, 1))
                    chat_lines.append((at.replace(microsecond=0), st.name, str(rng.choice(CHAT_TEXTS))))
                for _ in range(int(rng.poisson(b["voice"]))):
                    sec = (rows[0][0] - s_start).total_seconds() + rng.uniform(0, connected)
                    voice.append((max(0.0, sec), st.name, str(rng.choice(VOICE_TEXTS))))
            if live_ok:
                if rng.random() < b["p_quiz"]:
                    at = s_end + timedelta(minutes=int(rng.integers(5, 60 * 48)))
                    add_quiz(ev, st, n, score_choice(rng, b["quiz_probs"]), at, rng, tuple(rng.choice(b["micro"])))
            elif rng.random() < b["p_recover"]:
                add_recovery(ev, st, n, s_start, duration_s, rng, b["recover_delay"], tuple(rng.choice(b["micro"])))
            elif rng.random() < 0.4:
                add_recovery(ev, st, n, s_start, duration_s, rng, rng.uniform(1, 5), partial=True)
                if rng.random() < b["p_quiz"]:
                    at = s_start + timedelta(days=float(rng.uniform(1, 5)))
                    add_quiz(ev, st, n, score_choice(rng, b["quiz_probs"]), at, rng, tuple(rng.choice(b["micro"])))
            elif b["p_late_recover"] and late_rng.random() < b["p_late_recover"]:
                add_recovery(ev, st, n, s_start, duration_s, late_rng, late_rng.uniform(8, 10))

        with (folder / "participants.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Name (Original Name)", "User Email", "Join Time", "Leave Time", "Duration (Minutes)", "Guest"])
            for name, email, join, leave in part_rows:
                w.writerow([name, email, join.strftime(ZOOM_DT), leave.strftime(ZOOM_DT),
                            max(1, round((leave - join).total_seconds() / 60)), "No"])
        with (folder / "chat.txt").open("w", encoding="utf-8") as fh:
            for at, name, text in sorted(chat_lines):
                if n % 2:
                    fh.write(f"{at:%H:%M:%S}\t De {name} : {text}\n")
                else:
                    fh.write(f"{at:%H:%M:%S} De {name} a Todos: {text}\n")
        with (folder / "transcript.vtt").open("w", encoding="utf-8") as fh:
            fh.write("WEBVTT\n\n")
            cues = [(sec, TEACHER, "Continuamos con el siguiente tema.") for sec in np.linspace(60, duration_s - 300, 12)]
            cues += voice
            for i, (sec, name, text) in enumerate(sorted(cues), start=1):
                fh.write(f"{i}\n{vtt_time(sec)} --> {vtt_time(sec + 6)}\n{name}: {text}\n\n")

    with (out / "students.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "name", "email"])
        for st in students:
            w.writerow([st.code, st.name, st.email])
    with (out / "ground_truth.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "name", "email", "profile", "shift_date", "dropout_session", "dropout_date"])
        for st in students:
            shift = (start + timedelta(days=st.shift_day)).isoformat() if st.shift_day is not None else ""
            drop_date = sessions[st.dropout_session - 1][0].date().isoformat() if st.dropout_session else ""
            w.writerow([st.code, st.name, st.email, st.profile, shift, st.dropout_session or "", drop_date])
    with (out / "sessions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["number", "starts_at", "ends_at"])
        for i, (a, z) in enumerate(sessions, start=1):
            w.writerow([i, a.isoformat(), z.isoformat()])
    (out / "platform_events.json").write_text(json.dumps(ev.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Cohorte generada en {out}: {len(students)} alumnos, {len(sessions)} sesiones, "
          f"{len(ev.watch)} rangos vistos, {len(ev.quiz)} quizzes, {len(ev.micro)} micro-preguntas.")


if __name__ == "__main__":
    main()
