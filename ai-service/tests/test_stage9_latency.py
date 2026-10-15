"""
Etapa 9 — latencia del tutor: instrumentación, contexto reducido, respuestas breves, caché de respuestas verificadas y streaming opcional.

Todo con transporte simulado (MockTransport) y con las conexiones externas bloqueadas por `conftest`. NADA de esto mide la velocidad real de NVIDIA:
las pruebas comprueban que el código mide, reduce, guarda y transmite correctamente; el tiempo de Kimi solo lo demuestra una solicitud real autorizada.
"""
import json
import logging
import random
import re
import sqlite3
import threading
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.streaming import sse, sse_events
from app.config import AcademicConfig, settings
from app.knowledge.retriever import KnowledgeRetriever
from app.llm import NvidiaKimiProvider
from app.llm.answer_stream import AnswerStreamExtractor, ProvisionalText, decode_all
from app.llm.base import ResponseKind, TutorContext
from app.llm.errors import StreamCancelled
from app.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.main import create_app
from app.models.chat import ChatRequest
from app.services import latency
from app.services import tutor_service as tutor_module

from conftest import ADMIN_TOKEN, LESSON_1, N8N_COURSE, N8N_LESSON, chat_n8n, post_chat, rows
from llm_helpers import FAKE_NVIDIA_KEY, NVIDIA_CONFIG, REASONING_SECRET, FakeKimi, good_answer, json_response, nv_completion

ALLOW = replace(NVIDIA_CONFIG, allow_class_content=True, timeout_seconds=10.0)
OK = lambda request, payload: json_response(nv_completion(good_answer("S1")))      # noqa: E731
DOWN = lambda request, payload: httpx.Response(503, json={"status": 503})          # noqa: E731


def n8n_live(db_path, fake_assets, fake, monkeypatch, cache=False, streaming=False, config=ALLOW):
    monkeypatch.setattr(settings, "ANSWER_CACHE_ENABLED", cache)
    monkeypatch.setattr(settings, "CHAT_STREAMING_ENABLED", streaming)
    return create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets, llm_config=config,
                      llm_client=fake.client(), academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN))


def tutor_payloads(fake):
    return [p for p in fake.payloads if "<mensaje_estudiante>" in p["messages"][-1]["content"]]


# ===================================================================== A. instrumentación
def test_a_trace_measures_stages_and_is_a_noop_without_a_scope():
    latency.stage("x").__enter__()                       # sin traza activa: no falla ni acumula
    with latency.trace_scope() as trace:
        with latency.stage("retrieval_ms"):
            time.sleep(0.02)
        with latency.stage("retrieval_ms"):
            time.sleep(0.01)
        latency.set_count("prompt_chars", 1234)
        public = trace.public("live")
    assert public["status"] == "live" and public["retrieval_ms"] >= 25 and public["total_ms"] >= public["retrieval_ms"]
    assert public["counts"] == {"prompt_chars": 1234} and latency.current() is None


def test_traces_do_not_leak_between_concurrent_requests():
    seen = {}

    def worker(name, ms):
        with latency.trace_scope() as trace:
            with latency.stage("provider_ms"):
                time.sleep(ms / 1000)
            seen[name] = trace.public("live")["provider_ms"]
    threads = [threading.Thread(target=worker, args=("a", 30)), threading.Thread(target=worker, args=("b", 90))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen["a"] < seen["b"] and seen["a"] < 80


def test_the_aggregate_summarizes_statuses_and_percentiles_with_numbers_only():
    stats = latency.LatencyStats(size=5)
    for i, status in enumerate(["live", "live", "demo", "cached", "live", "live"]):
        stats.add({"status": status, "total_ms": 100.0 * (i + 1), "provider_ms": 50.0 * (i + 1), "counts": {"prompt_chars": 4000 + i}})
    summary = stats.summary()
    assert summary["samples"] == 5 and set(summary["by_status"]) <= {"live", "demo", "cached"}
    assert summary["total"]["max_ms"] == 600.0 and summary["total_live"]["n"] >= 1 and summary["prompt_chars"]["n"] == 5
    assert latency.LatencyStats().summary() is None


def test_every_chat_reports_stage_times_and_a_status_without_any_text(client, caplog):
    secret_question = "Explícame ORDER BY con paciencia"
    with caplog.at_level(logging.INFO):
        body = post_chat(client, secret_question).json()
    timing = body["timing"]
    assert timing["status"] == "demo" and timing["total_ms"] > 0
    assert {"guardian_ms", "retrieval_ms", "persist_ms", "counts"} <= set(timing) and timing["counts"]["evidence_segments"] >= 1
    assert all(isinstance(v, (int, float, dict, str)) for v in timing.values()) and timing["status"] in {"demo", "live"}
    assert "ORDER BY" not in json.dumps(timing) and "paciencia" not in json.dumps(timing)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("chat_timing")]
    assert lines and "paciencia" not in lines[0] and "status=demo" in lines[0]
    health = client.get("/health").json()["llm"]
    assert health["latency"]["samples"] >= 1 and health["latency"]["total"]["n"] >= 1 and "paciencia" not in json.dumps(health)


