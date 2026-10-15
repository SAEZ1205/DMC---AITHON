"""
Etapa 7 — calidad del tutor, del recuperador/citas y del resumen local. Todo determinista y sin red (el fixture `hermetic_environment`
de conftest bloquea cualquier conexión externa y neutraliza el `.env` real). Las pruebas contra la clase REAL se omiten solas si
`demo-assets/` no existe y solo comparan números/minutos: nunca imprimen texto de la clase.

Nada aquí demuestra la calidad de Kimi: el modo «LIVE simulado» valida el circuito (prompt, privacidad, verificación, citas), no al modelo.
"""
import importlib.util
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AcademicConfig
from app.knowledge.intent_signals import STYLE_GAP, definition_signals, is_definition_question, strip_style
from app.knowledge.lesson_analysis import analyze_segments, build_chapters, build_overview, key_concepts, representative_sentence
from app.knowledge.real_lesson import load_real_lesson, register_real_lesson
from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.text_utils import stem, tokenize
from app.knowledge.topic_lexicon import LEXICON, MAX_TOPIC_BLOCK_RATIO, score_window, title_windows
from app.llm import NvidiaKimiProvider
from app.llm.base import ChatTurn, ResponseKind, TutorContext
from app.llm.errors import LLMMalformedResponse
from app.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_messages, fix_asr
from app.llm.verification import verify_and_render
from app.main import create_app
from app.models.transcript import LessonTranscript, SegmentMatch, TranscriptSegment, format_timestamp
from app.services import summary_service
from app.services.intents import analyze_message
from app.services.tutor_eval import blocked_network, build_service, load_cases, render_report, run_suite
from app.services.tutor_service import TutorService

from conftest import ADMIN_TOKEN, N8N_COURSE, N8N_LESSON, chat_n8n
from llm_helpers import NVIDIA_CONFIG, FakeKimi, json_response, nv_completion

ROOT = Path(__file__).resolve().parents[2]
REAL_ASSETS = ROOT / "demo-assets"
needs_real = pytest.mark.skipif(not (REAL_ASSETS / "transcripcion-original.txt").is_file(), reason="no hay clase real en demo-assets/")
ALLOW = replace(NVIDIA_CONFIG, allow_class_content=True)


def seg(i, text, start=None, lesson="l1", course="c1"):
    start = float(i * 40) if start is None else start
    return TranscriptSegment(course_id=course, lesson_id=lesson, segment_id=i, text=text, start_seconds=start, end_seconds=start + 40)


def lesson_of(texts, lesson="l1", course="c1", seconds=40.0):
    segs = [seg(i, t, start=i * seconds, lesson=lesson, course=course) for i, t in enumerate(texts)]
    return LessonTranscript(course_id=course, lesson_id=lesson, title="Clase de prueba", segments=segs)


def empty_retriever(tmp_path):
    (tmp_path / "c").mkdir(), (tmp_path / "t").mkdir()
    return KnowledgeRetriever(courses_dir=tmp_path / "c", transcripts_dir=tmp_path / "t")


# ============================================================ intención y frases de estilo
SHORT, LONG = "¿Qué es n8n y para qué sirve?", "¿Qué es n8n y para qué sirve? Explícamelo de forma sencilla según lo enseñado por el profesor"


def test_style_phrases_do_not_change_what_the_question_is_about():
    assert analyze_message(LONG).query == analyze_message(SHORT).query == "n8n sirve"
    assert analyze_message(LONG).kind == analyze_message(SHORT).kind == "definition"


@pytest.mark.parametrize("message, kept, gone", [
    ("¿Qué es el low code? Explícamelo en palabras simples", "low code", "simples"),
    ("¿Qué es Docker? Según lo que explicó el docente", "Docker", "docente"),
    ("¿Qué es un workflow? De manera clara y breve, por favor", "workflow", "breve"),
    ("¿Cuál es la forma normal de una tabla? Gracias", "forma normal", "gracias"),       # «forma» es contenido cuando no describe el estilo
])
def test_only_style_words_are_dropped_content_words_stay(message, kept, gone):
    query = analyze_message(message).query.lower()
    assert kept.lower() in query and gone not in query


