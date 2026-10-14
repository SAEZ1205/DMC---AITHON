"""Resumen persistido: se genera una vez por versión de transcripción, se reutiliza y se invalida solo si el contenido cambia."""
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import AcademicConfig
from app.knowledge.real_lesson import TRANSCRIPT_FILE
from app.knowledge.retriever import KnowledgeRetriever
from app.main import create_app
from app.services import summary_service as ss
from app.services.summary_service import PIPELINE_VERSION, transcript_fingerprint

from conftest import ADMIN_TOKEN, LESSON_1, LESSON_2, N8N_COURSE, N8N_LESSON, build_synthetic_txt, rows
from llm_helpers import LIVE_CONFIG, FakeKimi

SQL = f"/api/courses/sql-demo/lessons/{LESSON_1}/summary"
N8N = f"/api/courses/{N8N_COURSE}/lessons/{N8N_LESSON}/summary"
ADMIN = {"X-Admin-Token": ADMIN_TOKEN}


def summary_rows(db_path):
    return rows(db_path, "SELECT course_id, lesson_id, engine, transcript_fingerprint, pipeline_version, created_at FROM lesson_summaries")


# =============================================================== una sola generación
def test_the_summary_is_generated_once_and_then_reused(acad, db_path):
    first = acad.get(SQL).json()
    second = acad.get(SQL).json()
    third = acad.get(SQL).json()
    assert first["cache"]["cached"] is False and second["cache"]["cached"] is True and third["cache"]["cached"] is True
    assert first["cache"]["generated_at"] == second["cache"]["generated_at"] == third["cache"]["generated_at"]
    assert first["overview"] == second["overview"] and first["sections"] == second["sections"]
    assert acad.app.state.summaries.generations == {"extractive": 1}
    assert len(summary_rows(db_path)) == 1


def test_cache_metadata_is_traceable(acad):
    cache = acad.get(SQL).json()["cache"]
    assert cache["engine"] == "extractive" and cache["pipeline_version"] == PIPELINE_VERSION
    assert re.fullmatch(r"[0-9a-f]{12}", cache["transcript_sha256_short"])


def test_the_summary_survives_a_server_restart_without_regenerating(make_app, db_path):
    with TestClient(make_app()) as c1:
        first = c1.get(SQL).json()
    app2 = make_app()
    with TestClient(app2) as c2:
        second = c2.get(SQL).json()
    assert second["cache"]["cached"] is True and second["cache"]["generated_at"] == first["cache"]["generated_at"]
    assert app2.state.summaries.generations == {}                              # nada se volvió a calcular
    assert len(summary_rows(db_path)) == 1


def test_each_lesson_has_its_own_summary(acad, db_path):
    a = acad.get(SQL).json()
    b = acad.get(f"/api/courses/sql-demo/lessons/{LESSON_2}/summary").json()
    assert a["lesson_id"] != b["lesson_id"] and len(summary_rows(db_path)) == 2


# ===================================================================== invalidación
def test_a_changed_transcript_invalidates_the_summary_and_replaces_the_old_one(db_path, fake_assets, clock):
    def start():
        cfg = AcademicConfig(admin_api_token=ADMIN_TOKEN)
        return create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets,
                          academic_config=cfg, clock=clock)

    app1 = start()
    with TestClient(app1) as c:
        first = c.get(N8N).json()
    app_same = start()                                                          # mismo archivo: se reutiliza
    with TestClient(app_same) as c:
        same = c.get(N8N).json()
    assert same["cache"]["transcript_sha256_short"] == first["cache"]["transcript_sha256_short"]
    assert same["cache"]["cached"] is True and app_same.state.summaries.generations == {}

    (fake_assets / TRANSCRIPT_FILE).write_bytes(build_synthetic_txt(extra={700: "Se añadió una explicación nueva sobre credenciales."}))
    app2 = start()                                                               # transcripción distinta
    with TestClient(app2) as c:
        changed = c.get(N8N).json()
    assert changed["cache"]["transcript_sha256_short"] != first["cache"]["transcript_sha256_short"]
    assert app2.state.summaries.generations == {"extractive": 1}                # se regeneró UNA vez (al procesar la clase)
    assert changed["cache"]["generated_at"] != first["cache"]["generated_at"]
    stored = [r for r in summary_rows(db_path) if r["lesson_id"] == N8N_LESSON]
    assert len(stored) == 1 and stored[0]["transcript_fingerprint"].startswith(changed["cache"]["transcript_sha256_short"])