def test_a_live_chat_separates_provider_time_prompt_time_verification_and_token_counts(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch)) as c:
        body = chat_n8n(c, "¿Qué es n8n?").json()
        health = c.get("/health").json()["llm"]
    t = body["timing"]
    assert t["status"] == "live" and {"prompt_ms", "egress_ms", "provider_ms", "verify_ms"} <= set(t)
    assert t["provider_ms"] > 0 and t["total_ms"] >= t["provider_ms"]
    assert t["counts"]["prompt_chars"] > 1000 and t["counts"]["prompt_tokens"] == 300 and t["counts"]["reasoning_tokens"] == 120
    assert health["latency"]["by_status"]["live"] == 1 and health["latency"]["provider"]["n"] == 1


def test_the_guardian_call_is_timed_apart_from_the_tutor_call(db_path, fake_assets, monkeypatch):
    def responder(request, payload):
        if "<pregunta>" in payload["messages"][-1]["content"]:
            return json_response(nv_completion({"label": "relevant", "reason": "ok"}))
        return json_response(nv_completion(good_answer("S1")))
    with TestClient(n8n_live(db_path, fake_assets, FakeKimi(responder), monkeypatch)) as c:
        body = chat_n8n(c, "¿Qué es la sostenibilidad ambiental de los procesos repetitivos?").json()
    t = body["timing"]
    assert "guardian_provider_ms" not in t or t["guardian_provider_ms"] > 0
    assert "provider_ms" not in t or t.get("provider_ms") != t.get("guardian_provider_ms")


# ===================================================================== B/C. contexto reducido y respuestas breves
def test_the_system_prompt_is_compact_and_asks_for_60_to_100_words():
    assert len(SYSTEM_PROMPT) < 2600 and PROMPT_VERSION == "rescuepath-socratic-tutor/1.2.0"
    assert "60 a 100 palabras" in SYSTEM_PROMPT and "No copies párrafos" in SYSTEM_PROMPT and "answerable" in SYSTEM_PROMPT


def test_an_independent_question_in_an_ongoing_session_sends_no_history(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch)) as c:
        first = chat_n8n(c, "¿Qué es n8n?").json()
        chat_n8n(c, "¿Para qué sirve Docker?", session_id=first["session_id"])         # pregunta nueva y autosuficiente
        chat_n8n(c, "No entendí, ¿puedes repetirlo?", session_id=first["session_id"])   # seguimiento: SÍ necesita el último turno
    p1, p2, p3 = tutor_payloads(fake)
    assert [m["role"] for m in p1["messages"]] == ["system", "user"]
    assert [m["role"] for m in p2["messages"]] == ["system", "user"]                     # sin historial: menos tokens y menos exposición
    assert [m["role"] for m in p3["messages"]] == ["system", "user", "assistant", "user"]   # solo el último turno (2 mensajes)


def test_history_messages_are_short_and_the_turn_counter_line_is_gone(db_path, fake_assets, monkeypatch):
    long_reply = {"answer": "Explicación larga " * 80 + "[S1]", "follow_up_question": None, "used_segments": ["S1"]}
    fake = FakeKimi(lambda r, p: json_response(nv_completion(long_reply)))
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch)) as c:
        first = chat_n8n(c, "¿Qué es n8n?").json()
        chat_n8n(c, "No entendí, ¿puedes repetirlo?", session_id=first["session_id"])
    second = tutor_payloads(fake)[-1]["messages"]
    assert len(second[2]["content"]) <= tutor_module.HISTORY_MESSAGE_MAX_CHARS == 300
    assert "Turno de la sesión" not in second[-1]["content"]


def test_evidence_is_one_or_two_segments_and_citations_still_come_from_real_segments(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch)) as c:
        body = chat_n8n(c, "¿Qué es n8n y para qué sirve?").json()
        lesson = c.app.state.real_lesson.lesson
    user = tutor_payloads(fake)[0]["messages"][-1]["content"]
    assert 1 <= user.count("<segmento ") <= tutor_module.DEFAULT_EVIDENCE_CAP == 2
    starts = {(s.start_seconds, s.end_seconds) for s in lesson.segments}
    assert body["references"] and all((r["start_seconds"], r["end_seconds"]) in starts for r in body["references"])
    assert all(r["start_seconds"] <= r["seek_seconds"] < r["end_seconds"] for r in body["references"])


def test_generation_parameters_stay_within_what_the_provider_requires(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch, config=replace(ALLOW, max_output_tokens=700))) as c:
        chat_n8n(c, "¿Qué es n8n?")
    payload = tutor_payloads(fake)[0]
    assert payload["max_completion_tokens"] == 700 >= 512 and payload["reasoning_effort"] == "low" and payload["stream"] is False


def test_one_question_makes_exactly_one_model_call_even_when_the_same_idempotency_key_is_resent(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    body = {"student_id": "demo-student-001", "course_id": N8N_COURSE, "lesson_id": N8N_LESSON, "message": "¿Qué es n8n?"}
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch)) as c:
        a = c.post("/api/chat", json=body, headers={"Idempotency-Key": "abc-123-key"})
        b = c.post("/api/chat", json=body, headers={"Idempotency-Key": "abc-123-key"})
    assert a.status_code == b.status_code == 200 and a.json()["session_id"] == b.json()["session_id"] and fake.calls == 1


# ===================================================================== E. caché de respuestas verificadas
def ask_new_student(c, text, n):
    return chat_n8n(c, text, student_id=f"demo-student-{n:03d}").json()