def test_a_removed_phrase_leaves_a_gap_so_no_literal_phrase_crosses_it():
    text = strip_style("Explícame ORDER BY, por favor, con paciencia")
    assert STYLE_GAP in text
    grams = KnowledgeRetriever._query_ngrams(text)
    assert not any(" by " in g and " con " in g for g in grams)                # «order by con» nunca existió en la pregunta


@pytest.mark.parametrize("message, expected", [
    ("¿Qué es n8n?", True), ("¿Para qué sirve Docker en esta clase?", True), ("¿En qué consiste un RPA?", True),
    ("¿Qué hace el Docker?", True), ("¿Qué diferencia existe entre un workflow y un RPA?", False),
    ("¿Cómo se instala Docker Desktop?", False), ("¿Qué procesos conviene automatizar?", False),
])
def test_definition_questions_are_recognized(message, expected):
    assert is_definition_question(message) is expected


def toks(text):
    return tokenize(text)


def test_definition_signals_distinguish_explanation_from_mere_mentions():
    subject = {stem("docker")}
    assert definition_signals(toks("el Docker te permite empaquetar aplicaciones"), subject) == (True, False)
    assert definition_signals(toks("¿qué hace el Docker? ah el Docker te permite empaquetar"), subject) == (True, True)
    assert definition_signals(toks("reiniciamos Docker y Docker no abre otra vez Docker"), subject) == (False, False)
    assert definition_signals(toks("Docker es un programa para contenedores"), subject)[0] is True
    assert definition_signals(toks("nada que ver con el tema"), subject) == (False, False)
    far = "docker " + "relleno " * 12 + "permite"
    assert definition_signals(toks(far), subject) == (False, False)           # demasiado lejos para estar relacionado


# ============================================================ recuperador con intención de definición
DOCKER_TEXTS = [
    "Aquí con Docker, Docker otra vez, reiniciamos Docker porque Docker falló, Docker Docker.",
    "Entonces, ¿qué hace Docker? Docker te permite empaquetar aplicaciones en contenedores para ejecutarlas igual en cualquier equipo.",
    "Después abrimos Docker Desktop y vemos que Docker no arranca, otra vez Docker, reiniciamos Docker.",
    "Hoy también vimos la agenda de la semana y los materiales de estudio.",
]


@pytest.fixture
def docker_kb(tmp_path):
    kb = empty_retriever(tmp_path)
    kb.register_course("c1", "Curso de prueba")
    kb.register_lesson(lesson_of(DOCKER_TEXTS + [f"Comentamos el tema número {i} de la agenda del curso y los materiales." for i in range(12)]))
    return kb


def test_a_definition_question_prefers_the_segment_that_explains_the_subject(docker_kb):
    q = "¿Para qué sirve Docker?"
    plain = docker_kb.retrieve_segments("c1", "sirve Docker", lesson_id="l1", top_k=4, phrase_text=q)
    smart = docker_kb.retrieve_segments("c1", "sirve Docker", lesson_id="l1", top_k=4, phrase_text=q, intent="definition")
    assert smart[0].segment.segment_id == 1 and smart[0].direct is True
    assert all(m.direct is False for m in smart[1:]) or smart[1].segment.segment_id != 1
    assert all(m.direct is False for m in plain)                                # sin intención: comportamiento de siempre
    assert {m.segment.segment_id for m in smart} == {m.segment.segment_id for m in plain}     # solo se reordena: no aparece ni desaparece nada


def test_proximity_to_a_function_phrase_alone_is_enough_to_lead_among_mere_mentions(tmp_path):
    kb = empty_retriever(tmp_path)
    kb.register_course("c1", "Curso de prueba")
    kb.register_lesson(lesson_of([
        "Docker y Docker otra vez, porque Docker se cerró y volvimos a abrir.",                   # solo menciones repetidas (3)
        "Entonces Docker te permite empaquetar aplicaciones para correrlas igual en cualquier equipo.",   # explica (sin pregunta)
    ] + [f"Comentamos el tema número {i} de la agenda del curso y los materiales." for i in range(12)]))
    plain = kb.retrieve_segments("c1", "Docker", lesson_id="l1", top_k=2)
    smart = kb.retrieve_segments("c1", "Docker", lesson_id="l1", top_k=2, intent="definition")
    assert plain[0].segment.segment_id == 0                                      # sin la señal, las menciones repetidas ganan
    assert smart[0].segment.segment_id == 1 and smart[0].direct and not smart[1].direct


