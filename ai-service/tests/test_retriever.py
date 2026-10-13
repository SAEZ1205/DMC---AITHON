import pytest

from app.knowledge.retriever import KnowledgeRetriever

from conftest import LESSON_1, LESSON_2


def topic_ids(r, query, course="sql-demo"):
    return [t["id"] for t in r.retrieve(course, query)]


def segs(r, query, **kw):
    return r.retrieve_segments("sql-demo", query, **kw)


OUT_OF_SCOPE = [
    "¿Qué es una API REST?",
    "¿Quién ganó el mundial de fútbol?",
    "receta de ceviche peruano",
    "datos de fútbol",              # una palabra suelta coincide pero el resto no
    "cómo conecto Python con SQL",  # la mayoría de la consulta no está en el material
    "¿qué?",                        # solo palabras vacías
    "",
]


# ======================================================== recuperación de temas
@pytest.mark.parametrize("query, expected_first", [
    ("¿Cómo filtro filas con WHERE?", "where"),
    ("ordenar resultados de mayor a menor", "order_by"),
    ("¿Cómo puedo contar cuántos registros hay?", "count"),
    ("explícame el LEFT JOIN", "left_join"),
    ("quiero agrupar por categoría", "group_by"),
])
def test_topic_retrieval_finds_the_right_topic(retriever, query, expected_first):
    assert topic_ids(retriever, query)[0] == expected_first


@pytest.mark.parametrize("query, expected", [
    ("proyección de columnas", "select"),
    ("PROYECCION DE COLUMNAS", "select"),
    ("agregación por categoría", "group_by"),
    ("AGREGACION por categoria", "group_by"),
    ("¿Qué condiciones puedo aplicar?", "where"),
    ("relacionar tablas con clave foránea", "inner_join"),
])
def test_topic_retrieval_ignores_accents_and_case(retriever, query, expected):
    assert topic_ids(retriever, query)[0] == expected


def test_substring_of_a_keyword_does_not_match(retriever):
    """Regresión: la keyword "on" (inner_join) coincidía dentro de "condiciones", "consulta", etc."""
    ids = topic_ids(retriever, "consulta con condiciones de contexto")
    assert "inner_join" not in ids


def test_unrelated_topic_is_not_returned_as_secondary_result(retriever):
    assert topic_ids(retriever, "cláusula WHERE") == ["where"]


def test_partial_single_word_overlap_is_not_enough_for_a_topic(retriever):
    # "clave" aparece en una keyword de inner_join, pero la consulta no trata de joins
    assert topic_ids(retriever, "¿Qué es una clave primaria?") == []


@pytest.mark.parametrize("query", OUT_OF_SCOPE)
def test_out_of_scope_queries_return_no_topics(retriever, query):
    assert retriever.retrieve("sql-demo", query) == []


def test_topic_citations_only_reference_retrieved_topics(retriever):
    chunks = retriever.retrieve("sql-demo", "¿Cómo uso ORDER BY?")
    cites = retriever.build_citations(chunks)
    assert [c.topic for c in cites] == ["order_by"]
    assert cites[0].reference == "ref:sql-demo/order-by"
    assert cites[0].timestamp is None  # las citas de fichas no llevan minuto
    assert retriever.build_citations(retriever.retrieve("sql-demo", "API REST")) == []


# ============================================= recuperación de segmentos / clases
def test_segment_retrieval_finds_the_right_moment(retriever):
    m = segs(retriever, "¿Cómo ordeno los resultados con ORDER BY?")[0]
    assert (m.segment.lesson_id, m.segment.timestamp_range) == (LESSON_1, "06:22–07:20")
    assert "ORDER BY" in m.segment.text


def test_segment_retrieval_with_accents_and_meta_words(retriever):
    a = segs(retriever, "¿En qué minuto de la clase explican la clave primaria?")
    b = segs(retriever, "clave primaria")
    assert a and [x.segment.segment_id for x in a] == [x.segment.segment_id for x in b]
    assert {(x.segment.lesson_id, x.segment.segment_id) for x in a} >= {(LESSON_1, 2)}


def test_accent_insensitive_segment_match(retriever):
    with_accent = segs(retriever, "agregación")
    without = segs(retriever, "AGREGACION")
    assert with_accent and [m.segment.segment_id for m in with_accent] == [m.segment.segment_id for m in without]
    assert with_accent[0].segment.lesson_id == LESSON_2


@pytest.mark.parametrize("query", OUT_OF_SCOPE)
def test_out_of_scope_queries_return_no_segments(retriever, query):
    assert segs(retriever, query) == []


def test_results_are_ranked_and_limited(retriever):
    res = segs(retriever, "tabla", top_k=2)
    assert len(res) <= 2
    assert [m.score for m in res] == sorted((m.score for m in res), reverse=True)