def test_a_repeated_question_is_served_from_the_cache_and_labeled_as_such(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch, cache=True)) as c:
        first = ask_new_student(c, "¿Qué es n8n?", 1)
        second = ask_new_student(c, "¿QUÉ es n8n?", 2)                       # otra alumna y otra forma de escribirlo
        health = c.get("/health").json()["llm"]
    assert fake.calls == 1                                                      # la segunda NO esperó a Kimi
    assert (first["mode"], first["answer_origin"], first["fallback_used"]) == ("LIVE", "live", False) and first["cached_at"] is None
    assert (second["mode"], second["answer_origin"], second["fallback_used"]) == ("CACHED", "cached", False) and second["cached_at"]
    assert "Respuesta guardada" in second["provider_notice"] and "no es una generación nueva" in second["provider_notice"]
    assert second["response"] == first["response"] and second["references"] == first["references"] and second["provider"] == "nvidia-kimi"
    assert second["timing"]["status"] == "cached" and second["timing"]["counts"]["cache_hit"] == 1 and "provider_ms" not in second["timing"]
    assert health["answer_cache"] == {"enabled": True, "entries": 1, "hits": 1}


def test_the_cache_never_stores_follow_ups_personal_questions_fallbacks_or_other_cached_answers(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch, cache=True)) as c:
        first = ask_new_student(c, "¿Qué es n8n?", 1)
        chat_n8n(c, "Dame otro ejemplo", session_id=first["session_id"])                                   # seguimiento
        ask_new_student(c, "Mi correo es ana.perez@correo.com. ¿Para qué sirve Docker?", 2)               # datos personales
        entries = c.get("/health").json()["llm"]["answer_cache"]["entries"]
    assert entries == 1                                                         # solo la primera pregunta autosuficiente y sin datos personales


def test_a_fallback_answer_is_never_cached_and_a_later_live_answer_is(db_path, fake_assets, monkeypatch):
    down = FakeKimi(DOWN)
    with TestClient(n8n_live(db_path, fake_assets, down, monkeypatch, cache=True)) as c:
        failed = ask_new_student(c, "¿Qué es n8n?", 1)
        assert c.get("/health").json()["llm"]["answer_cache"]["entries"] == 0
    assert failed["answer_origin"] == "demo_fallback" and failed["fallback_used"] is True and failed["mode"] == "DEMO"
    up = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, up, monkeypatch, cache=True)) as c:
        again = ask_new_student(c, "¿Qué es n8n?", 2)
        assert c.get("/health").json()["llm"]["answer_cache"]["entries"] == 1
    assert again["answer_origin"] == "live" and up.calls == 1


@pytest.mark.parametrize("what", ["transcript", "prompt", "model", "effort", "disabled"])
def test_anything_that_changes_the_answer_invalidates_the_cache(db_path, fake_assets, tmp_path, monkeypatch, what):
    from conftest import SYNTH_DURATION, build_synthetic_txt
    from mp4_builder import build_mp4
    first = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, first, monkeypatch, cache=True)) as c:
        ask_new_student(c, "¿Qué es n8n?", 1)
    assets, config, cache_on = fake_assets, ALLOW, True
    if what == "transcript":                                                    # la clase cambió (otro texto) => otra huella
        assets = tmp_path / "otra"
        assets.mkdir()
        (assets / "transcripcion-original.txt").write_bytes(build_synthetic_txt(extra={125: "Primero veamos qué es n8n, una herramienta de automatización distinta."}))
        (assets / "clase-demo.mp4").write_bytes(build_mp4(SYNTH_DURATION))
    elif what == "prompt":
        monkeypatch.setattr(tutor_module, "PROMPT_VERSION", "rescuepath-socratic-tutor/9.9.9")
    elif what == "model":
        config = replace(ALLOW, model="otro/modelo")
    elif what == "effort":
        config = replace(ALLOW, reasoning_effort="high")
    else:
        cache_on = False
    second = FakeKimi(OK)
    with TestClient(n8n_live(db_path, assets, second, monkeypatch, cache=cache_on, config=config)) as c:
        body = ask_new_student(c, "¿Qué es n8n?", 2)
    assert second.calls == 1 and body["answer_origin"] == "live"               # no se reutilizó: se generó de nuevo


def test_the_cache_stores_no_question_no_student_no_session_and_no_history(db_path, fake_assets, monkeypatch):
    question = "¿Qué es n8n? Explícame con un ejemplo de flujos automatizados"
    with TestClient(n8n_live(db_path, fake_assets, FakeKimi(OK), monkeypatch, cache=True)) as c:
        body = chat_n8n(c, question, student_id="demo-student-777").json()
    columns = {r["name"] for r in rows(db_path, "PRAGMA table_info(answer_cache)")}
    assert columns == {"cache_key", "created_at", "last_hit_at", "hits", "provider", "model", "answer_text", "follow_up", "cited_starts"}
    dump = json.dumps(rows(db_path, "SELECT * FROM answer_cache"), ensure_ascii=False)
    for private in ("con un ejemplo", "flujos automatizados", "Explícame", "demo-student-777", body["session_id"], "Zulemita", FAKE_NVIDIA_KEY):
        assert private not in dump
    assert re.fullmatch(r"[0-9a-f]{64}", rows(db_path, "SELECT cache_key FROM answer_cache")[0]["cache_key"])


