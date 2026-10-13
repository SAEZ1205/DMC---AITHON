"""
Persistencia local de sesiones y mensajes en SQLite.

Notas de diseño
  * Todas las consultas están parametrizadas.
  * Cada operación abre y cierra su propia conexión (seguro con los hilos de FastAPI).
  * `record_turn` guarda mensaje del estudiante + respuesta del tutor + actualización de
    la sesión en UNA transacción: o se guarda el turno completo o nada.
  * Se trabaja con identificadores SINTÉTICOS de estudiante y datos ficticios; no se
    guardan nombres, correos ni credenciales.
"""
import json
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from app.storage.academic_schema import ACADEMIC_SCHEMA

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    student_id    TEXT NOT NULL,
    course_id     TEXT NOT NULL,
    lesson_id     TEXT,
    current_topic TEXT,
    current_query TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('student', 'tutor')),
    content    TEXT NOT NULL,
    intent     TEXT,
    topic      TEXT,
    citations  TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    provider   TEXT,
    fallback_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, message_id);
CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    student_id: str
    course_id: str
    lesson_id: Optional[str]
    current_topic: Optional[str]
    current_query: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MessageRecord:
    message_id: int
    session_id: str
    role: str
    content: str
    intent: Optional[str]
    topic: Optional[str]
    citations: List[Dict[str, Any]]
    created_at: str
    provider: Optional[str] = None
    fallback_reason: Optional[str] = None


class ConversationStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._schema_ready = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ conexión
    def _open(self) -> sqlite3.Connection:
        self._ensure_schema()
        return self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)  # autocommit; tx explícitas
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn:
                conn.executescript(_SCHEMA + ACADEMIC_SCHEMA)
                self._migrate(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._schema_ready = True

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """v1 -> v2: columnas de auditoría del proveedor (bases creadas en la Etapa 2)."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        for column in ("provider", "fallback_reason"):
            if column not in columns:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")  # nombre fijo, no entrada de usuario

    def initialize(self) -> None:
        """Crea la base y las tablas si no existen (idempotente)."""
        self._ensure_schema()

    def connection(self):
        """Conexión de lectura (autocommit) con el esquema garantizado. Úsese con `closing(...)`."""
        return self._open()

    def transaction(self):
        """Transacción de escritura (BEGIN IMMEDIATE/COMMIT/ROLLBACK) para los almacenes académicos."""
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._open()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # ------------------------------------------------------------- sesiones
    def create_session(self, student_id: str, course_id: str, lesson_id: Optional[str]) -> SessionRecord:
        now = utc_now()
        session_id = self.new_session_id()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, student_id, course_id, lesson_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, student_id, course_id, lesson_id, now, now),
            )
        return SessionRecord(session_id, student_id, course_id, lesson_id, None, None, now, now)

    @staticmethod
    def new_session_id() -> str:
        return uuid.uuid4().hex

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with closing(self._open()) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return SessionRecord(**dict(row)) if row else None

    # ------------------------------------------------------------- mensajes
    def get_recent_messages(self, session_id: str, limit: int) -> List[MessageRecord]:
        """Los últimos `limit` mensajes de la sesión, del más antiguo al más reciente."""
        with closing(self._open()) as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY message_id DESC LIMIT ?",
                (session_id, max(limit, 0)),
            ).fetchall()
        return [self._message(r) for r in reversed(rows)]

    def get_all_messages(self, session_id: str) -> List[MessageRecord]:
        with closing(self._open()) as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY message_id", (session_id,)
            ).fetchall()
        return [self._message(r) for r in rows]

    def count_turns(self, session_id: str) -> int:
        """Número de mensajes del estudiante ya guardados en la sesión."""
        with closing(self._open()) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'student'", (session_id,)
            ).fetchone()[0]

    def get_cited_segments(self, session_id: str) -> Set[Tuple[str, float]]:
        """(lesson_id, start_seconds) de todos los segmentos ya citados al estudiante en la sesión."""
        with closing(self._open()) as conn:
            rows = conn.execute(
                "SELECT citations FROM messages WHERE session_id = ? AND role = 'tutor'", (session_id,)
            ).fetchall()
        cited: Set[Tuple[str, float]] = set()
        for row in rows:
            for c in json.loads(row["citations"]):
                if c.get("lesson_id") is not None and c.get("start_seconds") is not None:
                    cited.add((c["lesson_id"], float(c["start_seconds"])))
        return cited

    def record_turn(
        self,
        session_id: str,
        student_message: str,
        student_intent: str,
        tutor_message: str,
        tutor_intent: str,
        topic: Optional[str],
        citations: List[Dict[str, Any]],
        update_focus: bool,
        focus_topic: Optional[str] = None,
        focus_query: Optional[str] = None,
        new_session: Optional[Tuple[str, str, Optional[str]]] = None,
        provider: Optional[str] = None,
        fallback_reason: Optional[str] = None,
    ) -> None:
        """
        Guarda un turno completo de forma atómica. Si `update_focus` es True, la sesión
        recuerda `focus_topic`/`focus_query` para interpretar mensajes de seguimiento.
        `new_session=(student_id, course_id, lesson_id)` crea antes la sesión `session_id`
        dentro de la misma transacción (no quedan sesiones vacías si algo falla).
        """
        now = utc_now()
        with self._transaction() as conn:
            if new_session is not None:
                student_id, course_id, lesson_id = new_session
                conn.execute(
                    "INSERT INTO sessions (session_id, student_id, course_id, lesson_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, student_id, course_id, lesson_id, now, now),
                )
            insert = (
                "INSERT INTO messages (session_id, role, content, intent, topic, citations, created_at, "
                "provider, fallback_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            conn.execute(insert, (session_id, "student", student_message, student_intent, topic, "[]", now, None, None))
            conn.execute(
                insert,
                (session_id, "tutor", tutor_message, tutor_intent, topic,
                 json.dumps(citations, ensure_ascii=False), now, provider, fallback_reason),
            )
            if update_focus:
                conn.execute(
                    "UPDATE sessions SET current_topic = ?, current_query = ?, updated_at = ? WHERE session_id = ?",
                    (focus_topic, focus_query, now, session_id),
                )
            else:
                conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))

    # -------------------------------------------------------------- estado
    def stats(self) -> Dict[str, int]:
        """Contadores y comprobación de que la base responde (lanza si hay un fallo)."""
        with closing(self._open()) as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"sessions": sessions, "messages": messages}

    @staticmethod
    def _message(row: sqlite3.Row) -> MessageRecord:
        data = dict(row)
        data["citations"] = json.loads(data["citations"])
        return MessageRecord(**data)
