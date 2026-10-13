"""Resumen, capítulos y 'lo que te perdiste': lógica pura y contratos HTTP (sin API externa)."""
import re

import pytest

from app.knowledge.lesson_analysis import (
    analyze_segments, build_chapters, normalize_intervals, segment_overlaps, topic_defs, uppercase_terms,
    validate_intervals,
)
from app.knowledge.transcripts import parse_json_transcript
from app.models.transcript import format_timestamp

from conftest import LESSON_1, LESSON_2, STUDENT_A, rows

BASE = "/api/courses/sql-demo/lessons"


def missed(c, intervals, lesson=LESSON_2, student=STUDENT_A, **headers):
    return c.post("/api/lessons/missed-content", json={
        "student_id": student, "course_id": "sql-demo", "lesson_id": lesson, "absence_intervals": intervals}, headers=headers)


def iv(a, b):
    return {"start_seconds": a, "end_seconds": b}


def squash(s):
    return " ".join(s.split())


# ================================================================= resumen
def test_summary_is_built_from_the_transcript_and_is_verifiable(acad, retriever):
    resp = acad.get(f"{BASE}/{LESSON_2}/summary")
    assert resp.status_code == 200
    body = resp.json()
    lesson = retriever.get_lesson("sql-demo", LESSON_2)
    assert (body["course_id"], body["lesson_id"], body["lesson_title"]) == ("sql-demo", LESSON_2, lesson.title)
    assert (body["mode"], body["engine"], body["provider"]) == ("DEMO", "extractive", "demo")
    assert body["duration_seconds"] == 805.0 and body["duration_label"] == "13:25"
    assert [s["title"].split(" — ")[0] for s in body["sections"]] == ["Introducción", "INNER JOIN", "LEFT JOIN", "GROUP BY"]
    # cada punto clave es una frase TEXTUAL de los segmentos de su sección
    for sec in body["sections"]:
        text = squash(" ".join(lesson.segments[i].text for i in sec["segment_ids"]))
        assert squash(sec["key_point"]).rstrip("…") in text
        assert sec["references"] and sec["timestamp"].count("–") == 1
    used = {s["segment_id"] for s in body["segments_used"]}
    assert used == set(range(len(lesson.segments)))
    for ref, seg in zip(body["segments_used"], lesson.segments):
        assert (ref["start_seconds"], ref["end_seconds"], ref["timestamp"]) == (seg.start_seconds, seg.end_seconds, seg.timestamp_range)
    assert {r["lesson_id"] for r in body["references"]} == {LESSON_2}
    assert "FICTICIAS" in body["content_notice"] and "no es contenido oficial" in body["content_notice"]


def test_summary_key_concepts_have_real_minutes(acad, retriever):
    body = acad.get(f"{BASE}/{LESSON_2}/summary").json()
    concepts = {c["concept"]: c for c in body["key_concepts"]}
    assert concepts["INNER JOIN"]["kind"] == "tema" and concepts["INNER JOIN"]["first_minute"] == "02:20"
    assert concepts["LEFT JOIN"]["first_seconds"] == 330.0
    assert concepts["HAVING"]["kind"] == "termino" and concepts["HAVING"]["first_minute"] == "11:30"
    for c in concepts.values():
        seg = retriever.get_lesson("sql-demo", LESSON_2).segments[c["segment_ids"][0]]
        assert c["first_seconds"] == seg.start_seconds and c["mentions"] == len(c["segment_ids"])


def test_summary_makes_no_time_saving_claims(acad):
    text = acad.get(f"{BASE}/{LESSON_1}/summary").text.lower()
    assert "no se ha medido" in text
    assert not re.search(r"\d+\s*minutos?\s+de\s+contenido", text) and "equivale" not in text


def test_summary_llm_route_is_prepared_but_makes_no_external_call(acad, no_network):
    body = acad.get(f"{BASE}/{LESSON_1}/summary?engine=llm").json()
    assert (body["mode"], body["engine"], body["provider"]) == ("DEMO", "extractive", "demo")
    assert "acción administrativa explícita" in body["provider_notice"] and "sin ninguna llamada" in body["provider_notice"]
    assert acad.get(f"{BASE}/{LESSON_1}/summary").json()["provider_notice"] is None