def test_a_cache_hit_requires_that_the_current_evidence_still_contains_what_was_cited(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch, cache=True)) as c:
        ask_new_student(c, "¿Qué es n8n?", 1)
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE answer_cache SET cited_starts = '[999999.0]'")      # entrada corrupta/antigua: cita un segmento que ya no está
        conn.commit()
        conn.close()
        body = ask_new_student(c, "¿Qué es n8n?", 2)
    assert body["answer_origin"] == "live" and fake.calls == 2


def test_the_cache_is_off_for_demo_and_never_changes_what_demo_answers(client):
    first = post_chat(client, "Explícame ORDER BY").json()
    second = post_chat(client, "Explícame ORDER BY").json()
    assert first["answer_origin"] == second["answer_origin"] == "demo" and first["response"] == second["response"]
    assert client.get("/health").json()["llm"]["answer_cache"] is None


# ===================================================================== D. streaming — extractor
ANSWER_JSON = json.dumps({"answer": 'Explica «n8n» con "comillas", saltos\ny tildes: ñandú café 😀 [S1] y más [S2, S1]. Fin.',
                          "follow_up_question": "¿Cómo?", "used_segments": ["S1"], "answerable": True}, ensure_ascii=True)


@pytest.mark.parametrize("seed", range(12))
def test_the_extractor_recovers_the_exact_answer_for_any_chunking(seed):
    rnd = random.Random(seed)
    cuts = sorted(rnd.sample(range(1, len(ANSWER_JSON)), rnd.randint(1, 25)))
    chunks = [ANSWER_JSON[a:b] for a, b in zip([0] + cuts, cuts + [len(ANSWER_JSON)])]
    assert decode_all(chunks) == json.loads(ANSWER_JSON)["answer"]


def test_the_extractor_handles_fences_leading_text_and_stops_at_the_closing_quote():
    ex = AnswerStreamExtractor()
    out = ex.feed("```json\n{ \"answer\" :  \"Hola ") + ex.feed("mundo\", \"used_segments\": [\"S1\"]}") + ex.feed(' y "answer": "otro"')
    assert out == "Hola mundo" and ex.finished
    assert decode_all(['{"used_segments": ["S1"], "ans', 'wer": "tarde"}']) == "tarde"
    assert decode_all(['no es json', ' en absoluto']) == ""
    assert decode_all(['{"answer": "a\\u00', 'e9b"}']) == "aéb"                      # escape unicode partido entre trozos


def test_provisional_text_drops_citation_tags_and_never_shows_a_half_written_one():
    pt, shown = ProvisionalText(), []
    for piece in ["La clase explica", " que sí [", "S1", "] y también [S2", ", S1]. ", "Fin [", "X"]:
        shown.append(pt.push(piece))
    shown.append(pt.flush())
    visible = "".join(shown)
    assert "[S" not in visible and "S1]" not in visible and visible.startswith("La clase explica que sí  y también")
    assert "".join(shown[:2]).endswith("sí ")                                      # lo que está a medio escribir se retiene


def test_sse_frames_are_single_line_json_so_text_cannot_inject_events():
    frame = sse("delta", {"text": "línea1\nevent: final\ndata: {}\n\n<img src=x onerror=alert(1)>"})
    assert frame.count("\n") == 3 and frame.endswith("\n\n") and frame.startswith("event: delta\ndata: ")
    assert json.loads(frame.split("data: ", 1)[1])["text"].startswith("línea1\nevent: final")