def test_the_fingerprint_depends_on_text_times_and_source_hash(fake_assets):
    from app.knowledge.real_lesson import load_real_lesson
    base = load_real_lesson(fake_assets).lesson
    fp = transcript_fingerprint(base)
    same = base.model_copy(deep=True)
    assert transcript_fingerprint(same) == fp
    edited_text = base.model_copy(deep=True); edited_text.segments[3].text += " cambio"
    edited_time = base.model_copy(deep=True); edited_time.segments[3].start_seconds += 1
    other_source = base.model_copy(deep=True); other_source.content_hash = "0" * 64
    other_lesson = base.model_copy(deep=True); other_lesson.lesson_id = "otra"
    assert len({fp, *map(transcript_fingerprint, (edited_text, edited_time, other_source, other_lesson))}) == 5


def test_a_new_pipeline_version_invalidates_the_stored_summary(acad, db_path, monkeypatch):
    acad.get(SQL)
    monkeypatch.setattr(ss, "PIPELINE_VERSION", "summary/9.9")
    again = acad.get(SQL).json()
    assert again["cache"]["cached"] is False and again["cache"]["pipeline_version"] == "summary/9.9"
    assert [r["pipeline_version"] for r in summary_rows(db_path)] == ["summary/9.9"]


# ========================================================================= concurrencia
def test_simultaneous_requests_compute_the_summary_only_once(acad):
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(acad.get(SQL).json()["cache"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert acad.app.state.summaries.generations == {"extractive": 1}
    assert sorted(c["cached"] for c in results).count(False) <= 1 and len({c["generated_at"] for c in results}) == 1


def test_the_lock_serializes_slow_generations(acad):
    svc = acad.app.state.summaries
    lesson = acad.app.state.lessons._lesson("sql-demo", LESSON_1)
    calls, barrier = [], threading.Barrier(6)

    def slow():
        calls.append(1)
        time.sleep(0.25)
        return {"payload": "x"}, {"generator": "test"}

    def worker():
        barrier.wait()
        svc.get_or_create(lesson, "extractive", slow)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1


# ============================================================ motores y semántica de engine
def test_engine_semantics_without_a_kimi_summary(acad):
    ext = acad.get(f"{SQL}?engine=extractive").json()
    best = acad.get(f"{SQL}?engine=best").json()
    llm = acad.get(f"{SQL}?engine=llm").json()
    assert (ext["engine"], ext["mode"], ext["generated_by"]) == ("extractive", "DEMO", "extractive-heuristic/1.1")
    assert best["engine"] == "extractive" and best["provider_notice"] is None
    assert llm["engine"] == "extractive" and "acción administrativa explícita" in llm["provider_notice"]
    assert acad.get(f"{SQL}?engine=otro").json()["error"]["code"] == "invalid_engine"


def test_reading_summaries_never_calls_the_llm_even_when_live_is_configured(db_path, retriever):
    fake = FakeKimi()
    app = create_app(db_path=db_path, retriever=retriever, llm_config=LIVE_CONFIG, llm_client=fake.client())
    with TestClient(app) as c:
        for engine in ("extractive", "best", "llm"):
            assert c.get(f"{SQL}?engine={engine}").status_code == 200
    assert fake.calls == 0


# ============================================================ acción administrativa
def test_admin_actions_are_disabled_without_a_token_and_reject_wrong_ones(db_path, retriever, make_app):
    with TestClient(make_app(academic_config=AcademicConfig())) as c:
        r = c.post(f"/api/admin/lessons/sql-demo/{LESSON_1}/summary", headers=ADMIN)
    assert r.status_code == 403 and r.json()["error"]["code"] == "admin_disabled"
    with TestClient(make_app(academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN))) as c:
        for headers in ({}, {"X-Admin-Token": "equivocado"}, {"X-Admin-Token": ADMIN_TOKEN + "x"}):
            r = c.post(f"/api/admin/lessons/sql-demo/{LESSON_1}/summary", headers=headers)
            assert r.status_code == 401 and r.json()["error"]["code"] == "admin_unauthorized"
            assert ADMIN_TOKEN not in r.text


def test_forced_regeneration_replaces_the_summary_only_on_explicit_request(make_app, db_path):
    app = make_app(academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN))
    url = f"/api/admin/lessons/sql-demo/{LESSON_1}/summary"
    with TestClient(app) as c:
        first = c.post(url, headers=ADMIN).json()
        again = c.post(url, headers=ADMIN).json()
        forced = c.post(url + "?force=true", headers=ADMIN).json()
        shown = c.get(SQL).json()
    assert first["cached"] is False and again["cached"] is True and forced["cached"] is False
    assert again["generated_at"] == first["generated_at"] and forced["generated_at"] > first["generated_at"]
    assert shown["cache"]["generated_at"] == forced["generated_at"]
    assert app.state.summaries.generations == {"extractive": 2} and len(summary_rows(db_path)) == 1


