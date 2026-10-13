# Contrato de telemetría del asistente (para el dashboard)

> Datos **sintéticos de demostración**. Este servicio solo cuenta lo que ocurre dentro del asistente. **No** calcula riesgo ni
> tasas de deserción, **no** mide asistencia institucional y **no** aplica reglas de certificación: eso corresponde a otro módulo.

Documentación interactiva: `GET /docs` (OpenAPI en `/openapi.json`). Todos los errores usan
`{"error": {"code", "message", "details"}}`.

## 1. Endpoints de consulta

| Endpoint | Acceso | Para qué |
|---|---|---|
| `GET /api/telemetry/catalog` | abierto | Catálogo cerrado de eventos, su payload y el aviso que debe ver el estudiante |
| `GET /api/telemetry/summary` | abierto (solo agregados, sin identificadores) | Tarjetas y gráficos del dashboard |
| `GET /api/telemetry/events` | **token** `X-Telemetry-Token` | Eventos individuales con seudónimo de estudiante |

### `GET /api/telemetry/summary`
Parámetros opcionales: `course_id`, `lesson_id`, `since`, `until` (ISO-8601; `Z` o zona horaria; sin zona = UTC).

```jsonc
{
  "generated_at": "2026-10-13T02:35:41+00:00",
  "filters": {"course_id": null, "lesson_id": null, "since": null, "until": null},
  "total_events": 8,
  "distinct_students": 1,                       // solo un conteo; nunca ids
  "events_by_type": [{"key": "chat_question", "count": 1}, ...],   // siempre los 8 tipos, en orden fijo
  "top_topics": [{"key": "LEFT JOIN", "count": 1}],                // temas consultados en el chat
  "topics_with_difficulty_signals": [{"key": "Clave foránea", "count": 1}],
  "most_retrieved_lessons": [{"key": "clase-02-uniones-y-agrupacion", "count": 1}],
  "quiz": {"started": 1, "completed": 1, "average_score_percent": 75.0, "passed": 1, "pass_rate_percent": 100.0},
  "missed_content_requests": 1,
  "summary_requests": 1,
  "micro_question_categories": [{"key": "disponibilidad_tiempo", "count": 1}],
  "mode_breakdown": [{"key": "DEMO", "count": 8}],
  "data_scope": "demo_synthetic",
  "is_demo_metrics": true,
  "methodology_notice": "Conteos de actividad registrada por el asistente (DEMO). No son una probabilidad de deserción, ..."
}
```
- `topics_with_difficulty_signals` **no es** un diagnóstico: son temas con señales observables (el estudiante pidió repetir la
  explicación, o falló una pregunta del quiz).
- `quiz.passed` usa el umbral **DEMO** configurable (`QUIZ_PASS_THRESHOLD_PERCENT`); no es una regla institucional.

### `GET /api/telemetry/events`
Requiere la cabecera `X-Telemetry-Token`. **Está deshabilitado (403 `telemetry_events_disabled`) si el servidor no define
`TELEMETRY_API_TOKEN`**; con token ausente o incorrecto responde 401 `telemetry_unauthorized`.
Parámetros: `course_id`, `lesson_id`, `event_type` (del catálogo), `since`, `until`, `before`, `limit` (1-500, por defecto 100).
Orden: más recientes primero. Paginación: si `next_before` no es `null`, pásalo como `before` para la página siguiente.

```jsonc
{
  "events": [{
    "event_id": "9b1f…", "event_type": "quiz_completed",
    "student_ref": "est_283816cc3e5b",            // seudónimo estable (HMAC con TELEMETRY_PSEUDONYM_SALT); nunca el id original
    "course_id": "sql-demo", "lesson_id": "clase-02-uniones-y-agrupacion", "session_id": null,
    "mode": "DEMO", "timestamp": "2026-10-13T02:35:40.812345+00:00",
    "payload": {"attempt_id": "fb18…", "total_questions": 4, "correct_count": 3, "score_percent": 75.0, "passed": true}
  }],
  "count": 1, "limit": 100, "next_before": null,
  "data_scope": "demo_synthetic", "privacy_notice": "Datos SINTÉTICOS de demostración…"
}
```

## 2. Catálogo cerrado de eventos

Cada evento tiene: `event_id`, `event_type`, `student_ref` (seudónimo o `null`), `course_id`, `lesson_id`, `session_id`
(`null` si no aplica), `mode` (`DEMO` | `LIVE`: quién generó la respuesta asociada), `timestamp` (UTC) y un `payload` estructurado.
No se registra el **texto** de los mensajes del estudiante dentro de los eventos.

| `event_type` | Cuándo se genera | `payload` |
|---|---|---|
| `chat_question` | Cada mensaje al asistente (`POST /api/chat`) | `user_intent`, `response_type`, `grounded`, `topic`, `topic_id`, `message_length` |
| `content_retrieved` | El chat respondió con segmentos de la transcripción | `segments[{lesson_id, start_seconds, end_seconds}]`, `source` |
| `topic_difficulty` | (a) el estudiante pidió repetir una explicación (`repeat_request`); (b) respondió mal una pregunta del quiz (`quiz_incorrect`) | `topic`, `signal`, y en quiz `question_id`, `attempt_id` |
| `lesson_summary_requested` | `GET …/summary` | `engine`, `section_count`, `concept_count` |
| `missed_content_requested` | `POST /api/lessons/missed-content` | `interval_count`, `total_missed_seconds`, `segment_count`, `concepts` |
| `quiz_started` | Se crea un intento (retomar uno abierto **no** genera otro evento) | `attempt_id`, `num_questions`, `resumed` |
| `quiz_completed` | Se envía y califica un intento | `attempt_id`, `total_questions`, `correct_count`, `score_percent`, `passed` |
| `micro_question_answered` | El estudiante **respondió** la micro-pregunta (omitirla no genera evento) | `question_id`, `period_index`, `category`, `confidence` |

Categorías de `micro_question_answered.category` (interpretadas por reglas conservadoras; indican el **tema**, no un estado
psicológico): `disponibilidad_tiempo`, `trabajo`, `dificultad_contenido`, `ritmo_academico`, `satisfaccion`, `otro`,
`sin_informacion_suficiente`.

## 3. Idempotencia
Los `POST` aceptan la cabecera opcional `Idempotency-Key` (8-64 caracteres `A-Za-z0-9_.:-`). Un reintento con la **misma clave y el
mismo cuerpo** devuelve la respuesta original (cabecera `Idempotent-Replay: true`) sin duplicar mensajes, intentos ni eventos.
La misma clave con otro cuerpo → 422 `idempotency_key_reuse`. La clave se acota por endpoint y por estudiante.

## 4. Privacidad y límites (léase antes de integrar)
- Hoy el login del frontend es demostrativo y **no hay autenticación de estudiantes**: `student_id` es un identificador sintético, no una credencial.
  No publique estos endpoints con datos de estudiantes reales sin añadir autenticación y autorización.
- `summary` solo devuelve agregados. `events` devuelve seudónimos y exige token.
- Con muy pocos estudiantes, un agregado puede identificar a alguien; con datos reales habrá que aplicar umbrales mínimos de agregación.
- El estudiante es informado de que su actividad genera métricas: las respuestas del chat, resumen, "lo que te perdiste" y quiz incluyen `telemetry_notice`.
- Las métricas **no** son una probabilidad de deserción ni un registro de asistencia.