# ===================================================================== D. streaming — proveedor
def sse_body(answer_obj, reasoning=("Pienso en la pregunta… ", REASONING_SECRET), piece=9, finish="stop", usage=True):
    lines = [f'data: {json.dumps({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})}']
    for r in reasoning:
        lines.append("data: " + json.dumps({"choices": [{"index": 0, "delta": {"reasoning_content": r}}]}))
    text = json.dumps(answer_obj, ensure_ascii=False)
    for i in range(0, len(text), piece):
        lines.append("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": text[i:i + piece]}}]}, ensure_ascii=False))
    last = {"model": "moonshotai/kimi-k3", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    if usage:
        last["usage"] = {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500, "completion_tokens_details": {"reasoning_tokens": 120}}
    lines.append("data: " + json.dumps(last))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def sse_responder(answer_obj, **kw):
    return lambda request, payload: httpx.Response(200, content=sse_body(answer_obj, **kw), headers={"content-type": "text/event-stream"})


def sql_context(retriever, **kw):
    ev = retriever.retrieve_segments("sql-demo", "ORDER BY", lesson_id=LESSON_1, top_k=2)
    base = dict(kind=ResponseKind.GROUNDED, student_message="Explícame ORDER BY", course_name="SQL", lesson_title="Clase 1", topic_label="ORDER BY", evidence=ev)
    base.update(kw)
    return TutorContext(**base)


def test_streaming_delivers_provisional_text_discards_reasoning_and_ends_with_the_same_verified_reply(retriever):
    answer = good_answer("S1")
    streamed = FakeKimi(sse_responder(answer))
    plain = FakeKimi(OK)
    deltas = []
    reply = NvidiaKimiProvider(ALLOW, client=streamed.client()).generate_stream(sql_context(retriever), deltas.append)
    reference = NvidiaKimiProvider(ALLOW, client=plain.client()).generate(sql_context(retriever))
    assert len(deltas) > 1 and "".join(deltas) == "El docente explica que ORDER BY ordena el resultado de la consulta . Sin él, no hay un orden garantizado."
    assert REASONING_SECRET not in "".join(deltas) + reply.text and "Pienso" not in "".join(deltas)
    assert reply.text == reference.text and reply.cited_indices == reference.cited_indices == (0,)
    assert reply.provider == "nvidia-kimi" and reply.mode == "LIVE" and reply.usage["reasoning_tokens"] == 120
    sent = streamed.payloads[0]
    assert sent["stream"] is True and streamed.requests[0].headers["accept"] == "text/event-stream"
    assert FAKE_NVIDIA_KEY not in json.dumps(sent) and "min " not in "".join(deltas)


def test_the_streamed_request_goes_through_the_same_privacy_barrier(retriever):
    from app.privacy import PrivacyFilter
    fake = FakeKimi(sse_responder(good_answer("S1")))
    provider = NvidiaKimiProvider(ALLOW, client=fake.client())
    provider.attach_privacy(PrivacyFilter(extra_names=["Zulemita"]))
    provider.generate_stream(sql_context(retriever, student_message="Explícame ORDER BY, dice Zulemita"), lambda t: None)
    assert "Zulemita" not in json.dumps(fake.payloads, ensure_ascii=False) and "[nombre]" in json.dumps(fake.payloads, ensure_ascii=False)


def test_a_streamed_answer_that_fails_verification_never_becomes_a_reply(retriever):
    from app.llm.errors import LLMMalformedResponse
    bad = {"answer": "Dice «una frase inventada que la clase jamás pronunció» [S1].", "used_segments": ["S1"]}
    deltas = []
    with pytest.raises(LLMMalformedResponse):
        NvidiaKimiProvider(ALLOW, client=FakeKimi(sse_responder(bad)).client()).generate_stream(sql_context(retriever), deltas.append)
    assert deltas                                              # el texto provisional pudo mostrarse...
    # ...pero el servicio lo reemplaza por el fallback DEMO verificado (ver la prueba del endpoint)


def test_a_cancelled_stream_stops_reading_and_raises(retriever):
    seen = []

    def stop_after_first_text():
        return len(seen) >= 1
    with pytest.raises(StreamCancelled):
        NvidiaKimiProvider(ALLOW, client=FakeKimi(sse_responder(good_answer("S1"), piece=4)).client()).generate_stream(
            sql_context(retriever), seen.append, stop_after_first_text)
    assert 1 <= len(seen) < 6


def test_stream_errors_are_mapped_like_the_normal_path(retriever):
    from app.llm.errors import LLMRateLimited, LLMTimeout, LLMUnavailable
    expectations = [(httpx.Response(429, json={"title": "Too Many"}), LLMRateLimited), (httpx.Response(503, json={}), LLMUnavailable)]
    for response, exc_type in expectations:
        provider = NvidiaKimiProvider(ALLOW, client=FakeKimi(lambda r, p, resp=response: resp).client())
        with pytest.raises(exc_type):
            provider.generate_stream(sql_context(retriever), lambda t: None)

    def hang(request, payload):
        raise httpx.ReadTimeout("sin datos")
    with pytest.raises(LLMTimeout) as excinfo:
        NvidiaKimiProvider(ALLOW, client=FakeKimi(hang).client()).generate_stream(sql_context(retriever), lambda t: None)
    assert excinfo.value.public_message == "NVIDIA no respondió en 10 s."


def test_truncated_or_reasoning_only_streams_are_rejected_not_shown_as_answers(retriever):
    from app.llm.errors import LLMMalformedResponse
    only_reasoning = lambda r, p: httpx.Response(200, content=b'data: {"choices":[{"delta":{"reasoning_content":"solo pienso"}}]}\n\ndata: [DONE]\n\n')   # noqa: E731
    with pytest.raises(LLMMalformedResponse, match="vac"):
        NvidiaKimiProvider(ALLOW, client=FakeKimi(only_reasoning).client()).generate_stream(sql_context(retriever), lambda t: None)
    cut = FakeKimi(sse_responder(good_answer("S1"), finish="length"))
    with pytest.raises(LLMMalformedResponse, match="truncada"):
        NvidiaKimiProvider(ALLOW, client=cut.client()).generate_stream(sql_context(retriever), lambda t: None)


# ===================================================================== D. streaming — endpoint SSE
def read_events(response):
    events, name = [], None
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:") and name:
            events.append((name, json.loads(line[5:].strip())))
            name = None
    return events


def stream_body(message="¿Qué es n8n?", student="demo-student-001", session=None):
    return {"student_id": student, "course_id": N8N_COURSE, "lesson_id": N8N_LESSON, "session_id": session, "message": message}


def test_the_stream_endpoint_is_off_by_default_and_the_normal_chat_is_unaffected(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch, streaming=False)) as c:
        refused = c.post("/api/chat/stream", json=stream_body())
        normal = chat_n8n(c, "¿Qué es n8n?")
        health = c.get("/health").json()["llm"]
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "streaming_disabled" and fake.calls == 1
    assert normal.status_code == 200 and health["streaming"] == {"enabled": False, "supported": True}


