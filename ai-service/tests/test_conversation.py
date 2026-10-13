"""Memoria de sesión, aislamiento entre estudiantes/clases/cursos y persistencia."""
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.knowledge.retriever import KnowledgeRetriever
from app.llm.demo_provider import DemoProvider
from app.main import create_app
from app.services.tutor_service import HISTORY_MESSAGE_MAX_CHARS, HISTORY_WINDOW_MESSAGES

from conftest import LESSON_1, LESSON_2, STUDENT_A, STUDENT_B, post_chat


def refs(body):
    return [(r["lesson_id"], r["timestamp"]) for r in body["references"]]


# ----------------------------------------------------------------- memoria
def test_session_id_is_generated_and_reused(client):
    first = post_chat(client, "Explícame ORDER BY").json()
    assert re.fullmatch(r"[0-9a-f]{32}", first["session_id"])
    second = post_chat(client, "otro ejemplo", session_id=first["session_id"]).json()
    assert second["session_id"] == first["session_id"]
    other = post_chat(client, "Explícame ORDER BY").json()
    assert other["session_id"] != first["session_id"]


def test_follow_up_message_uses_the_previous_topic(client):
    first = post_chat(client, "Explícame ORDER BY.").json()
    assert refs(first) == [(LESSON_1, "06:22–07:20")]

    follow = post_chat(client, "¿Puedes darme otro ejemplo?", session_id=first["session_id"]).json()
    assert follow["topic_detected"] == "order_by"            # entendió que se refiere a ORDER BY
    assert follow["grounded"] is True
    assert refs(follow) == [(LESSON_1, "07:20–08:05")]       # otro fragmento, no el mismo
    assert "ORDER BY" in follow["response"]
    assert set(l for l, _ in refs(follow)) == {LESSON_1}     # nunca otra clase


def test_asking_for_more_when_everything_was_shown_is_honest(client):
    s = post_chat(client, "Explícame ORDER BY").json()["session_id"]
    post_chat(client, "otro ejemplo", session_id=s)
    last = post_chat(client, "otro ejemplo más", session_id=s).json()
    assert last["response_type"] == "exhausted" and last["references"] == []
    assert "Ya te mostré todos los fragmentos" in last["response"]


def test_confusion_repeats_the_same_fragment(client):
    s = post_chat(client, "Explícame WHERE").json()
    again = post_chat(client, "no entendí", session_id=s["session_id"]).json()
    assert refs(again) == refs(s) == [(LESSON_1, "04:25–05:30")]
    assert again["response"] != s["response"]  # reformulación (apertura y pregunta distintas)


def test_deictic_reference_resolves_to_the_current_topic(client):
    s = post_chat(client, "Explícame ORDER BY").json()["session_id"]
    body = post_chat(client, "¿qué pasa si no uso eso?", session_id=s).json()
    assert body["grounded"] is True and refs(body) == [(LESSON_1, "06:22–07:20")]


def test_follow_up_without_previous_topic_asks_for_context(client):
    body = post_chat(client, "¿Puedes darme otro ejemplo?").json()
    assert body["response_type"] == "needs_context" and body["references"] == []
    assert body["topic_detected"] is None


def test_failed_lookup_does_not_erase_the_current_topic(client):
    s = post_chat(client, "Explícame ORDER BY").json()["session_id"]
    assert post_chat(client, "¿Qué es una API REST?", session_id=s).json()["response_type"] == "no_evidence"
    follow = post_chat(client, "otro ejemplo", session_id=s).json()
    assert follow["topic_detected"] == "order_by" and follow["grounded"] is True


def test_topic_switches_with_a_new_question(client):
    s = post_chat(client, "Explícame ORDER BY").json()["session_id"]
    post_chat(client, "Explícame WHERE", session_id=s)
    follow = post_chat(client, "no entendí", session_id=s).json()
    assert follow["topic_detected"] == "where" and refs(follow) == [(LESSON_1, "04:25–05:30")]


def test_greeting_and_closing_never_cite(client):
    hello = post_chat(client, "Hola, buenas tardes").json()
    assert hello["response_type"] == "greeting" and hello["references"] == []
    assert "Clase 1" in hello["response"]
    bye = post_chat(client, "Muchas gracias, chau", session_id=hello["session_id"]).json()
    assert bye["response_type"] == "closing" and bye["references"] == [] and bye["grounded"] is False


def test_greeting_mixed_with_a_question_is_answered_as_a_question(client):
    body = post_chat(client, "Hola, ¿cómo funciona ORDER BY?").json()
    assert body["response_type"] == "grounded"


