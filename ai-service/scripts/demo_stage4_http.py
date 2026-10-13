"""
Demostración HTTP REAL de la Etapa 4 contra un servidor en ejecución (sin Kimi, sin llamadas facturables).

    # 1) levantar el servidor (otra terminal), por ejemplo:
    #    $env:MICRO_QUESTION_REFERENCE_DATE='2026-09-01'; $env:TELEMETRY_API_TOKEN='demo-token-etapa4'
    #    $env:DATABASE_URL='sqlite:///./data/stage4-demo.db'
    #    python -m uvicorn app.main:app --port 8000
    # 2) ejecutar:
    #    python scripts/demo_stage4_http.py --base-url http://127.0.0.1:8000 --db data/stage4-demo.db

Usa un estudiante FICTICIO. Para poder responder el quiz, la demo lee la clave de respuestas directamente del
archivo SQLite (solo en esta demo local: la API nunca entrega la respuesta correcta antes del envío).
Escribe la evidencia en docs/evidence/etapa4_demo_http.md.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

STUDENT = "demo-student-etapa4"
COURSE = "sql-demo"
LESSON = "clase-02-uniones-y-agrupacion"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "etapa4_demo_http.md"

log = []


def record(title, response, note=""):
    try:
        body = response.json()
    except ValueError:
        body = response.text
    log.append((title, response.request.method, str(response.request.url), response.status_code, body, note))
    print(f"[{response.status_code}] {response.request.method} {response.request.url.path}  -  {title}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", default="data/stage4-demo.db", help="SQLite del servidor de demo (solo para leer la clave del quiz)")
    parser.add_argument("--token", default="demo-token-etapa4")
    args = parser.parse_args()
    http = httpx.Client(base_url=args.base_url, timeout=15)

    health = record("Estado del backend", http.get("/health"))
    assert health["mode"] == "DEMO" and health["llm"]["active_provider"] == "demo", "la demo exige modo DEMO (sin llamadas facturables)"

    chapters = record("Capítulos", http.get(f"/api/courses/{COURSE}/lessons/{LESSON}/chapters"))
    summary = record("Resumen", http.get(f"/api/courses/{COURSE}/lessons/{LESSON}/summary", params={"student_id": STUDENT}))
    assert [c["chapter_id"] for c in chapters["chapters"]] == [s["chapter_id"] for s in summary["sections"]]

    bad = record("Ausencia inválida (negativa)", http.post("/api/lessons/missed-content", json={
        "student_id": STUDENT, "course_id": COURSE, "lesson_id": LESSON,
        "absence_intervals": [{"start_seconds": -10, "end_seconds": 60}]}), "se rechaza con 422")
    assert bad["error"]["code"] == "invalid_absence_intervals"
    missed = record("Lo que te perdiste (intervalos solapados y duplicados, SIMULADOS)", http.post("/api/lessons/missed-content", json={
        "student_id": STUDENT, "course_id": COURSE, "lesson_id": LESSON,
        "absence_intervals": [{"start_seconds": 180, "end_seconds": 300}, {"start_seconds": 250, "end_seconds": 420},
                              {"start_seconds": 180, "end_seconds": 300}]}), "se normalizan a 180-420 sin repetir contenido")

    chat_headers = {"Idempotency-Key": "demo-chat-0001"}
    chat_body = {"student_id": STUDENT, "course_id": COURSE, "lesson_id": LESSON, "message": "Explícame LEFT JOIN"}
    chat1 = record("Chat (primer envío)", http.post("/api/chat", json=chat_body, headers=chat_headers))
    chat2 = record("Chat (reintento con la misma Idempotency-Key)", http.post("/api/chat", json=chat_body, headers=chat_headers),
                   "devuelve la respuesta original sin duplicar mensajes ni eventos")
    assert chat1 == chat2

    started = record("Inicio del quiz", http.post("/api/quiz/start", json={
        "student_id": STUDENT, "course_id": COURSE, "lesson_id": LESSON, "num_questions": 4}))
    assert "correct" not in json.dumps(started).lower(), "la respuesta correcta no debe salir en /start"
    key = {r[0]: r[1] for r in sqlite3.connect(f"file:{args.db}?mode=ro", uri=True).execute(
        "SELECT question_id, correct_option_id FROM quiz_answers WHERE attempt_id = ?", (started["attempt_id"],))}
    answers = []
    for i, q in enumerate(started["questions"]):
        right = key[q["question_id"]]
        wrong = next(o["option_id"] for o in q["options"] if o["option_id"] != right)
        answers.append({"question_id": q["question_id"], "option_id": right if i != 3 else wrong})   # 3 aciertos, 1 error
    result = record("Envío del quiz", http.post("/api/quiz/submit", json={
        "attempt_id": started["attempt_id"], "student_id": STUDENT, "answers": answers}), "3 de 4 correctas")
    resubmit = record("Segundo envío del mismo intento", http.post("/api/quiz/submit", json={
        "attempt_id": started["attempt_id"], "student_id": STUDENT, "answers": [
            {"question_id": a["question_id"], "option_id": key[a["question_id"]]} for a in answers]}), "debe rechazarse")
    assert resubmit["error"]["code"] == "quiz_already_submitted" and result["score_percent"] == 75.0

    micro = record("Micro-pregunta (¿corresponde?)", http.get("/api/micro-question", params={"student_id": STUDENT, "course_id": COURSE}))
    if micro["due"]:
        record("Respuesta a la micro-pregunta", http.post("/api/micro-question/answer", json={
            "student_id": STUDENT, "course_id": COURSE, "question_id": micro["question"]["question_id"],
            "period_index": micro["period_index"], "answer_text": "Me cuesta un poco por el trabajo, llego justo de tiempo"}))
        record("Micro-pregunta (segunda consulta del mismo periodo)", http.get(
            "/api/micro-question", params={"student_id": STUDENT, "course_id": COURSE}), "ya no se vuelve a preguntar")

    tsum = record("Telemetría: resumen agregado", http.get("/api/telemetry/summary"))
    record("Telemetría: eventos SIN token", http.get("/api/telemetry/events"), "debe rechazarse")
    events = record("Telemetría: eventos con token", http.get("/api/telemetry/events", headers={"X-Telemetry-Token": args.token}, params={"limit": 50}))
    assert STUDENT not in json.dumps(tsum) and STUDENT not in json.dumps(events), "no deben aparecer identificadores originales"

    types = sorted({e["event_type"] for e in events["events"]})
    print("\nEventos generados:", types)
    write_evidence(types)
    return 0


def write_evidence(types):
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Evidencia: demostración HTTP de la Etapa 4",
        "",
        f"Generada el {datetime.now(timezone.utc).isoformat(timespec='seconds')} con `scripts/demo_stage4_http.py` contra un servidor uvicorn local.",
        f"Estudiante FICTICIO: `{STUDENT}`. Modo DEMO, sin Kimi y sin llamadas facturables.",
        f"Eventos de telemetría observados: {', '.join(types)}.",
        "",
    ]
    for i, (title, method, url, status, body, note) in enumerate(log, 1):
        lines += [f"## {i}. {title}", "", f"`{method} {url}` → **{status}**" + (f" — {note}" if note else ""), "", "```json",
                  json.dumps(body, ensure_ascii=False, indent=2), "```", ""]
    EVIDENCE.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print("Evidencia escrita en", EVIDENCE.relative_to(ROOT))


if __name__ == "__main__":
    sys.exit(main())