def test_summary_errors_are_structured(acad):
    assert acad.get(f"{BASE}/{LESSON_1}/summary?engine=magia").json()["error"]["code"] == "invalid_engine"
    assert acad.get(f"{BASE}/clase-99/summary").json()["error"]["code"] == "lesson_not_found"
    assert acad.get("/api/courses/nope/lessons/x/summary").json()["error"]["code"] == "course_not_found"
    assert acad.get(f"{BASE}/{LESSON_1}/summary?student_id=../x").status_code == 422


# ================================================================ capítulos
@pytest.mark.parametrize("lesson, expected", [
    (LESSON_1, [(0, 150), (150, 265), (265, 382), (382, 485)]),
    (LESSON_2, [(0, 140), (140, 330), (330, 505), (505, 805)]),
])
def test_chapters_have_exact_timestamps_from_the_transcript(acad, retriever, lesson, expected):
    body = acad.get(f"{BASE}/{lesson}/chapters").json()
    got = [(c["start_seconds"], c["end_seconds"]) for c in body["chapters"]]
    assert got == [(float(a), float(b)) for a, b in expected]
    assert [c["chapter_id"] for c in body["chapters"]] == [f"cap-{i:02d}" for i in range(1, len(expected) + 1)]


@pytest.mark.parametrize("lesson", [LESSON_1, LESSON_2])
def test_chapters_stay_inside_the_lesson_and_partition_its_segments(acad, retriever, lesson):
    body = acad.get(f"{BASE}/{lesson}/chapters").json()
    transcript = retriever.get_lesson("sql-demo", lesson)
    seg_times = {(s.start_seconds, s.end_seconds) for s in transcript.segments}
    ids = []
    prev_end = 0.0
    for ch in body["chapters"]:
        assert 0 <= ch["start_seconds"] < ch["end_seconds"] <= transcript.duration_seconds == body["duration_seconds"]
        assert ch["start_seconds"] >= prev_end                                   # sin solapamientos
        assert ch["start_seconds"] in {a for a, _ in seg_times} and ch["end_seconds"] in {b for _, b in seg_times}  # nada inventado
        prev_end = ch["end_seconds"]
        ids += ch["segment_ids"]
        assert ch["title"] and ch["description"] and ch["references"]
        text = squash(" ".join(transcript.segments[i].text for i in ch["segment_ids"]))
        assert squash(ch["description"]).rstrip("…") in text                      # descripción textual
        assert {r["timestamp"] for r in ch["references"]} == {transcript.segments[i].timestamp_range for i in ch["segment_ids"]}
    assert ids == list(range(len(transcript.segments)))                          # todos los segmentos, una sola vez
    assert "no se modifica ningún video" in body["method_note"]


def test_chapter_titles_come_from_the_course_cards(acad):
    titles = [c["title"] for c in acad.get(f"{BASE}/{LESSON_1}/chapters").json()["chapters"]]
    assert titles == ["Introducción", "SELECT — Proyección de Columnas", "WHERE — Filtrado Condicional de Filas",
                      "ORDER BY — Ordenamiento de Resultados"]


def test_chapters_without_course_cards_fall_back_to_neutral_groups():
    lesson = parse_json_transcript({"segments": [
        {"start_seconds": i * 10, "end_seconds": i * 10 + 10, "text": f"Texto número {i} de la clase de prueba aquí."} for i in range(7)
    ]}, "c", "l")
    chapters = build_chapters(lesson, [], analyze_segments(lesson, []))
    assert [c.title for c in chapters] == ["Parte 1", "Parte 2", "Parte 3"]
    assert (chapters[0].start_seconds, chapters[-1].end_seconds) == (0, 70)


def test_uppercase_terms_join_adjacent_words():
    assert [t for t, _ in uppercase_terms("Usa ORDER BY y luego COUNT DISTINCT; también IS NULL.")] == [
        "ORDER BY", "COUNT DISTINCT", "IS NULL"]