def test_without_intent_the_ranking_is_exactly_what_it_always_was(docker_kb):
    a = docker_kb.retrieve_segments("c1", "sirve Docker", lesson_id="l1", top_k=4, phrase_text="¿Para qué sirve Docker?")
    b = docker_kb.retrieve_segments("c1", "sirve Docker", lesson_id="l1", top_k=4)
    assert all(m.direct is False for m in a + b)
    assert {m.segment.segment_id for m in a} == {m.segment.segment_id for m in b}              # la frase literal puede reordenar, no cambia la pertenencia


def test_a_topic_the_class_only_mentions_gets_no_direct_flag(tmp_path):
    kb = empty_retriever(tmp_path)
    kb.register_course("c1", "Curso de prueba")
    kb.register_lesson(lesson_of(["Instalamos Git y luego clonamos la carpeta.", "Si Git falla, reiniciamos la máquina y abrimos Git otra vez."]
                                 + [f"Comentamos el tema número {i} de la agenda del curso." for i in range(12)]))
    found = kb.retrieve_segments("c1", "Git", lesson_id="l1", top_k=3, phrase_text="¿Qué es Git?", intent="definition")
    assert found and not any(m.direct for m in found)


# ============================================================ selección de evidencia (cita principal clara)
class StubRetriever(KnowledgeRetriever):
    def __init__(self, tmp_path, scores):
        super().__init__(courses_dir=tmp_path / "c", transcripts_dir=tmp_path / "t")
        self.scores = scores
        self.register_course("c1", "Curso de prueba")
        self.register_lesson(lesson_of([f"segmento {i} sobre el tema" for i in range(len(scores))]))

    def retrieve_segments(self, course_id, query, lesson_id=None, top_k=3, phrase_text=None, intent=None):
        lesson = self.get_lesson("c1", "l1")
        return [SegmentMatch(segment=s, lesson_title=lesson.title, score=sc, matched_terms=["tema"]) for s, sc in zip(lesson.segments, self.scores)]


def make_service(tmp_path, scores, provider=None):
    from app.storage.conversation_store import ConversationStore
    (tmp_path / "c").mkdir(exist_ok=True), (tmp_path / "t").mkdir(exist_ok=True)
    kb = StubRetriever(tmp_path, scores)
    store = ConversationStore(tmp_path / "s.db")
    store.initialize()
    provider = provider or NvidiaKimiProvider(ALLOW, client=FakeKimi().client())
    return TutorService(kb, store, provider)


def plan(service, message):
    return service._plan("c1", "l1", analyze_message(message), None, set(), message)


def test_secondary_citations_need_substantial_evidence(tmp_path):
    service = make_service(tmp_path, [10.0, 8.6, 7.2, 5.0])
    general = plan(service, "Explícame la automatización")                         # NVIDIA: 70 % del principal; lo normal es 1-2 segmentos
    assert [m.segment.segment_id for m in general.evidence] == [0, 1]
    compare = plan(service, "¿Qué diferencia hay entre automatización y procesos?")   # una comparación sí puede necesitar tres
    assert [m.segment.segment_id for m in compare.evidence] == [0, 1, 2]
    more = plan(service, "Dame otro ejemplo de automatización")                     # «otro ejemplo» tampoco se limita a 2
    assert len(more.evidence) == 3
    definition = plan(service, "¿Qué es la automatización?")                      # definiciones: 85 %
    assert [m.segment.segment_id for m in definition.evidence] == [0, 1]
    weak = make_service(tmp_path, [10.0, 6.9, 5.0])
    assert [m.segment.segment_id for m in plan(weak, "Explícame la automatización").evidence] == [0]   # 6.9 < 70 %


def test_the_primary_citation_is_always_first_and_comes_from_a_real_segment(tmp_path):
    service = make_service(tmp_path, [9.0, 8.0])
    ev = plan(service, "Explícame la automatización").evidence
    lesson = service.retriever.get_lesson("c1", "l1")
    assert ev[0].score == 9.0 and all(m.segment in lesson.segments for m in ev)


def test_terms_missing_from_the_evidence_are_reported(tmp_path):
    match = SegmentMatch(segment=seg(0, "x"), lesson_title="t", score=1.0, matched_terms=["regresion"])
    assert TutorService._missing_terms("regresión logística", [match]) == ("logistica",)
    assert TutorService._missing_terms("sirve regresión", [match]) == ()