def test_the_stream_endpoint_sends_status_deltas_and_one_final_with_verified_citations(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(sse_responder(good_answer("S1"), piece=7))
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch, streaming=True)) as c:
        with c.stream("POST", "/api/chat/stream", json=stream_body()) as response:
            assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
            events = read_events(response)
        health = c.get("/health").json()["llm"]
    names = [n for n, _ in events]
    assert names[0] == "status" and names[-1] == "final" and names.count("final") == 1 and "delta" in names
    assert all(n in {"status", "delta", "final"} for n in names) and names.index("delta") < names.index("final")
    provisional = "".join(d["text"] for n, d in events if n == "delta")
    final = events[-1][1]
    assert "[S" not in provisional and "min " not in provisional and REASONING_SECRET not in json.dumps(events)
    assert final["answer_origin"] == "live" and final["mode"] == "LIVE" and final["references"] and "min " in final["response"]   # citas SOLO en el final
    assert final["timing"]["first_text_ms"] > 0 and final["timing"]["first_text_ms"] <= final["timing"]["total_ms"]
    assert health["streaming"] == {"enabled": True, "supported": True} and fake.calls == 1 and fake.payloads[0]["stream"] is True


def test_a_failing_stream_ends_with_a_demo_fallback_final_and_never_an_unverified_answer(db_path, fake_assets, monkeypatch):
    bad = {"answer": "El profesor dijo «algo totalmente inventado que nadie pronunció jamás» [S1].", "used_segments": ["S1"]}
    with TestClient(n8n_live(db_path, fake_assets, FakeKimi(sse_responder(bad)), monkeypatch, streaming=True)) as c:
        with c.stream("POST", "/api/chat/stream", json=stream_body()) as response:
            events = read_events(response)
    final = events[-1]
    assert final[0] == "final" and final[1]["fallback_used"] is True and final[1]["answer_origin"] == "demo_fallback" and final[1]["mode"] == "DEMO"
    assert "llm_malformed_response" in final[1]["provider_notice"] and "Kimi no participó" in final[1]["provider_notice"]


def test_a_down_provider_in_streaming_mode_yields_the_demo_final(db_path, fake_assets, monkeypatch):
    with TestClient(n8n_live(db_path, fake_assets, FakeKimi(DOWN), monkeypatch, streaming=True)) as c:
        with c.stream("POST", "/api/chat/stream", json=stream_body()) as response:
            events = read_events(response)
    assert [n for n, _ in events][-1] == "final" and events[-1][1]["fallback_used"] is True and events[-1][1]["references"]


def test_stream_errors_use_the_controlled_error_event(db_path, fake_assets, monkeypatch):
    with TestClient(n8n_live(db_path, fake_assets, FakeKimi(OK), monkeypatch, streaming=True)) as c:
        with c.stream("POST", "/api/chat/stream", json={**stream_body(), "session_id": "no-existe"}) as response:
            events = read_events(response)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "session_not_found" and "Traceback" not in json.dumps(events)