def test_the_kimi_summary_cannot_be_requested_without_live_authorization(make_app):
    app = make_app(academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN))
    with TestClient(app) as c:
        r = c.post(f"/api/admin/lessons/sql-demo/{LESSON_1}/summary?engine=kimi", headers=ADMIN)
        bad = c.post(f"/api/admin/lessons/sql-demo/{LESSON_1}/summary?engine=otro", headers=ADMIN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "llm_not_available" and "ninguna llamada" in r.json()["error"]["message"]
    assert bad.json()["error"]["code"] == "invalid_engine"


# ================================================================ clase real (sintética)
def test_the_real_class_summary_is_generated_when_processing_the_class_not_when_a_student_opens_it(n8n, n8n_app):
    assert n8n_app.state.summaries.generations == {"extractive": 1}            # generado al arrancar (procesamiento)
    first = n8n.get(N8N).json()
    assert first["cache"]["cached"] is True                                      # el estudiante recibe el guardado
    assert n8n_app.state.summaries.generations == {"extractive": 1}


def test_the_real_class_is_summarized_in_time_window_chapters_with_real_timestamps(n8n, n8n_app):
    body = n8n.get(N8N).json()
    lesson = n8n_app.state.real_lesson.lesson
    assert body["mode"] == "DEMO" and body["engine"] == "extractive" and "Resumen local por temas" in body["method_note"]
    assert "sin modelo de lenguaje" in body["method_note"] and "no es una cita del docente" in body["method_note"].lower()
    secs = body["sections"]
    assert 8 <= len(secs) <= 20 and secs[0]["start_seconds"] == lesson.segments[0].start_seconds
    by_start = {s.start_seconds: s for s in lesson.segments}
    prev_end = 0.0
    for sec in secs:
        assert sec["start_seconds"] in by_start and sec["start_seconds"] >= prev_end and sec["end_seconds"] <= lesson.duration_seconds
        prev_end = sec["end_seconds"]
        ref = sec["references"][0]
        segment = next(s for s in lesson.segments if s.start_seconds == ref["start_seconds"])
        line = next(ln for ln in segment.lines if ln.start_seconds == ref["seek_seconds"])
        assert " ".join(sec["key_point"].rstrip("…").split()) in " ".join(segment.text.split())   # frase TEXTUAL del segmento citado
        head = " ".join(sec["key_point"].split()[:2])
        assert head.lower() in " ".join(line.text.split()).lower()                           # y el salto cae en el renglón donde empieza
        assert sec["start_seconds"] <= ref["seek_seconds"] < sec["end_seconds"] and sec["title"]
    assert secs[-1]["end_seconds"] == lesson.duration_seconds
    chapters = n8n.get(f"/api/courses/{N8N_COURSE}/lessons/{N8N_LESSON}/chapters").json()["chapters"]
    assert [c["chapter_id"] for c in chapters] == [s["chapter_id"] for s in secs]


def test_the_real_class_summary_contains_no_personal_data(n8n):
    blob = n8n.get(N8N).text + n8n.get(f"/api/courses/{N8N_COURSE}/lessons/{N8N_LESSON}/chapters").text
    for leak in ("maria.lopezzi", "987 654", "Lopezzi", "Maria", "Pedro"):
        assert leak not in blob
