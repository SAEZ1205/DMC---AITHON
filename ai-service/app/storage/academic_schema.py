"""
Tablas de la Etapa 4 (quizzes, micro-preguntas, telemetría del asistente e idempotencia).

Se crean con `CREATE TABLE IF NOT EXISTS` junto al esquema de conversaciones, de modo que una base
de la Etapa 3 (v2) se actualiza a v3 sin perder datos.

Privacidad: solo identificadores SINTÉTICOS de estudiante y los campos mínimos necesarios. El texto
libre de una micro-pregunta se guarda aparte de la categoría interpretada.
"""

ACADEMIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS quiz_attempts (
    attempt_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    course_id  TEXT NOT NULL,
    lesson_id  TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('started', 'completed')),
    total_questions INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    submitted_at TEXT,
    correct_count INTEGER,
    score_percent REAL,
    passed INTEGER,
    pass_threshold_percent REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_student ON quiz_attempts(student_id, course_id, lesson_id);

CREATE TABLE IF NOT EXISTS quiz_answers (
    attempt_id TEXT NOT NULL REFERENCES quiz_attempts(attempt_id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    option_order TEXT NOT NULL,            -- JSON: índices originales de las alternativas, ya barajadas
    correct_option_id TEXT NOT NULL,       -- solo en el servidor; nunca se entrega antes del envío
    selected_option_id TEXT,
    is_correct INTEGER,
    PRIMARY KEY (attempt_id, position)
);

CREATE TABLE IF NOT EXISTS micro_responses (
    response_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    course_id  TEXT NOT NULL,
    question_id TEXT NOT NULL,
    period_index INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('answered', 'skipped')),
    answer_text TEXT,                      -- lo que EXPRESÓ el estudiante
    category TEXT,                         -- lo que el sistema INTERPRETÓ (regla), no un diagnóstico
    classification_method TEXT,
    confidence TEXT,
    matched_cues TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE (student_id, course_id, question_id, period_index)
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id   TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    student_id TEXT,
    course_id  TEXT,
    lesson_id  TEXT,
    session_id TEXT,
    mode       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    idempotency_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_idem ON telemetry_events(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_telemetry_type ON telemetry_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_course ON telemetry_events(course_id, lesson_id);

CREATE TABLE IF NOT EXISTS lesson_summaries (
    course_id  TEXT NOT NULL,
    lesson_id  TEXT NOT NULL,
    transcript_fingerprint TEXT NOT NULL,   -- huella del contenido de la transcripción
    engine     TEXT NOT NULL CHECK (engine IN ('extractive', 'kimi')),
    pipeline_version TEXT NOT NULL,
    payload    TEXT NOT NULL,               -- resumen estructurado (JSON), sin texto personal adicional
    generator_meta TEXT NOT NULL DEFAULT '{}',   -- modelo, nº de llamadas, tokens (solo contadores)
    created_at TEXT NOT NULL,
    PRIMARY KEY (course_id, lesson_id, transcript_fingerprint, engine, pipeline_version)
);

CREATE TABLE IF NOT EXISTS idempotent_responses (
    scope_key  TEXT PRIMARY KEY,           -- endpoint:student:clave
    request_hash TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