def test_session_keeps_its_lesson_when_later_requests_omit_it(client):
    s = post_chat(client, "Explícame ORDER BY").json()["session_id"]
    body = post_chat(client, "otro ejemplo", session_id=s, lesson_id=None).json()
    assert body["lesson_id"] == LESSON_1 and refs(body) == [(LESSON_1, "07:20–08:05")]


# ------------------------------------------------------------ aislamiento
def test_two_students_do_not_share_conversations_or_context(client):
    a = post_chat(client, "Explícame ORDER BY", student_id=STUDENT_A).json()
    b = post_chat(client, "otro ejemplo", student_id=STUDENT_B).json()
    assert b["session_id"] != a["session_id"]
    assert b["response_type"] == "needs_context"       # B no hereda el tema de A
    assert "ORDER BY" not in b["response"] and b["references"] == []


def test_foreign_session_cannot_be_reused_and_leaks_nothing(client, db_path):
    a = post_chat(client, "Mensaje privado de A sobre ORDER BY", student_id=STUDENT_A).json()
    before = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    resp = post_chat(client, "otro ejemplo", student_id=STUDENT_B, session_id=a["session_id"])
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "session_mismatch"
    assert "privado" not in resp.text and "ORDER BY" not in resp.text and a["response"] not in resp.text

    after = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert after == before  # el intento no escribió nada en la conversación de A
    # y A conserva su sesión intacta
    assert post_chat(client, "otro ejemplo", student_id=STUDENT_A, session_id=a["session_id"]).status_code == 200


def test_unknown_session_is_rejected_without_creating_it(client, db_path):
    resp = post_chat(client, "hola", session_id="0123456789abcdef0123456789abcdef")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "session_not_found"
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_session_cannot_be_moved_to_another_lesson(client):
    s = post_chat(client, "Explícame ORDER BY", lesson_id=LESSON_1).json()["session_id"]
    resp = post_chat(client, "Explícame HAVING", lesson_id=LESSON_2, session_id=s)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "session_mismatch"


def test_session_cannot_be_moved_from_course_wide_to_a_lesson(client):
    s = post_chat(client, "Explícame ORDER BY", lesson_id=None).json()["session_id"]
    assert post_chat(client, "otro ejemplo", lesson_id=LESSON_1, session_id=s).status_code == 403


def test_session_cannot_cross_courses(db_path, two_course_tree):
    r = KnowledgeRetriever(transcripts_dir=two_course_tree)
    with TestClient(create_app(db_path=db_path, retriever=r)) as c:
        a = post_chat(c, "webhook", course_id="curso-a", lesson_id="clase-01").json()
        assert a["grounded"] is True
        resp = post_chat(c, "webhook", course_id="curso-b", lesson_id="clase-01", session_id=a["session_id"])
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "session_mismatch"
        b = post_chat(c, "webhook", course_id="curso-b", lesson_id="clase-01").json()
        assert refs(b) == [("clase-01", "00:10–00:50")] and "otro curso" in b["response"]
        assert "aplicación" not in b["response"]  # texto del curso A ausente


def test_two_lessons_do_not_mix_content(client):
    in_class_2 = post_chat(client, "¿Cómo funciona WHERE?", lesson_id=LESSON_2).json()
    assert in_class_2["grounded"] is True
    assert {l for l, _ in refs(in_class_2)} == {LESSON_2}
    assert "calificacion mayor o igual a 14" not in in_class_2["response"]  # texto de la clase 1

    # LIKE solo se enseña en la clase 1
    assert post_chat(client, "¿Qué hace el operador LIKE?", lesson_id=LESSON_1).json()["grounded"] is True
    leak = post_chat(client, "¿Qué hace el operador LIKE?", lesson_id=LESSON_2).json()
    assert leak["response_type"] == "no_evidence" and leak["references"] == []
    assert "porcentaje" not in leak["response"]


def test_follow_up_never_crosses_into_another_lesson(client):
    s = post_chat(client, "Explícame los joins", lesson_id=LESSON_2).json()
    for _ in range(4):
        body = post_chat(client, "otro ejemplo", lesson_id=LESSON_2, session_id=s["session_id"]).json()
        assert {l for l, _ in refs(body)} <= {LESSON_2}