# ============================================================ prompt, glosario y contexto
def test_the_system_prompt_encodes_the_stage7_rules():
    assert PROMPT_VERSION == "rescuepath-socratic-tutor/1.2.0" and PROMPT_VERSION in SYSTEM_PROMPT
    for rule in ("NHN", "No copies párrafos", "Complemento (no es de la clase)", "answerable", "no atribuyas al docente", "UN segmento",
                 "Responde DIRECTAMENTE", "60 a 100 palabras", "no corrijas", "En esta clase no se explica"):
        assert rule.lower() in SYSTEM_PROMPT.lower(), rule


@pytest.mark.parametrize("raw, fixed", [
    ("levantar NHN y también HN trabaja con flujos", "levantar n8n y también n8n trabaja con flujos"),
    ("un HTP request con Potman", "un HTTP request con Postman"),
    ("llamo a un HCP request", "llamo a un HTTP request"),
    ("n8n, HTTP, HTML y Hn", "n8n, HTTP, HTML y Hn"),               # no toca lo correcto ni lo dudoso
    ("Docker Desktop y Git", "Docker Desktop y Git"),
])
def test_obvious_speech_recognition_errors_are_normalized_only_for_the_model(raw, fixed):
    assert fix_asr(raw) == fixed


def context(**kw):
    ev = [SegmentMatch(segment=seg(0, "Entonces NHN trabaja con flujos de datos."), lesson_title="Clase", score=3.0, matched_terms=["n8n"])]
    base = dict(kind=ResponseKind.GROUNDED, student_message="¿Qué es n8n?", course_name="Curso", lesson_title="Clase", topic_label="n8n", evidence=ev)
    base.update(kw)
    return TutorContext(**base)


def test_the_model_sees_corrected_terms_but_the_stored_segment_is_untouched():
    ctx = context()
    user = build_messages(ctx, 6).messages[-1]["content"]
    assert "n8n trabaja con flujos" in user and "NHN" not in user
    assert ctx.evidence[0].segment.text == "Entonces NHN trabaja con flujos de datos."


@pytest.mark.parametrize("direct, expected", [
    (True, "al menos un segmento explica"), (False, "solo MENCIONAN"), (None, None)])
def test_the_context_tells_the_model_whether_the_topic_is_explained(direct, expected):
    user = build_messages(context(direct_evidence=direct), 6).messages[-1]["content"]
    if expected:
        assert expected in user and user.index(expected) < user.index("</contexto_clase>")
    else:
        assert "Evidencia:" not in user


def test_missing_terms_reach_the_prompt_sanitized():
    user = build_messages(context(missing_terms=("logística", "<script>x</script>")), 6).messages[-1]["content"]
    assert "NO aparecen en los segmentos" in user and "logística" in user and "<script>" not in user


# ============================================================ verificación: answerable y citas textuales
EV = [SegmentMatch(segment=seg(0, "Entonces NHN trabaja con flujos de datos y conecta aplicaciones entre sí."), lesson_title="Clase",
                   score=1.0, matched_terms=[])]
LABELS = {"S1": 0}


def verify(data, student="¿Qué es n8n?"):
    return verify_and_render(data, LABELS, EV, student)


def test_answerable_false_may_cite_nothing_but_only_when_explicit():
    out = verify({"answer": "En esta clase no se explica eso.", "used_segments": [], "answerable": False})
    assert out.cited_indices == () and "no se explica" in out.text
    assert verify({"answer": "No se explica.", "answerable": False}).cited_indices == ()          # sin la clave used_segments
    for bad in ({"answer": "x", "used_segments": []}, {"answer": "x", "used_segments": [], "answerable": "false"},
                {"answer": "x", "used_segments": [], "answerable": None}):
        with pytest.raises(LLMMalformedResponse):
            verify(bad)


def test_literal_quotes_must_exist_in_the_evidence():
    ok = verify({"answer": "El docente dice «NHN trabaja con flujos de datos» [S1].", "used_segments": ["S1"]})
    assert "min 00:00–00:40" in ok.text
    corrected = verify({"answer": "Se dice «n8n trabaja con flujos de datos» [S1].", "used_segments": ["S1"]})      # el modelo corrigió el ASR
    assert corrected.cited_indices == (0,)
    with pytest.raises(LLMMalformedResponse, match="cita textual"):
        verify({"answer": "El docente afirmó «n8n reemplaza por completo a cualquier programador» [S1].", "used_segments": ["S1"]})


