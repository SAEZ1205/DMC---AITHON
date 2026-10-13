"""Almacenamiento SQLite, análisis de intención y proveedor DEMO a nivel de unidad."""
import json
import sqlite3

import pytest

from app.llm import get_provider
from app.llm.base import ChatTurn, FollowUpMode, ResponseKind, TutorContext
from app.llm.demo_provider import MAX_QUOTE_CHARS, NO_EVIDENCE_TEXT, DemoProvider, shorten
from app.services.intents import analyze_message
from app.storage import ConversationStore

from conftest import LESSON_1


@pytest.fixture
def store(db_path):
    return ConversationStore(db_path)


def turn(store, sid, n="hola", citations=None, **kw):
    store.record_turn(sid, n, "question", "respuesta", "grounded", None, citations or [], update_focus=False, **kw)


# ------------------------------------------------------------------- store
def test_creating_the_store_does_not_touch_the_disk_until_used(db_path):
    ConversationStore(db_path)
    assert not db_path.exists()


def test_schema_is_created_lazily_and_is_idempotent(store, db_path):
    store.initialize()
    store.initialize()
    assert db_path.exists()
    tables = {r[0] for r in sqlite3.connect(db_path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sessions", "messages"} <= tables
    assert sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0] == 4  # v4: + resúmenes persistidos


def test_session_roundtrip(store):
    s = store.create_session("est-1", "sql-demo", LESSON_1)
    got = store.get_session(s.session_id)
    assert (got.student_id, got.course_id, got.lesson_id, got.current_topic) == ("est-1", "sql-demo", LESSON_1, None)
    assert store.get_session("no-existe") is None


def test_messages_are_scoped_to_their_session_and_ordered(store):
    a = store.create_session("a", "c", None)
    b = store.create_session("b", "c", None)
    turn(store, a.session_id, "uno")
    turn(store, b.session_id, "secreto de b")
    turn(store, a.session_id, "dos")
    msgs = store.get_all_messages(a.session_id)
    assert [m.content for m in msgs if m.role == "student"] == ["uno", "dos"]
    assert all("secreto" not in m.content for m in msgs)
    assert [m.message_id for m in msgs] == sorted(m.message_id for m in msgs)
    assert store.count_turns(a.session_id) == 2 and store.count_turns(b.session_id) == 1


def test_recent_messages_returns_the_latest_in_chronological_order(store):
    s = store.create_session("a", "c", None)
    for i in range(5):
        turn(store, s.session_id, f"m{i}")
    recent = store.get_recent_messages(s.session_id, 3)
    assert len(recent) == 3 and recent[-1].role == "tutor"
    assert [m.role for m in recent] == ["tutor", "student", "tutor"]   # los 3 últimos, en orden cronológico
    assert [m.content for m in recent if m.role == "student"] == ["m4"]
    assert store.get_recent_messages(s.session_id, 0) == []


def test_record_turn_is_atomic_when_serialization_fails(store, db_path):
    s = store.create_session("a", "c", None)
    with pytest.raises(TypeError):
        turn(store, s.session_id, "no debe guardarse", citations=[{"x": object()}])
    assert store.get_all_messages(s.session_id) == []


def test_new_session_is_created_inside_the_same_transaction(store):
    sid = store.new_session_id()
    with pytest.raises(TypeError):
        store.record_turn(sid, "m", "question", "r", "grounded", None, [{"x": object()}],
                          update_focus=False, new_session=("a", "c", None))
    assert store.get_session(sid) is None  # no quedan sesiones huérfanas
    store.record_turn(sid, "m", "question", "r", "grounded", None, [], update_focus=True,
                      focus_topic="where", focus_query="WHERE", new_session=("a", "c", LESSON_1))
    got = store.get_session(sid)
    assert (got.current_topic, got.current_query) == ("where", "WHERE")


def test_messages_for_unknown_sessions_are_rejected_by_foreign_key(store):
    with pytest.raises(sqlite3.IntegrityError):
        turn(store, "sesion-fantasma")
    assert store.stats() == {"sessions": 0, "messages": 0}


def test_cited_segments_are_collected_from_tutor_messages_only(store):
    s = store.create_session("a", "c", LESSON_1)
    cite = {"lesson_id": LESSON_1, "start_seconds": 382.0}
    turn(store, s.session_id, citations=[cite])
    assert store.get_cited_segments(s.session_id) == {(LESSON_1, 382.0)}
    assert store.get_cited_segments("otra") == set()


# ----------------------------------------------------------------- intents
@pytest.mark.parametrize("message, intent, query, mode", [
    ("Explícame ORDER BY.", "question", "ORDER BY", FollowUpMode.NONE),
    ("¿Cómo funciona ORDER BY?", "question", "ORDER BY", FollowUpMode.NONE),
    ("¿Qué es una clave primaria?", "question", "clave primaria", FollowUpMode.NONE),
    ("¿Puedes darme otro ejemplo?", "follow_up", "", FollowUpMode.MORE),
    ("dame más detalle por favor", "follow_up", "", FollowUpMode.MORE),
    ("no entendí", "follow_up", "", FollowUpMode.REPEAT),
    ("¿y eso?", "follow_up", "", FollowUpMode.REPEAT),
    ("otro ejemplo de WHERE", "question", "WHERE", FollowUpMode.MORE),
    ("ok pero no entendí el WHERE", "question", "WHERE", FollowUpMode.REPEAT),
    ("Explícame ORDER BY otra vez", "question", "ORDER BY", FollowUpMode.REPEAT),
    ("explícalo de nuevo", "follow_up", "", FollowUpMode.REPEAT),
    ("Hola", "greeting", "", FollowUpMode.NONE),
    ("buenos días", "greeting", "", FollowUpMode.NONE),
    ("Gracias, chau", "closing", "", FollowUpMode.NONE),
    ("hola, gracias", "closing", "", FollowUpMode.NONE),
    ("gracias, ¿qué es GROUP BY?", "question", "GROUP BY", FollowUpMode.NONE),
])
def test_message_analysis(message, intent, query, mode):
    a = analyze_message(message)
    assert (a.user_intent, a.query, a.follow_up_mode) == (intent, query, mode)


def test_deictic_references_are_detected():
    assert analyze_message("¿qué pasa si no uso eso?").refers_to_previous is True
    assert analyze_message("Explícame ORDER BY").refers_to_previous is False


# ---------------------------------------------------------- proveedor DEMO
def ctx(retriever, query, kind=ResponseKind.GROUNDED, **kw):
    evidence = retriever.retrieve_segments("sql-demo", query, lesson_id=LESSON_1, top_k=2)
    base = dict(kind=kind, student_message=query, course_name="SQL", lesson_title="Clase 1",
                topic_label="ORDER BY", evidence=evidence)
    base.update(kw)
    return TutorContext(**base)


def test_demo_provider_is_deterministic(retriever):
    p = DemoProvider()
    c = ctx(retriever, "ORDER BY")
    assert p.generate(c) == p.generate(c)
    assert p.mode == "DEMO" and p.name == "demo"


def test_grounded_reply_quotes_only_the_given_evidence(retriever):
    reply = DemoProvider().generate(ctx(retriever, "ORDER BY"))
    assert "06:22–07:20" in reply.text and reply.follow_up_question in reply.text
    assert "LEFT JOIN" not in reply.text  # nada fuera de la evidencia


def test_grounded_without_evidence_degrades_to_no_evidence(retriever):
    reply = DemoProvider().generate(ctx(retriever, "ORDER BY", evidence=[]))
    assert reply.text == NO_EVIDENCE_TEXT and reply.follow_up_question is None


@pytest.mark.parametrize("kind", [ResponseKind.NO_EVIDENCE, ResponseKind.EXHAUSTED, ResponseKind.NEEDS_CONTEXT,
                                  ResponseKind.GREETING, ResponseKind.CLOSING])
def test_non_grounded_replies_never_mention_minutes(retriever, kind):
    reply = DemoProvider().generate(ctx(retriever, "ORDER BY", kind=kind, evidence=[]))
    assert reply.text and "min " not in reply.text and reply.follow_up_question is None


def test_openings_vary_by_follow_up_mode(retriever):
    p = DemoProvider()
    texts = {m: p.generate(ctx(retriever, "ORDER BY", follow_up_mode=m)).text.split("\n")[0] for m in FollowUpMode}
    assert len(set(texts.values())) == 3


def test_shorten_preserves_the_original_prefix():
    long = "Primera frase completa. " * 40
    out = shorten(long)
    assert len(out) <= MAX_QUOTE_CHARS + 1 and long.startswith(out) and out.endswith(".")
    assert shorten("texto corto") == "texto corto"
    no_stops = "palabra " * 100
    cut = shorten(no_stops)
    assert cut.endswith("…") and no_stops.startswith(cut[:-1].rstrip())


def test_get_provider_without_credentials_stays_in_demo(caplog):
    # Desde la Etapa 3 'kimi' existe, pero sin clave (y sin autorización) se mantiene DEMO de forma explícita.
    assert get_provider("kimi").mode == "DEMO"
    assert "sin LLM_API_KEY" in caplog.text
    assert get_provider("demo").name == "demo"


def test_v1_database_from_stage_2_is_migrated_in_place_without_losing_data(db_path):
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, course_id TEXT NOT NULL,
            lesson_id TEXT, current_topic TEXT, current_query TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE messages (message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('student', 'tutor')), content TEXT NOT NULL, intent TEXT, topic TEXT,
            citations TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
        INSERT INTO sessions VALUES ('s1', 'est-1', 'sql-demo', NULL, NULL, NULL, 't', 't');
        INSERT INTO messages (session_id, role, content, created_at) VALUES ('s1', 'student', 'mensaje viejo', 't');
        PRAGMA user_version = 1;
        """
    )
    legacy.commit(); legacy.close()

    store = ConversationStore(db_path)
    assert store.stats() == {"sessions": 1, "messages": 1}
    old = store.get_all_messages("s1")[0]
    assert (old.content, old.provider, old.fallback_reason) == ("mensaje viejo", None, None)
    turn(store, "s1", "mensaje nuevo", provider="kimi:kimi-k2.6", fallback_reason=None)
    assert store.get_all_messages("s1")[-1].provider == "kimi:kimi-k2.6"
    assert sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0] == 4  # migrado hasta la versión vigente