# ------------------------------------------------ las citas solo vienen de la evidencia
@pytest.mark.parametrize("lesson, question", [
    (LESSON_1, "¿Cómo funciona ORDER BY?"),
    (LESSON_1, "¿Qué es una clave primaria?"),
    (LESSON_1, "filtrar con WHERE"),
    (LESSON_2, "diferencia entre INNER JOIN y LEFT JOIN"),
    (LESSON_2, "HAVING"),
    (None, "clave primaria"),
])
def test_every_citation_is_a_real_retrieved_segment_quoted_in_the_answer(client, retriever, lesson, question):
    body = post_chat(client, question, lesson_id=lesson).json()
    assert body["grounded"] is True
    retrieved = {
        (m.segment.lesson_id, m.segment.start_seconds)
        for m in retriever.retrieve_segments("sql-demo", question, lesson_id=lesson, top_k=10)
    }
    for ref in body["references"]:
        assert (ref["lesson_id"], ref["start_seconds"]) in retrieved
        seg = next(s for s in retriever.get_lesson("sql-demo", ref["lesson_id"]).segments
                   if s.start_seconds == ref["start_seconds"])
        assert (seg.end_seconds, seg.timestamp_range) == (ref["end_seconds"], ref["timestamp"])
        assert seg.text[:40] in body["response"]
    # todo minuto mencionado en el texto está respaldado por una referencia
    mentioned = set(re.findall(r"min (\d+:\d\d–\d+:\d\d)", body["response"]))
    assert mentioned == {r["timestamp"] for r in body["references"]}


# ------------------------------------------------------------ historial acotado
class _SpyProvider(DemoProvider):
    def __init__(self):
        self.contexts = []

    def generate(self, context):
        self.contexts.append(context)
        return super().generate(context)


def test_history_given_to_the_provider_is_bounded(db_path, retriever):
    spy = _SpyProvider()
    with TestClient(create_app(db_path=db_path, retriever=retriever, provider=spy)) as c:
        s = post_chat(c, "Explícame ORDER BY").json()["session_id"]
        for i in range(8):
            post_chat(c, "otro ejemplo " + "x" * 900 if i == 0 else "no entendí", session_id=s)
    last = spy.contexts[-1]
    assert last.turn_number == 9
    assert len(last.history) == HISTORY_WINDOW_MESSAGES
    assert all(len(t.content) <= HISTORY_MESSAGE_MAX_CHARS for t in last.history)
    assert [t.role for t in last.history] == ["student", "tutor"] * (HISTORY_WINDOW_MESSAGES // 2)
    assert spy.contexts[0].history == []


# --------------------------------------------------------------- persistencia
def test_sessions_and_messages_persist_in_sqlite(client, db_path):
    first = post_chat(client, "Explícame ORDER BY.").json()
    post_chat(client, "¿Puedes darme otro ejemplo?", session_id=first["session_id"])

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    session = db.execute("SELECT * FROM sessions").fetchall()
    assert len(session) == 1
    s = session[0]
    assert (s["session_id"], s["student_id"], s["course_id"], s["lesson_id"]) == (
        first["session_id"], STUDENT_A, "sql-demo", LESSON_1)
    assert s["current_topic"] == "order_by" and s["current_query"] == "ORDER BY"
    assert s["created_at"] and s["updated_at"] >= s["created_at"]

    rows = db.execute("SELECT * FROM messages ORDER BY message_id").fetchall()
    assert [r["role"] for r in rows] == ["student", "tutor", "student", "tutor"]
    assert rows[0]["content"] == "Explícame ORDER BY."
    assert rows[1]["content"] == first["response"]
    assert rows[1]["intent"] == "grounded" and rows[1]["topic"] == "order_by"
    assert rows[0]["created_at"]
    assert json.loads(rows[1]["citations"])[0]["timestamp"] == "06:22–07:20"
    assert json.loads(rows[3]["citations"])[0]["timestamp"] == "07:20–08:05"
    assert json.loads(rows[0]["citations"]) == []  # los mensajes del estudiante no llevan citas


def test_conversation_survives_a_server_restart(db_path, retriever):
    with TestClient(create_app(db_path=db_path, retriever=retriever)) as c1:
        s = post_chat(c1, "Explícame ORDER BY").json()["session_id"]
    with TestClient(create_app(db_path=db_path, retriever=retriever)) as c2:   # "reinicio"
        body = post_chat(c2, "otro ejemplo", session_id=s).json()
        assert body["topic_detected"] == "order_by" and refs(body) == [(LESSON_1, "07:20–08:05")]
        assert c2.get("/health").json()["storage"]["messages"] == 4


def test_hostile_text_is_stored_verbatim_and_does_not_touch_the_schema(client, db_path):
    evil = "x'); DROP TABLE messages; --"
    assert post_chat(client, evil).status_code == 200
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT content FROM messages WHERE role='student'").fetchone()[0] == evil
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='messages'").fetchone()[0] == 1


def test_default_database_path_does_not_depend_on_cwd(tmp_path, monkeypatch):
    from app import config
    monkeypatch.chdir(tmp_path)
    resolved = config._resolve_sqlite_path("sqlite:///./data/rescuepath.db")
    assert resolved == (config.BASE_DIR / "data" / "rescuepath.db").resolve()
    assert config._resolve_sqlite_path("sqlite:///") == config.DATA_DIR / "rescuepath.db"
    assert config.settings.DATABASE_PATH.is_absolute()