def test_a_cache_hit_in_streaming_mode_returns_only_the_labeled_final(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(sse_responder(good_answer("S1")))
    with TestClient(n8n_live(db_path, fake_assets, fake, monkeypatch, cache=True, streaming=True)) as c:
        with c.stream("POST", "/api/chat/stream", json=stream_body()) as r1:
            read_events(r1)
        with c.stream("POST", "/api/chat/stream", json=stream_body(student="demo-student-002")) as r2:
            events = read_events(r2)
    assert fake.calls == 1 and [n for n, _ in events] == ["status", "final"] and events[-1][1]["answer_origin"] == "cached"


class BlockingProvider:
    """Proveedor de prueba que 'razona' hasta que lo cancelan: demuestra que cerrar la conexión corta la llamada."""
    name, mode, supports_streaming, max_evidence, secondary_score_ratio = "bloqueante", "LIVE", True, 2, 0.7

    def __init__(self):
        self.started, self.stopped = threading.Event(), threading.Event()
        self.cancel_seen = False

    def generate_stream(self, context, on_delta, should_cancel=None):
        on_delta("primer texto")
        self.started.set()
        for _ in range(400):
            if should_cancel and should_cancel():
                self.cancel_seen = True
                self.stopped.set()
                raise StreamCancelled()
            time.sleep(0.01)
        self.stopped.set()
        raise AssertionError("no se canceló")

    def generate(self, context):
        raise AssertionError("debía usarse el modo streaming")


def blocking_service(tmp_path, fake_assets):
    from app.knowledge.real_lesson import load_real_lesson, register_real_lesson
    from app.services.tutor_service import TutorService
    from app.storage.conversation_store import ConversationStore
    real = load_real_lesson(fake_assets)
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    store = ConversationStore(tmp_path / "b.db")
    store.initialize()
    provider = BlockingProvider()
    return TutorService(kb, store, provider, privacy=real.privacy), provider, store


def test_closing_the_connection_cancels_the_provider_call_and_stores_nothing(tmp_path, fake_assets):
    service, provider, store = blocking_service(tmp_path, fake_assets)
    request = ChatRequest(student_id="demo-student-001", course_id=N8N_COURSE, lesson_id=N8N_LESSON, message="¿Qué es n8n?")
    generator = sse_events(service, request, heartbeat=0.05)
    assert next(generator).startswith("event: status")
    assert "primer texto" in next(generator)
    assert provider.started.wait(2)
    generator.close()                                                           # el navegador se fue
    assert provider.stopped.wait(3) and provider.cancel_seen
    assert rows(tmp_path / "b.db", "SELECT 1 FROM messages") == []              # no se guardó ningún turno a medias


def test_a_silent_stream_sends_keepalive_comments(tmp_path, fake_assets):
    service, provider, _ = blocking_service(tmp_path, fake_assets)
    request = ChatRequest(student_id="demo-student-001", course_id=N8N_COURSE, lesson_id=N8N_LESSON, message="¿Qué es n8n?")
    generator = sse_events(service, request, heartbeat=0.02)
    first = [next(generator) for _ in range(4)]
    generator.close()
    assert provider.stopped.wait(3)
    assert any(frame == ": keep-alive\n\n" for frame in first)


# ===================================================================== frontend (comprobaciones estáticas)
from pathlib import Path   # noqa: E402

JS = (Path(__file__).resolve().parents[2] / "frontend" / "demo-n8n.js").read_text(encoding="utf-8")


def test_the_frontend_streams_only_when_asked_and_renders_everything_as_text():
    assert 'get("stream") === "1"' in JS and "/api/chat/stream" in JS and "streaming_disabled" in JS         # opt-in y vuelta al chat normal
    assert "getReader" in JS and "TextDecoder" in JS and "Borrador" in JS                                  # el provisional se rotula como borrador
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", JS, flags=re.S)
    assert "innerHTML" not in code and "eval(" not in code and "insertAdjacentHTML" not in code
    assert "Respuesta guardada" in JS and "reply.mode === \"CACHED\"" in JS                                 # la caché se distingue de LIVE y de DEMO
    assert "Primer texto" in JS and "timing" in JS


# ===================================================================== navegador real: streaming opt-in y etiqueta de caché
import asyncio   # noqa: E402
import contextlib   # noqa: E402

from browser_helpers import chrome_page, find_chrome, free_port, static_server   # noqa: E402


@contextlib.contextmanager
def running_backend(app, port):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


WATCH_DRAFT = """(() => { window.__draft = [];
  new MutationObserver(() => { const d = document.querySelector('.demo-draft'); if (d && d.textContent) window.__draft.push(d.textContent); })
    .observe(document.getElementById('messages'), {subtree: true, childList: true, characterData: true}); })()"""
LAST_TUTOR = """(() => { const m = [...document.querySelectorAll('.demo-msg.tutor:not(.pending)')].pop();
  return m ? {pills: [...m.querySelectorAll('.demo-pill')].map(x => [x.textContent, x.className]), cites: m.querySelectorAll('.demo-cite').length,
              notes: [...m.querySelectorAll('.demo-note')].map(x => x.textContent), text: m.firstChild.textContent} : null; })()"""


async def browser_ask(url, question, watch=False):
    async with chrome_page(url) as c:
        await c.wait("document.getElementById('chapterList').children.length > 0", 40)
        if watch:
            await c.js(WATCH_DRAFT)
        await c.js(f"(() => {{ document.getElementById('question').value = {json.dumps(question)}; document.getElementById('chatForm').requestSubmit(); }})()")
        await c.wait("!document.querySelector('.demo-msg.pending') && document.querySelectorAll('.demo-msg.tutor:not(.pending)').length >= 1", 40)
        return {"answer": await c.js(LAST_TUTOR), "drafts": await c.js("window.__draft || []"), "draft_left": await c.js("!!document.querySelector('.demo-draft')")}


@pytest.mark.skipif(find_chrome() is None, reason="no hay Chrome/Edge instalado")
def test_in_a_real_browser_streaming_shows_a_draft_then_replaces_it_with_the_verified_answer(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(sse_responder(good_answer("S1"), piece=6))
    api_port, front_port = free_port(), free_port()
    monkeypatch.setattr(settings, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(settings, "CHAT_STREAMING_ENABLED", True)
    app = create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets, llm_config=ALLOW,
                     llm_client=fake.client(), academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN),
                     cors_origins=[f"http://127.0.0.1:{front_port}"])
    with running_backend(app, api_port), static_server(front_port):
        url = f"http://127.0.0.1:{front_port}/demo-n8n.html?stream=1&api=http://127.0.0.1:{api_port}"
        result = asyncio.run(browser_ask(url, "¿Qué es n8n?", watch=True))
    drafts = result["drafts"]
    assert drafts and all("[S" not in d for d in drafts) and REASONING_SECRET not in json.dumps(drafts)       # hubo borrador y sin etiquetas ni razonamiento
    assert not result["draft_left"]                                                                         # al final el borrador desaparece
    answer = result["answer"]
    assert answer["cites"] >= 1 and "min " in answer["text"]                                                  # citas y minutos SOLO en la respuesta verificada
    labels = [p[0] for p in answer["pills"]]
    assert any(l.startswith("Respuesta: LIVE") for l in labels) and any(l.startswith("⏱") and "Primer texto" in l and "servidor" in l for l in labels)
    assert fake.payloads[0]["stream"] is True and fake.calls == 1


@pytest.mark.skipif(find_chrome() is None, reason="no hay Chrome/Edge instalado")
def test_in_a_real_browser_the_default_page_uses_the_normal_chat_and_a_repeated_question_is_labeled_as_saved(db_path, fake_assets, monkeypatch):
    fake = FakeKimi(OK)
    api_port, front_port = free_port(), free_port()
    monkeypatch.setattr(settings, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_STREAMING_ENABLED", True)          # aunque el backend lo permita, sin ?stream=1 la página usa /api/chat
    app = create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets, llm_config=ALLOW,
                     llm_client=fake.client(), academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN),
                     cors_origins=[f"http://127.0.0.1:{front_port}"])
    with running_backend(app, api_port), static_server(front_port):
        url = f"http://127.0.0.1:{front_port}/demo-n8n.html?api=http://127.0.0.1:{api_port}"
        first = asyncio.run(browser_ask(url, "¿Qué es n8n?", watch=True))
        second = asyncio.run(browser_ask(url, "¿Qué es n8n?"))             # otra visita = otra conversación
    assert first["drafts"] == [] and any(p[0].startswith("Respuesta: LIVE") for p in first["answer"]["pills"])
    labels = [p for p in second["answer"]["pills"]]
    saved = [p for p in labels if p[0].startswith("Respuesta guardada")]
    assert saved and "cached" in saved[0][1] and not any(p[0].startswith("Respuesta: LIVE") for p in labels)
    assert any("no es una generación nueva" in n for n in second["answer"]["notes"]) and second["answer"]["cites"] >= 1
    assert fake.calls == 1                                                  # la segunda visita no esperó a Kimi