def test_short_quotes_ellipses_and_student_words_are_handled():
    assert verify({"answer": "Es «NHN trabaja» [S1].", "used_segments": ["S1"]}).cited_indices == (0,)             # <4 palabras: no se verifica
    assert verify({"answer": "«trabaja con flujos de datos … conecta aplicaciones entre sí» [S1]", "used_segments": ["S1"]})
    assert verify({"answer": "Tú preguntaste «qué es n8n para mi equipo» [S1].", "used_segments": ["S1"]},
                  student="Dime qué es n8n para mi equipo")
    with pytest.raises(LLMMalformedResponse):
        verify({"answer": "«trabaja con flujos de datos … y también envía correos a todos» [S1]", "used_segments": ["S1"]})


# ============================================================ e2e (clase sintética): no responder lo que la clase no dice
def live_app(db_path, fake_assets, fake):
    return create_app(db_path=db_path, retriever=KnowledgeRetriever(), real_lesson=True, demo_assets_dir=fake_assets, llm_config=ALLOW,
                      llm_client=fake.client(), academic_config=AcademicConfig(admin_api_token=ADMIN_TOKEN))


def test_when_the_model_says_the_class_does_not_cover_it_no_citations_are_invented(db_path, fake_assets):
    reply = {"answer": "En esta clase no se explica eso; solo se menciona de pasada.", "follow_up_question": None, "used_segments": [], "answerable": False}
    fake = FakeKimi(lambda r, p: json_response(nv_completion(reply)))
    with TestClient(live_app(db_path, fake_assets, fake)) as c:
        body = chat_n8n(c, "¿Qué es Docker?").json()
    assert body["response_type"] == "no_evidence" and body["references"] == [] and body["grounded"] is False
    assert body["mode"] == "LIVE" and body["provider"] == "nvidia-kimi" and "no se explica" in body["response"]


def test_a_fabricated_quotation_falls_back_to_demo_with_the_notice(db_path, fake_assets):
    reply = {"answer": "El profesor dijo «n8n garantiza que todos los procesos se vuelven gratis para siempre» [S1].", "used_segments": ["S1"], "answerable": True}
    fake = FakeKimi(lambda r, p: json_response(nv_completion(reply)))
    with TestClient(live_app(db_path, fake_assets, fake)) as c:
        body = chat_n8n(c, "¿Qué es n8n?").json()
    assert body["fallback_used"] is True and body["mode"] == "DEMO" and "llm_malformed_response" in body["provider_notice"]


# ============================================================ léxico temático y resumen local
def test_a_topic_needs_an_anchor_and_enough_distinct_terms():
    assert "docker" in score_window("instalamos docker y revisamos el contenedor con la imagen del compose")
    assert "docker" not in score_window("docker docker docker docker docker docker")             # una sola palabra repetida no basta
    assert "rpa" not in score_window("el robot hace tareas en la pantalla del escritorio")        # sin ancla (rpa / power automate / uipath)
    assert score_window("") == {}


def test_titles_only_exist_where_the_words_exist():
    texts = ["conversamos de fútbol y del clima hoy"] * 3 + ["instalamos docker, el contenedor y la imagen con compose"]
    titles = title_windows(texts)
    assert titles[0] is None and titles[1] is None and titles[3] is not None and "Docker" in titles[3][0]


def test_a_topic_present_in_almost_every_block_is_discounted():
    common = "el workflow tiene un trigger y un nodo, la acción conecta cada flujo"
    texts = [common] * 9 + ["la api recibe un webhook con json y la url del endpoint en el request"]
    titles = title_windows(texts)
    assert all(t is None for t in titles[:9]) and "APIs" in titles[9][0]
    assert MAX_TOPIC_BLOCK_RATIO < 1


def test_lexicon_is_well_formed():
    keys = [t.key for t in LEXICON]
    assert len(keys) == len(set(keys)) >= 15
    for t in LEXICON:
        assert t.title and t.short and t.terms and t.anchors and t.min_terms >= 2
        assert all(a == " ".join(tokenize(a)) for a in t.anchors), t.key                       # anclas ya normalizadas
        assert {a for a in t.anchors} <= {term for term, _ in t.terms} | set(t.anchors)