# ======================================================== lo que te perdiste
def test_missed_content_returns_overlapping_segments_with_minutes(acad, retriever):
    resp = missed(acad, [iv(180, 420)])
    assert resp.status_code == 200
    body = resp.json()
    assert body["normalized_intervals"] == [{"start_seconds": 180.0, "end_seconds": 420.0, "start_label": "03:00",
                                             "end_label": "07:00", "duration_seconds": 240.0}]
    segs = [(s["timestamp"], s["overlap_seconds"], s["partially_missed"]) for s in body["missed_segments"]]
    assert segs == [("02:20–03:55", 55.0, True), ("03:55–05:30", 95.0, False), ("05:30–07:10", 90.0, True)]
    first = body["missed_segments"][0]
    assert (first["overlap_start_seconds"], first["overlap_end_seconds"]) == (180.0, 235.0)
    assert first["text"] == retriever.get_lesson("sql-demo", LESSON_2).segments[2].text
    assert body["total_missed_seconds"] == 240.0
    assert [c["concept"] for c in body["concepts_missed"]] == ["INNER JOIN", "ON", "LEFT JOIN", "NULL"]
    assert body["concepts_missed"][0]["first_minute"] == "03:00"             # desde el instante en que empieza la ausencia
    assert [r["timestamp"] for r in body["references"]] == ["02:20–03:55", "03:55–05:30", "05:30–07:10"]
    assert "03:00–07:00" in body["explanation"] and "INNER JOIN" in body["explanation"]
    assert [p["timestamp"] for p in body["key_points"]] == [s[0] for s in segs]
    assert "SIMULADOS" in body["attendance_notice"] and "Zoom" in body["attendance_notice"]
    assert "no modifica ni calcula asistencia" in body["attendance_notice"]


def test_overlapping_duplicate_and_touching_intervals_are_normalized_without_repeating_content(acad):
    messy = missed(acad, [iv(250, 420), iv(180, 300), iv(180, 300), iv(300, 330), iv(400, 420)]).json()
    clean = missed(acad, [iv(180, 420)]).json()
    assert messy["normalized_intervals"] == clean["normalized_intervals"]
    ids = [s["segment_id"] for s in messy["missed_segments"]]
    assert ids == [s["segment_id"] for s in clean["missed_segments"]] and len(ids) == len(set(ids))
    assert messy["total_missed_seconds"] == 240.0
    notes = " ".join(messy["normalization_notes"])
    assert "duplicado" in notes and "fusionaron" in notes
    assert clean["normalization_notes"] == []


def test_disjoint_intervals_keep_their_own_boundaries(acad):
    body = missed(acad, [iv(700, 760), iv(0, 50)]).json()
    assert [(i["start_seconds"], i["end_seconds"]) for i in body["normalized_intervals"]] == [(0.0, 50.0), (700.0, 760.0)]
    assert [(s["segment_id"], s["overlap_seconds"]) for s in body["missed_segments"]] == [(0, 50.0), (8, 60.0)]
    assert body["missed_segments"][0]["partially_missed"] is False and body["missed_segments"][1]["partially_missed"] is True


def test_an_interval_ending_exactly_at_a_segment_boundary_does_not_pull_in_the_next_one(acad):
    body = missed(acad, [iv(140, 235)]).json()          # exactamente el segmento 2 (INNER JOIN)
    assert [s["segment_id"] for s in body["missed_segments"]] == [2]
    assert body["missed_segments"][0]["partially_missed"] is False


def test_a_partial_overlap_with_two_intervals_in_one_segment_sums_each_piece(acad):
    body = missed(acad, [iv(0, 10), iv(20, 30)]).json()  # ambos dentro del segmento 0 (0-50)
    seg = body["missed_segments"][0]
    assert seg["overlap_seconds"] == 20.0 and seg["partially_missed"] is True
    assert (seg["overlap_start_seconds"], seg["overlap_end_seconds"]) == (0.0, 30.0) and len(body["missed_segments"]) == 1


@pytest.mark.parametrize("interval, reason", [
    (iv(-5, 40), "negativo"),
    (iv(10, -2), "negativo"),
    (iv(300, 200), "invertido"),
    (iv(100, 100), "vacio"),
    (iv(780, 900), "fuera_de_duracion"),
    (iv(805, 900), "fuera_de_duracion"),
    (iv(900, 950), "fuera_de_duracion"),
    (iv(0, 805.5), "fuera_de_duracion"),
])
def test_invalid_intervals_are_rejected_with_the_reason(acad, interval, reason):
    resp = missed(acad, [iv(0, 30), interval])
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "invalid_absence_intervals"
    assert err["details"] == [{"index": 1, "reason": reason, "message": err["details"][0]["message"]}]
    assert err["details"][0]["message"]