# ===================================================================== F. comparación de modelos (preparada, no ejecutada)
import importlib.util   # noqa: E402

COMPARE = Path(__file__).resolve().parents[1] / "scripts" / "compare_models.py"


@pytest.fixture(scope="module")
def compare():
    spec = importlib.util.spec_from_file_location("compare_models", COMPARE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_model_comparison_defaults_to_a_plan_and_sends_nothing(compare, capsys, tmp_path):
    def forbidden():
        raise AssertionError("no debía crearse ningún cliente HTTP")
    assert compare.run([], client_factory=forbidden, out=tmp_path / "r.md") == 0
    out = capsys.readouterr().out
    assert "Solicitudes planificadas: 9" in out and "FICTICIO (sql-demo)" in out and "No se envió nada" in out
    assert "moonshotai/kimi-k3" in out and "meta/llama-3.1-8b-instruct" in out and "nemotron-3.5-lightning" in out
    assert not (tmp_path / "r.md").exists() and FAKE_NVIDIA_KEY not in out


@pytest.mark.parametrize("argv, fragment", [
    (["--i-authorize-real-requests", "3"], "necesita 9"),
    (["--course", "real", "--i-authorize-real-requests", "9"], "--include-class-content"),
    (["--i-authorize-real-requests", "9"], "NVIDIA_API_KEY"),
])
def test_the_comparison_refuses_without_every_authorization(compare, capsys, tmp_path, argv, fragment):
    assert compare.run(argv, client_factory=lambda: (_ for _ in ()).throw(AssertionError("sin red")), out=tmp_path / "r.md") == 2
    assert fragment in capsys.readouterr().out and not (tmp_path / "r.md").exists()


def test_the_authorized_comparison_sends_exactly_the_planned_requests_with_per_model_parameters(compare, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", FAKE_NVIDIA_KEY)
    fake = FakeKimi(OK)
    code = compare.run(["--questions", "2", "--i-authorize-real-requests", "6"], client_factory=fake.client, out=tmp_path / "r.md")
    out = capsys.readouterr().out
    assert code == 0 and fake.calls == 6 and FAKE_NVIDIA_KEY not in out
    by_model = {}
    for payload in fake.payloads:
        by_model.setdefault(payload["model"], []).append(payload)
    assert set(by_model) == {"moonshotai/kimi-k3", "meta/llama-3.1-8b-instruct", "nvidia/nemotron-3.5-lightning-30b-a3b"} and all(len(v) == 2 for v in by_model.values())
    assert all(p["reasoning_effort"] == "low" and p["max_completion_tokens"] >= 512 for p in by_model["moonshotai/kimi-k3"])
    assert all("reasoning_effort" not in p for m, ps in by_model.items() if m != "moonshotai/kimi-k3" for p in ps)      # parámetro propio de K3
    assert all(p["stream"] is False for ps in by_model.values() for p in ps)
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "| modelo |" in report and "meta/llama-3.1-8b-instruct" in report and FAKE_NVIDIA_KEY not in report
    assert all(r.headers["authorization"] == f"Bearer {FAKE_NVIDIA_KEY}" for r in fake.requests)


def test_a_failing_model_is_recorded_and_does_not_stop_the_comparison(compare, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", FAKE_NVIDIA_KEY)

    def responder(request, payload):
        return httpx.Response(404, json={"title": "no"}) if payload["model"].startswith("meta/") else json_response(nv_completion(good_answer("S1")))
    fake = FakeKimi(responder)
    compare.run(["--questions", "1", "--i-authorize-real-requests", "3"], client_factory=fake.client, out=tmp_path / "r.md")
    report = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert fake.calls == 3 and "| meta/llama-3.1-8b-instruct |" in report and "error" in report.lower() and "| moonshotai/kimi-k3 |" in report


def test_non_reasoning_models_get_neither_the_k3_parameter_nor_the_512_token_floor(retriever):
    cfg = replace(ALLOW, model="meta/llama-3.1-8b-instruct", reasoning_effort="none", max_output_tokens=300)
    fake = FakeKimi(OK)
    provider = NvidiaKimiProvider(cfg, client=fake.client())
    provider.generate(sql_context(retriever))
    payload = fake.payloads[0]
    assert "reasoning_effort" not in payload and payload["max_completion_tokens"] == 300 and provider.limits()["max_output_tokens"] == 300
    k3 = FakeKimi(OK)
    NvidiaKimiProvider(replace(ALLOW, max_output_tokens=300), client=k3.client()).generate(sql_context(retriever))
    assert k3.payloads[0]["reasoning_effort"] == "low" and k3.payloads[0]["max_completion_tokens"] == 512            # K3 conserva su mínimo