def long_lesson(texts_by_block, per_block=15, seconds=40.0):          # 15 x 40 s = 10 min: un bloque por ventana de análisis
    texts = [t for t in texts_by_block for _ in range(per_block)]
    lesson = lesson_of(texts, seconds=seconds)
    return lesson, build_chapters(lesson, [], analyze_segments(lesson, []))


GARDEN = "Hoy hablamos de jardinería, macetas, tierra y riego durante la tarde de verano."
RPA = "El rpa y power automate usan robots que operan la pantalla y el escritorio de una aplicación antigua."
MESSAGING = "Para whatsapp y telegram enviamos un mensaje al chat con un bot, y el mensaje llega a la persona."
SETUP = "Instalamos docker desktop, abrimos la terminal, powershell y git, y reiniciamos la máquina para instalar wsl en windows."
NOCODE = "El no code no requiere conocimiento técnico y el low code permite escribir poco código cuando hace falta."


def test_contiguous_blocks_with_the_same_topic_merge_but_never_beyond_thirty_minutes():
    lesson, chapters = long_lesson([NOCODE, SETUP, SETUP, SETUP, SETUP, SETUP, SETUP, RPA, MESSAGING, GARDEN])   # 6 de 10 bloques: tema sostenido
    setup = [ch for ch in chapters if "setup" in ch.topic_keys]
    assert len(setup) == 2                                                        # 6 bloques de 10 min -> 2 capítulos de 30 min
    assert all(ch.end_seconds - ch.start_seconds <= 1800 for ch in setup)
    assert setup[0].title.startswith("Preparación del entorno") and "(continuación)" not in setup[0].title
    assert all("(continuación)" in ch.title for ch in setup[1:])
    assert chapters[0].start_seconds == lesson.segments[0].start_seconds and chapters[-1].end_seconds == lesson.duration_seconds
    assert [ch.title for ch in chapters if "nocode" in ch.topic_keys][0].startswith("No-code y low-code")


def test_chapter_times_and_sentences_come_from_the_real_segments():
    lesson, chapters = long_lesson([NOCODE, SETUP, SETUP, RPA, MESSAGING, GARDEN])
    starts = {s.start_seconds for s in lesson.segments}
    ends = {s.end_seconds for s in lesson.segments}
    assert {ch.start_seconds for ch in chapters} <= starts and {ch.end_seconds for ch in chapters} <= ends
    flat = " ".join(" ".join(s.text.split()) for s in lesson.segments)
    for ch in chapters:
        assert ch.description.rstrip("…") in flat                                               # frase TEXTUAL (nada parafraseado)
        assert ch.reference_segments and ch.start_seconds <= ch.focus_seconds < ch.end_seconds


def test_without_lexicon_evidence_the_old_title_is_kept():
    lesson, chapters = long_lesson([GARDEN] * 3)
    assert all(not ch.topic_keys for ch in chapters) and all(ch.title for ch in chapters)


def test_representative_sentence_prefers_complete_sentences_with_topic_terms():
    segs = [seg(0, "Sí. El low code permite escribir poco código cuando hace falta algo más específico en el proceso. Entonces bueno ya vamos.")]
    sentence, source = representative_sentence(segs, ("nocode",))
    assert sentence.startswith("El low code permite") and source is segs[0]
    assert representative_sentence([seg(0, "Hola chicos buenas tardes a todos.")], ("nocode",)) is None


def test_the_overview_is_an_index_with_minutes_and_says_titles_are_labels():
    lesson, chapters = long_lesson([NOCODE, SETUP, SETUP, RPA, MESSAGING, GARDEN])
    text = build_overview(lesson, chapters, key_concepts(lesson, analyze_segments(lesson, [])))
    assert "No-code y low-code (00:00)" in text and "Preparación del entorno" in text
    second = next(ch for ch in chapters if "setup" in ch.topic_keys)
    assert f"({format_timestamp(second.start_seconds)})" in text and "no son frases del docente" in text