def test_the_whole_lesson_is_a_valid_interval(acad):
    body = missed(acad, [iv(0, 805)]).json()
    assert len(body["missed_segments"]) == 10 and body["total_missed_seconds"] == 805.0
    assert all(not s["partially_missed"] for s in body["missed_segments"])


def test_all_invalid_intervals_are_reported_together(acad):
    err = missed(acad, [iv(-1, 5), iv(9, 3), iv(0, 10)]).json()["error"]
    assert [d["index"] for d in err["details"]] == [0, 1]


@pytest.mark.parametrize("payload", [
    {"absence_intervals": []},
    {"absence_intervals": [iv(0, 1)] * 21},
    {"absence_intervals": [{"start_seconds": "pronto", "end_seconds": 10}]},
    {"absence_intervals": [{"start_seconds": 1}]},
    {"absence_intervals": [iv(0, 10)], "attendance_source": "zoom"},          # no se aceptan campos extra
    {"absence_intervals": [{"start_seconds": 0, "end_seconds": 10, "inferred_from_chat": True}]},
])
def test_malformed_missed_content_requests_get_422(acad, payload):
    body = {"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_2, **payload}
    assert acad.post("/api/lessons/missed-content", json=body).status_code == 422


def test_missed_content_unknown_lesson_or_course(acad):
    assert missed(acad, [iv(0, 10)], lesson="clase-99").json()["error"]["code"] == "lesson_not_found"
    r = acad.post("/api/lessons/missed-content", json={"student_id": STUDENT_A, "course_id": "nope", "lesson_id": "x",
                                                      "absence_intervals": [iv(0, 10)]})
    assert r.json()["error"]["code"] == "course_not_found"


def test_absences_are_never_inferred_from_chat_history(acad):
    acad.post("/api/chat", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_2,
                                 "message": "No pude asistir, explícame LEFT JOIN"})
    # el chat no crea ausencias: sin intervalos explícitos la petición es inválida
    r = acad.post("/api/lessons/missed-content", json={"student_id": STUDENT_A, "course_id": "sql-demo", "lesson_id": LESSON_2})
    assert r.status_code == 422


def test_missed_content_works_without_any_external_service(acad, no_network):
    assert missed(acad, [iv(180, 420)]).status_code == 200
    assert acad.get(f"{BASE}/{LESSON_1}/summary").status_code == 200
    assert acad.get(f"{BASE}/{LESSON_1}/chapters").status_code == 200


# ----------------------------------------------------------- lógica pura
def test_validate_and_normalize_helpers():
    reasons = [p.reason for p in validate_intervals([(0, 10), (5, 5), (20, 10), (-1, 3), (790, 806)], 805)]
    assert reasons == ["vacio", "invertido", "negativo", "fuera_de_duracion"]
    merged, notes = normalize_intervals([(10, 20), (15, 30), (30, 40), (50, 60), (50, 60)])
    assert merged == [(10, 40), (50, 60)] and len(notes) == 2


def test_segment_overlaps_helper(retriever):
    lesson = retriever.get_lesson("sql-demo", LESSON_1)
    result = segment_overlaps(lesson, [(30, 100)])
    assert [(o.segment.segment_id, o.overlap_seconds, o.partial) for o in result] == [(0, 12.0, True), (1, 53.0, False), (2, 5.0, True)]


def test_missed_content_telemetry_stores_counts_not_text(acad, db_path):
    missed(acad, [iv(180, 420)])
    ev = rows(db_path, "SELECT * FROM telemetry_events WHERE event_type = 'missed_content_requested'")
    assert len(ev) == 1 and ev[0]["student_id"] == STUDENT_A and ev[0]["lesson_id"] == LESSON_2 and ev[0]["mode"] == "DEMO"
    assert '"segment_count": 3' in ev[0]["payload"] and "total_missed_seconds" in ev[0]["payload"]
    assert "Escribimos la condición" not in ev[0]["payload"]
