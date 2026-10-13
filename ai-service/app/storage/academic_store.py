"""
Almacén académico: telemetría del asistente, intentos de quiz, micro-preguntas e idempotencia.

Comparte archivo SQLite y transacciones con `ConversationStore` (composición, sin duplicar la conexión).
Todas las consultas están parametrizadas. Los métodos `*_tx` reciben una conexión ya dentro de una
transacción para que el servicio agrupe varias escrituras de forma atómica.
"""
import json
import sqlite3
import uuid
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional

from app.storage.conversation_store import ConversationStore, utc_now


def new_id() -> str:
    return uuid.uuid4().hex


class AcademicStore:
    def __init__(self, base: ConversationStore):
        self.base = base

    def transaction(self):
        return self.base.transaction()

    def connection(self):
        return self.base.connection()

    # ================================================================= telemetría
    def insert_event_tx(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        mode: str,
        payload: Dict[str, Any],
        student_id: Optional[str] = None,
        course_id: Optional[str] = None,
        lesson_id: Optional[str] = None,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Optional[str]:
        """Inserta un evento; devuelve su id, o None si la clave idempotente ya existía (no se duplica)."""
        event_id = new_id()
        cur = conn.execute(
            "INSERT OR IGNORE INTO telemetry_events (event_id, event_type, student_id, course_id, lesson_id, "
            "session_id, mode, payload, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, event_type, student_id, course_id, lesson_id, session_id, mode,
             json.dumps(payload, ensure_ascii=False, sort_keys=True), created_at or utc_now(), idempotency_key),
        )
        return event_id if cur.rowcount == 1 else None

    def record_event(self, event_type: str, mode: str, payload: Dict[str, Any], **ids) -> Optional[str]:
        with self.transaction() as conn:
            return self.insert_event_tx(conn, event_type, mode, payload, **ids)

    def query_events(
        self,
        course_id: Optional[str] = None,
        lesson_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        before: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses, params = self._event_filters(course_id, lesson_id, event_type, since, until)
        if before:
            clauses.append("(created_at < ?)")
            params.append(before)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with closing(self.connection()) as conn:
            rows = conn.execute(
                f"SELECT * FROM telemetry_events {where} ORDER BY created_at DESC, event_id DESC LIMIT ?",  # noqa: S608 (cláusulas fijas)
                (*params, limit),
            ).fetchall()
        return [self._event_row(r) for r in rows]

    def events_for_summary(self, course_id=None, lesson_id=None, since=None, until=None) -> List[Dict[str, Any]]:
        clauses, params = self._event_filters(course_id, lesson_id, None, since, until)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with closing(self.connection()) as conn:
            rows = conn.execute(f"SELECT * FROM telemetry_events {where}", params).fetchall()  # noqa: S608
        return [self._event_row(r) for r in rows]

    @staticmethod
    def _event_filters(course_id, lesson_id, event_type, since, until):
        clauses, params = [], []
        for column, value in (("course_id", course_id), ("lesson_id", lesson_id), ("event_type", event_type)):
            if value:
                clauses.append(f"{column} = ?")   # nombres de columna fijos
                params.append(value)
        if since:
            clauses.append("created_at >= ?"); params.append(since)
        if until:
            clauses.append("created_at <= ?"); params.append(until)
        return clauses, params

    @staticmethod
    def _event_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        return data

    # ===================================================================== quiz
    def count_attempts_tx(self, conn, student_id, course_id, lesson_id) -> int:
        return conn.execute(
            "SELECT COUNT(1) FROM quiz_attempts WHERE student_id = ? AND course_id = ? AND lesson_id = ?",
            (student_id, course_id, lesson_id),
        ).fetchone()[0]

    def find_open_attempt_tx(self, conn, student_id, course_id, lesson_id, now_iso) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            "SELECT * FROM quiz_attempts WHERE student_id = ? AND course_id = ? AND lesson_id = ? "
            "AND status = 'started' AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
            (student_id, course_id, lesson_id, now_iso),
        ).fetchone()
        return dict(row) if row else None

    def insert_attempt_tx(self, conn, attempt: Dict[str, Any], answers: Iterable[Dict[str, Any]]) -> None:
        conn.execute(
            "INSERT INTO quiz_attempts (attempt_id, student_id, course_id, lesson_id, status, total_questions, "
            "created_at, expires_at, pass_threshold_percent) VALUES (?, ?, ?, ?, 'started', ?, ?, ?, ?)",
            (attempt["attempt_id"], attempt["student_id"], attempt["course_id"], attempt["lesson_id"],
             attempt["total_questions"], attempt["created_at"], attempt["expires_at"], attempt["pass_threshold_percent"]),
        )
        for a in answers:
            conn.execute(
                "INSERT INTO quiz_answers (attempt_id, position, question_id, option_order, correct_option_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (attempt["attempt_id"], a["position"], a["question_id"], json.dumps(a["option_order"]), a["correct_option_id"]),
            )

    def get_attempt(self, attempt_id: str) -> Optional[Dict[str, Any]]:
        with closing(self.connection()) as conn:
            row = conn.execute("SELECT * FROM quiz_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return dict(row) if row else None

    def get_attempt_answers(self, attempt_id: str) -> List[Dict[str, Any]]:
        with closing(self.connection()) as conn:
            rows = conn.execute(
                "SELECT * FROM quiz_answers WHERE attempt_id = ? ORDER BY position", (attempt_id,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["option_order"] = json.loads(d["option_order"])
            out.append(d)
        return out

    def complete_attempt(
        self,
        attempt_id: str,
        graded: List[Dict[str, Any]],
        correct_count: int,
        score_percent: float,
        passed: bool,
        submitted_at: str,
        events: List[Dict[str, Any]],
    ) -> bool:
        """
        Cierra el intento de forma atómica y SOLO si seguía abierto (`status='started'`).
        Devuelve False si otro envío ya lo cerró: así dos envíos concurrentes no pueden alterar la nota.
        """
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE quiz_attempts SET status = 'completed', submitted_at = ?, correct_count = ?, "
                "score_percent = ?, passed = ? WHERE attempt_id = ? AND status = 'started'",
                (submitted_at, correct_count, score_percent, int(passed), attempt_id),
            )
            if cur.rowcount != 1:
                return False
            for g in graded:
                conn.execute(
                    "UPDATE quiz_answers SET selected_option_id = ?, is_correct = ? WHERE attempt_id = ? AND position = ?",
                    (g["selected_option_id"], int(g["is_correct"]), attempt_id, g["position"]),
                )
            for ev in events:
                self.insert_event_tx(conn, **ev)
            return True

    def has_completed_quiz(self, student_id: str, course_id: str) -> bool:
        with closing(self.connection()) as conn:
            return conn.execute(
                "SELECT 1 FROM quiz_attempts WHERE student_id = ? AND course_id = ? AND status = 'completed' LIMIT 1",
                (student_id, course_id),
            ).fetchone() is not None

    # ============================================================ micro-pregunta
    def get_micro_response(self, student_id, course_id, question_id, period_index) -> Optional[Dict[str, Any]]:
        with closing(self.connection()) as conn:
            row = conn.execute(
                "SELECT * FROM micro_responses WHERE student_id = ? AND course_id = ? AND question_id = ? AND period_index = ?",
                (student_id, course_id, question_id, period_index),
            ).fetchone()
        return dict(row) if row else None

    def micro_period_handled(self, student_id: str, course_id: str, period_index: int) -> bool:
        """¿Ya respondió u omitió CUALQUIER micro-pregunta de este periodo? (no se insiste dentro del periodo)."""
        with closing(self.connection()) as conn:
            return conn.execute(
                "SELECT 1 FROM micro_responses WHERE student_id = ? AND course_id = ? AND period_index = ? LIMIT 1",
                (student_id, course_id, period_index),
            ).fetchone() is not None

    def insert_micro_response(self, record: Dict[str, Any], event: Optional[Dict[str, Any]]) -> bool:
        """Guarda la respuesta (y su evento) atómicamente. False si ya existía para ese estudiante/periodo/pregunta."""
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO micro_responses (response_id, student_id, course_id, question_id, period_index, "
                "status, answer_text, category, classification_method, confidence, matched_cues, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["response_id"], record["student_id"], record["course_id"], record["question_id"],
                 record["period_index"], record["status"], record.get("answer_text"), record.get("category"),
                 record.get("classification_method"), record.get("confidence"),
                 json.dumps(record.get("matched_cues", []), ensure_ascii=False), record["created_at"]),
            )
            if cur.rowcount != 1:
                return False
            if event is not None:
                self.insert_event_tx(conn, **event)
            return True

    # =============================================================== resúmenes
    def get_summary(self, course_id, lesson_id, fingerprint, engine, pipeline_version) -> Optional[Dict[str, Any]]:
        with closing(self.connection()) as conn:
            row = conn.execute(
                "SELECT * FROM lesson_summaries WHERE course_id = ? AND lesson_id = ? AND transcript_fingerprint = ? "
                "AND engine = ? AND pipeline_version = ?",
                (course_id, lesson_id, fingerprint, engine, pipeline_version),
            ).fetchone()
        return dict(row) if row else None

    def save_summary(self, course_id, lesson_id, fingerprint, engine, pipeline_version, payload, meta) -> None:
        """Guarda el resumen vigente y elimina los de huellas anteriores del mismo motor (invalidación)."""
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM lesson_summaries WHERE course_id = ? AND lesson_id = ? AND engine = ? "
                "AND (transcript_fingerprint <> ? OR pipeline_version <> ?)",
                (course_id, lesson_id, engine, fingerprint, pipeline_version),
            )
            conn.execute(
                "INSERT OR REPLACE INTO lesson_summaries (course_id, lesson_id, transcript_fingerprint, engine, "
                "pipeline_version, payload, generator_meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (course_id, lesson_id, fingerprint, engine, pipeline_version,
                 json.dumps(payload, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), utc_now()),
            )

    def list_summaries(self, course_id: str, lesson_id: str) -> List[Dict[str, Any]]:
        with closing(self.connection()) as conn:
            rows = conn.execute(
                "SELECT course_id, lesson_id, transcript_fingerprint, engine, pipeline_version, created_at "
                "FROM lesson_summaries WHERE course_id = ? AND lesson_id = ?", (course_id, lesson_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # ============================================================== idempotencia
    def get_idempotent(self, scope_key: str) -> Optional[Dict[str, Any]]:
        with closing(self.connection()) as conn:
            row = conn.execute("SELECT * FROM idempotent_responses WHERE scope_key = ?", (scope_key,)).fetchone()
        return dict(row) if row else None

    def save_idempotent(self, scope_key: str, request_hash: str, status_code: int, body: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotent_responses (scope_key, request_hash, status_code, body, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (scope_key, request_hash, status_code, body, utc_now()),
            )