def test_the_pipeline_version_changes_only_regenerate_when_it_must(make_app, monkeypatch, db_path):
    from conftest import rows
    sql = "/api/courses/sql-demo/lessons/clase-01-introduccion-sql/summary"
    with TestClient(make_app()) as c:
        first = c.get(sql).json()
        again = c.get(sql).json()
    assert again["cache"]["cached"] is True and first["cache"]["pipeline_version"] == summary_service.PIPELINE_VERSION == "summary/1.2"
    assert len(rows(db_path, "SELECT 1 FROM lesson_summaries")) == 1
    monkeypatch.setattr(summary_service, "PIPELINE_VERSION", "summary/9.9")                        # el algoritmo cambió
    with TestClient(make_app()) as c:
        regenerated = c.get(sql).json()
    assert regenerated["cache"]["cached"] is False and regenerated["cache"]["pipeline_version"] == "summary/9.9"
    stored = rows(db_path, "SELECT pipeline_version FROM lesson_summaries")
    assert stored == [{"pipeline_version": "summary/9.9"}]                       # la versión anterior se reemplaza: queda UNA fila vigente


def test_the_summary_endpoint_labels_the_local_summary_and_never_calls_a_model(n8n, n8n_app):
    body = n8n.get(f"/api/courses/{N8N_COURSE}/lessons/{N8N_LESSON}/summary").json()
    assert body["engine"] == "extractive" and body["mode"] == "DEMO" and body["generated_by"] == "extractive-heuristic/1.1"
    assert "Resumen local por temas" in body["method_note"] and "sin modelo de lenguaje" in body["method_note"]
    assert n8n_app.state.summaries.generations == {"extractive": 1}


# ============================================================ plan del resumen con Kimi (solo lectura)
def test_the_kimi_summary_plan_reads_only_and_makes_no_calls(fake_assets, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("plan_kimi_summary", ROOT / "ai-service" / "scripts" / "plan_kimi_summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ASSETS", fake_assets)
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "Solicitudes previstas" in out and "no se promete ninguna duración" in out and "NO lo hagas durante la demo" in out
    real = load_real_lesson(fake_assets)
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    from app.services.lesson_service import LessonService
    chapters, _, _ = LessonService(kb, None).extractive_skeleton(real.lesson)
    calls = module.plan(real.lesson, chapters)
    assert len(calls) >= len(chapters) + 1 and calls[-1][0].startswith("Resumen general")
    assert all(n <= 20 for _, n, _ in calls[:-1])


# ============================================================ evaluador del tutor
CASES_FILE = ROOT / "ai-service" / "eval" / "tutor_eval_cases.json"
REQUIRED = ["¿Qué es n8n y para qué sirve?", "¿Cuál es la diferencia entre no-code y low-code?", "¿Qué procesos conviene automatizar?",
            "¿Qué diferencia existe entre un workflow y un RPA?", "¿Para qué sirve Docker en esta clase?"]


def test_the_eval_set_covers_the_required_questions_and_behaviors():
    data = load_cases(CASES_FILE)
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids)) >= 10
    questions = [c.get("question") or c["turns"][0] for c in data["cases"]]
    assert all(q in questions for q in REQUIRED)
    behaviors = {c["expect"].get("behavior") for c in data["cases"]}
    assert {"grounded", "out_of_scope", "no_evidence"} <= behaviors
    assert any(len(c.get("turns", [])) >= 2 for c in data["cases"])                        # hay un caso con seguimiento contextual
    for c in data["cases"]:
        lo, hi = c["expect"].get("primary_start", (0, 1))
        assert lo < hi


def synthetic_cases(lesson):
    def window(second):
        s = next(s for s in lesson.segments if s.start_seconds <= second < s.end_seconds)
        return [s.start_seconds - 1, s.start_seconds + 1]
    return [
        {"id": "n8n-que-es", "question": "¿Qué es n8n y para qué sirve?",
         "expect": {"behavior": "grounded", "guardian": "relevant", "max_citations": 1, "primary_start": window(120)}},
        {"id": "n8n-larga", "question": "¿Qué es n8n y para qué sirve? Explícamelo de forma sencilla según lo enseñado por el profesor",
         "expect": {"behavior": "grounded", "guardian": "relevant", "max_citations": 1, "primary_start": window(120), "same_primary_as": "n8n-que-es"}},
        {"id": "docker", "question": "¿Para qué sirve Docker en esta clase?",
         "expect": {"behavior": "grounded", "guardian": "relevant", "primary_start": window(540)}},
        {"id": "ausente", "question": "¿Qué es Kubernetes?", "expect": {"behavior": "no_evidence", "no_citations": True}},
        {"id": "fuera", "question": "¿Cómo se prepara una pizza napolitana?",
         "expect": {"behavior": "out_of_scope", "guardian": "not_relevant", "no_citations": True}},
        {"id": "seguimiento", "turns": ["¿Qué es n8n y para qué sirve?", "Dame otro ejemplo"],
         "expect": {"behavior": "grounded", "different_from_previous": True}},
    ]