def test_matched_terms_are_reported(retriever):
    m = segs(retriever, "LEFT JOIN")[0]
    assert set(m.matched_terms) == {"left", "join"}


# ------------------------------------------------------------------ aislamiento
def test_lesson_scope_returns_only_that_lesson(retriever):
    res = segs(retriever, "WHERE filtrar filas", lesson_id=LESSON_1)
    assert res and {m.segment.lesson_id for m in res} == {LESSON_1}
    res2 = segs(retriever, "WHERE filtrar filas", lesson_id=LESSON_2)
    assert res2 and {m.segment.lesson_id for m in res2} == {LESSON_2}


def test_lesson_scope_does_not_leak_content_from_other_lessons(retriever):
    # LEFT JOIN solo se enseña en la clase 2
    assert segs(retriever, "LEFT JOIN", lesson_id=LESSON_1) == []
    assert segs(retriever, "LEFT JOIN", lesson_id=LESSON_2) != []
    # LIKE y comodín solo en la clase 1
    assert segs(retriever, "operador LIKE comodín", lesson_id=LESSON_2) == []
    assert segs(retriever, "operador LIKE comodín", lesson_id=LESSON_1) != []


def test_unscoped_search_covers_all_lessons_of_the_course_and_labels_them(retriever):
    res = segs(retriever, "clave primaria", top_k=5)
    assert {m.segment.lesson_id for m in res} == {LESSON_1, LESSON_2}


def test_unknown_lesson_or_course_returns_nothing(retriever):
    assert segs(retriever, "SELECT", lesson_id="clase-99") == []
    assert retriever.retrieve_segments("no-existe", "SELECT") == []
    assert retriever.retrieve("no-existe", "SELECT") == []


def test_courses_are_isolated_even_with_same_lesson_ids(two_course_tree, tmp_path):
    r = KnowledgeRetriever(courses_dir=tmp_path / "x", transcripts_dir=two_course_tree)
    a = r.retrieve_segments("curso-a", "webhook", top_k=10)
    b = r.retrieve_segments("curso-b", "webhook", top_k=10)
    assert {m.segment.course_id for m in a} == {"curso-a"}
    assert {m.segment.course_id for m in b} == {"curso-b"}
    assert len(a) == 2 and len(b) == 1
    # clase-01 existe en ambos cursos: el alcance por clase sigue limitado al curso
    only_a = r.retrieve_segments("curso-a", "webhook", lesson_id="clase-01", top_k=10)
    assert [m.segment.course_id for m in only_a] == ["curso-a"]
    assert only_a[0].segment.timestamp_range == "00:00–00:30"
    # contenido de A no aparece al consultar B y viceversa
    assert r.retrieve_segments("curso-b", "autenticación endpoint") == []
    assert r.retrieve_segments("curso-a", "otro curso distinto") == []


def test_topics_are_isolated_by_course(two_course_tree, tmp_path):
    r = KnowledgeRetriever(transcripts_dir=two_course_tree)
    assert r.retrieve("curso-a", "SELECT") == []  # "SELECT" pertenece a sql-demo
    assert r.retrieve("sql-demo", "SELECT")[0]["id"] == "select"


# ----------------------------------------------------------------------- citas
def test_segment_citations_carry_exact_timestamps(retriever):
    matches = segs(retriever, "ORDER BY", lesson_id=LESSON_1)
    cite = retriever.build_segment_citations(matches)[0]
    assert cite.lesson_id == LESSON_1
    assert (cite.start_seconds, cite.end_seconds) == (382.0, 440.0)
    assert cite.timestamp == "06:22–07:20"
    assert cite.reference == f"ref:sql-demo/{LESSON_1}#t=382"
    assert "06:22–07:20" in cite.title and "Clase 1" in cite.title


def test_citation_timestamps_match_the_cited_text(retriever):
    for query in ["LEFT JOIN", "HAVING", "clave foránea", "alias AS", "paréntesis AND OR"]:
        for m, cite in zip(segs(retriever, query), retriever.build_segment_citations(segs(retriever, query))):
            lesson = retriever.get_lesson("sql-demo", cite.lesson_id)
            seg = lesson.segments[m.segment.segment_id]
            assert seg.text == m.segment.text
            assert (seg.start_seconds, seg.end_seconds) == (cite.start_seconds, cite.end_seconds)


def test_no_citations_when_nothing_relevant(retriever):
    assert retriever.build_segment_citations(segs(retriever, "API REST")) == []


def test_having_is_cited_at_the_right_minute(retriever):
    m = segs(retriever, "HAVING")[0]
    assert m.segment.lesson_id == LESSON_2
    assert m.segment.timestamp_range == "11:30–12:40"   # segundos 690–760
    assert m.segment.minute_ref == "11:30"