@pytest.mark.parametrize("mode", ["demo", "simulated"])
def test_the_evaluator_passes_on_the_synthetic_class_in_both_modes(fake_assets, tmp_path, mode):
    real = load_real_lesson(fake_assets)
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    rig = build_service(kb, real.lesson, tmp_path / f"{mode}.db", mode, privacy=real.privacy)
    with blocked_network():
        results = run_suite(rig, synthetic_cases(real.lesson))
    failed = [(r.case_id, c.name, c.detail) for r in results for c in r.checks if not c.ok]
    assert failed == []
    report = render_report(results, mode, real.lesson.title)
    if mode == "simulated":
        assert "SIMULADO" in report and "NO es Kimi" in report and rig.transport.calls >= 3
        assert all("SIMULADO" in t.text for r in results for t in r.turns if t.provider == "nvidia-kimi")
        assert not any(t.mode == "LIVE" and t.provider != "nvidia-kimi" for r in results for t in r.turns)
    else:
        assert "DEMO (proveedor determinista real)" in report and "SIMULADO" not in report


def test_the_evaluator_detects_a_wrong_expectation(fake_assets, tmp_path):
    real = load_real_lesson(fake_assets)
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    rig = build_service(kb, real.lesson, tmp_path / "x.db", "demo", privacy=real.privacy)
    bad = [{"id": "mal", "question": "¿Qué es n8n y para qué sirve?", "expect": {"behavior": "grounded", "primary_start": [9000, 9100], "max_citations": 0}}]
    (result,) = run_suite(rig, bad)
    assert not result.passed and {c.name for c in result.checks if not c.ok} >= {"cita principal en la ventana esperada", "pocas citas"}


def test_the_evaluator_blocks_sockets_and_restores_them():
    import socket
    original = socket.socket.connect
    with blocked_network():
        with pytest.raises(RuntimeError, match="red bloqueada"):
            socket.create_connection(("example.com", 80), timeout=1)
    assert socket.socket.connect is original


@needs_real
@pytest.mark.parametrize("mode", ["demo", "simulated"])
def test_the_real_class_eval_set_passes_in_both_modes(tmp_path, mode):
    real = load_real_lesson(REAL_ASSETS)
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    rig = build_service(kb, real.lesson, tmp_path / f"{mode}.db", mode, privacy=real.privacy)
    with blocked_network():
        results = run_suite(rig, load_cases(CASES_FILE)["cases"])
    failed = [(r.case_id, c.name, c.detail) for r in results for c in r.checks if not c.ok]
    assert failed == [] and len(results) >= 10


@needs_real
def test_the_real_class_chapters_are_supported_by_the_transcript():
    real = load_real_lesson(REAL_ASSETS)
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    lesson = real.lesson
    chapters = build_chapters(lesson, [], analyze_segments(lesson, []))
    assert 10 <= len(chapters) <= 20 and chapters[0].start_seconds == lesson.segments[0].start_seconds
    assert chapters[-1].end_seconds == lesson.duration_seconds
    assert all(a.end_seconds <= b.start_seconds + 25 for a, b in zip(chapters, chapters[1:]))
    n = len(chapters)
    for i, ch in enumerate(chapters):
        if ch.topic_keys:
            text = " ".join(s.text for s in ch.segments)
            found = set(score_window(text, "start" if ch.start_seconds == chapters[0].start_seconds else "middle")) | set(
                score_window(text, "end"))
            assert set(ch.topic_keys) <= found, (ch.chapter_id, ch.topic_keys)                  # cada título tiene evidencia en su propio texto
    assert sum(1 for ch in chapters if ch.topic_keys) >= n - 3                                   # casi todos tienen título temático
    assert "automatización" in chapters[0].title.lower()


# ============================================================ pantalla
def test_the_screen_distinguishes_the_local_summary_from_the_kimi_one():
    js = (ROOT / "frontend" / "demo-n8n.js").read_text(encoding="utf-8")
    assert "Resumen local por temas · sin IA generativa" in js and "Resumen generado con Kimi" in js
    assert re.search(r'byKimi \? " live" : " warn"', js)
