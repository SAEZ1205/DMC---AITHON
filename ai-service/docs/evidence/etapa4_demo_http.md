# Evidencia: demostración HTTP de la Etapa 4

Generada el 2026-10-13T02:35:40+00:00 con `scripts/demo_stage4_http.py` contra un servidor uvicorn local.
Estudiante FICTICIO: `demo-student-etapa4`. Modo DEMO, sin Kimi y sin llamadas facturables.
Eventos de telemetría observados: chat_question, content_retrieved, lesson_summary_requested, micro_question_answered, missed_content_requested, quiz_completed, quiz_started, topic_difficulty.

## 1. Estado del backend

`GET http://127.0.0.1:8000/health` → **200**

```json
{
  "status": "ok",
  "service": "dmc-rescuepath-ai",
  "version": "1.0.0-mvp",
  "mode": "DEMO",
  "llm_provider": "demo",
  "llm_provider_configured": "demo",
  "llm": {
    "active_provider": "demo",
    "configured_provider": "demo",
    "mode": "DEMO",
    "selection_reason": "LLM_PROVIDER=demo",
    "model": null,
    "live_authorized": false,
    "api_key_configured": false,
    "fallback_to_demo": false,
    "live_calls_used": null,
    "live_calls_limit": null
  },
  "storage": {
    "status": "ok",
    "engine": "sqlite",
    "database_file": "stage4-demo.db",
    "sessions": 0,
    "messages": 0,
    "error": null
  },
  "knowledge": {
    "courses": 1,
    "lessons": 2,
    "load_errors": 0
  },
  "timestamp": "2026-10-13T02:35:40.043287+00:00"
}
```

## 2. Capítulos

`GET http://127.0.0.1:8000/api/courses/sql-demo/lessons/clase-02-uniones-y-agrupacion/chapters` → **200**

```json
{
  "course_id": "sql-demo",
  "lesson_id": "clase-02-uniones-y-agrupacion",
  "lesson_title": "Clase 2 — Uniones y agrupación (DEMO)",
  "duration_seconds": 805.0,
  "chapters": [
    {
      "chapter_id": "cap-01",
      "title": "Introducción",
      "start_seconds": 0.0,
      "end_seconds": 140.0,
      "timestamp": "00:00–02:20",
      "description": "Repasamos rápido lo anterior y hoy nos enfocamos en dos temas: combinar varias tablas y resumir datos.",
      "segment_ids": [
        0,
        1
      ],
      "concepts": [],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=0",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 00:00–00:50",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 0.0,
          "end_seconds": 50.0,
          "timestamp": "00:00–00:50"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=50",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 00:50–02:20",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 50.0,
          "end_seconds": 140.0,
          "timestamp": "00:50–02:20"
        }
      ]
    },
    {
      "chapter_id": "cap-02",
      "title": "INNER JOIN — Intersección y Coincidencia Exacta",
      "start_seconds": 140.0,
      "end_seconds": 330.0,
      "timestamp": "02:20–05:30",
      "description": "INNER JOIN combina filas de dos tablas cuando hay coincidencia exacta.",
      "segment_ids": [
        2,
        3
      ],
      "concepts": [
        "INNER JOIN",
        "ON"
      ],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=140",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 02:20–03:55",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 140.0,
          "end_seconds": 235.0,
          "timestamp": "02:20–03:55"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=235",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 03:55–05:30",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 235.0,
          "end_seconds": 330.0,
          "timestamp": "03:55–05:30"
        }
      ]
    },
    {
      "chapter_id": "cap-03",
      "title": "LEFT JOIN — Conservación de la Tabla Izquierda",
      "start_seconds": 330.0,
      "end_seconds": 505.0,
      "timestamp": "05:30–08:25",
      "description": "Cuando necesitamos conservar todas las filas de la tabla izquierda usamos LEFT JOIN.",
      "segment_ids": [
        4,
        5
      ],
      "concepts": [
        "LEFT JOIN",
        "NULL",
        "WHERE",
        "IS NULL"
      ],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=330",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 05:30–07:10",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 330.0,
          "end_seconds": 430.0,
          "timestamp": "05:30–07:10"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=430",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 07:10–08:25",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 430.0,
          "end_seconds": 505.0,
          "timestamp": "07:10–08:25"
        }
      ]
    },
    {
      "chapter_id": "cap-04",
      "title": "GROUP BY — Agrupación y Resumen de Datos",
      "start_seconds": 505.0,
      "end_seconds": 805.0,
      "timestamp": "08:25–13:25",
      "description": "Con GROUP BY usamos funciones de agregado como COUNT y AVG.",
      "segment_ids": [
        6,
        7,
        8,
        9
      ],
      "concepts": [
        "GROUP BY",
        "COUNT",
        "AVG",
        "COUNT DISTINCT",
        "WHERE",
        "HAVING"
      ],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=505",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 08:25–10:00",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 505.0,
          "end_seconds": 600.0,
          "timestamp": "08:25–10:00"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=600",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 10:00–11:30",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 600.0,
          "end_seconds": 690.0,
          "timestamp": "10:00–11:30"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=690",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 11:30–12:40",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 690.0,
          "end_seconds": 760.0,
          "timestamp": "11:30–12:40"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=760",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 12:40–13:25",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 760.0,
          "end_seconds": 805.0,
          "timestamp": "12:40–13:25"
        }
      ]
    }
  ],
  "method_note": "Capítulos derivados de la transcripción: un capítulo nuevo empieza cuando un segmento introduce un tema aún no visto. Los tiempos son los de los segmentos; no se modifica ningún video.",
  "content_notice": "Modo DEMO: el contenido proviene de transcripciones FICTICIAS escritas para el MVP; no es contenido oficial de DMC Institute."
}
```

## 3. Resumen

`GET http://127.0.0.1:8000/api/courses/sql-demo/lessons/clase-02-uniones-y-agrupacion/summary?student_id=demo-student-etapa4` → **200**

```json
{
  "course_id": "sql-demo",
  "lesson_id": "clase-02-uniones-y-agrupacion",
  "lesson_title": "Clase 2 — Uniones y agrupación (DEMO)",
  "duration_seconds": 805.0,
  "duration_label": "13:25",
  "mode": "DEMO",
  "engine": "extractive",
  "provider": "demo",
  "overview": "«Clase 2 — Uniones y agrupación (DEMO)» dura 13:25 y está organizada en 4 capítulos: Introducción, INNER JOIN, LEFT JOIN, GROUP BY. Temas tratados: INNER JOIN, LEFT JOIN, WHERE, GROUP BY, COUNT.",
  "sections": [
    {
      "chapter_id": "cap-01",
      "title": "Introducción",
      "start_seconds": 0.0,
      "end_seconds": 140.0,
      "timestamp": "00:00–02:20",
      "key_point": "Repasamos rápido lo anterior y hoy nos enfocamos en dos temas: combinar varias tablas y resumir datos.",
      "segment_ids": [
        0,
        1
      ],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=0",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 00:00–00:50",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 0.0,
          "end_seconds": 50.0,
          "timestamp": "00:00–00:50"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=50",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 00:50–02:20",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 50.0,
          "end_seconds": 140.0,
          "timestamp": "00:50–02:20"
        }
      ]
    },
    {
      "chapter_id": "cap-02",
      "title": "INNER JOIN — Intersección y Coincidencia Exacta",
      "start_seconds": 140.0,
      "end_seconds": 330.0,
      "timestamp": "02:20–05:30",
      "key_point": "INNER JOIN combina filas de dos tablas cuando hay coincidencia exacta.",
      "segment_ids": [
        2,
        3
      ],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=140",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 02:20–03:55",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 140.0,
          "end_seconds": 235.0,
          "timestamp": "02:20–03:55"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=235",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 03:55–05:30",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 235.0,
          "end_seconds": 330.0,
          "timestamp": "03:55–05:30"
        }
      ]
    },
    {
      "chapter_id": "cap-03",
      "title": "LEFT JOIN — Conservación de la Tabla Izquierda",
      "start_seconds": 330.0,
      "end_seconds": 505.0,
      "timestamp": "05:30–08:25",
      "key_point": "Cuando necesitamos conservar todas las filas de la tabla izquierda usamos LEFT JOIN.",
      "segment_ids": [
        4,
        5
      ],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=330",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 05:30–07:10",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 330.0,
          "end_seconds": 430.0,
          "timestamp": "05:30–07:10"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=430",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 07:10–08:25",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 430.0,
          "end_seconds": 505.0,
          "timestamp": "07:10–08:25"
        }
      ]
    },
    {
      "chapter_id": "cap-04",
      "title": "GROUP BY — Agrupación y Resumen de Datos",
      "start_seconds": 505.0,
      "end_seconds": 805.0,
      "timestamp": "08:25–13:25",
      "key_point": "Con GROUP BY usamos funciones de agregado como COUNT y AVG.",
      "segment_ids": [
        6,
        7,
        8,
        9
      ],
      "references": [
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=505",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 08:25–10:00",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 505.0,
          "end_seconds": 600.0,
          "timestamp": "08:25–10:00"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=600",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 10:00–11:30",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 600.0,
          "end_seconds": 690.0,
          "timestamp": "10:00–11:30"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=690",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 11:30–12:40",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 690.0,
          "end_seconds": 760.0,
          "timestamp": "11:30–12:40"
        },
        {
          "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=760",
          "topic": "clase-02-uniones-y-agrupacion",
          "title": "Clase 2 — Uniones y agrupación (DEMO) — min 12:40–13:25",
          "lesson_id": "clase-02-uniones-y-agrupacion",
          "start_seconds": 760.0,
          "end_seconds": 805.0,
          "timestamp": "12:40–13:25"
        }
      ]
    }
  ],
  "key_concepts": [
    {
      "concept": "INNER JOIN",
      "kind": "tema",
      "first_seconds": 140.0,
      "first_minute": "02:20",
      "mentions": 2,
      "segment_ids": [
        2,
        3
      ]
    },
    {
      "concept": "LEFT JOIN",
      "kind": "tema",
      "first_seconds": 330.0,
      "first_minute": "05:30",
      "mentions": 2,
      "segment_ids": [
        4,
        5
      ]
    },
    {
      "concept": "WHERE",
      "kind": "tema",
      "first_seconds": 430.0,
      "first_minute": "07:10",
      "mentions": 2,
      "segment_ids": [
        5,
        8
      ]
    },
    {
      "concept": "GROUP BY",
      "kind": "tema",
      "first_seconds": 505.0,
      "first_minute": "08:25",
      "mentions": 2,
      "segment_ids": [
        6,
        7
      ]
    },
    {
      "concept": "COUNT",
      "kind": "tema",
      "first_seconds": 600.0,
      "first_minute": "10:00",
      "mentions": 1,
      "segment_ids": [
        7
      ]
    },
    {
      "concept": "ON",
      "kind": "termino",
      "first_seconds": 140.0,
      "first_minute": "02:20",
      "mentions": 1,
      "segment_ids": [
        2
      ]
    },
    {
      "concept": "NULL",
      "kind": "termino",
      "first_seconds": 330.0,
      "first_minute": "05:30",
      "mentions": 1,
      "segment_ids": [
        4
      ]
    },
    {
      "concept": "IS NULL",
      "kind": "termino",
      "first_seconds": 430.0,
      "first_minute": "07:10",
      "mentions": 1,
      "segment_ids": [
        5
      ]
    },
    {
      "concept": "AVG",
      "kind": "termino",
      "first_seconds": 600.0,
      "first_minute": "10:00",
      "mentions": 1,
      "segment_ids": [
        7
      ]
    },
    {
      "concept": "COUNT DISTINCT",
      "kind": "termino",
      "first_seconds": 600.0,
      "first_minute": "10:00",
      "mentions": 1,
      "segment_ids": [
        7
      ]
    },
    {
      "concept": "HAVING",
      "kind": "termino",
      "first_seconds": 690.0,
      "first_minute": "11:30",
      "mentions": 1,
      "segment_ids": [
        8
      ]
    }
  ],
  "segments_used": [
    {
      "segment_id": 0,
      "start_seconds": 0.0,
      "end_seconds": 50.0,
      "timestamp": "00:00–00:50"
    },
    {
      "segment_id": 1,
      "start_seconds": 50.0,
      "end_seconds": 140.0,
      "timestamp": "00:50–02:20"
    },
    {
      "segment_id": 2,
      "start_seconds": 140.0,
      "end_seconds": 235.0,
      "timestamp": "02:20–03:55"
    },
    {
      "segment_id": 3,
      "start_seconds": 235.0,
      "end_seconds": 330.0,
      "timestamp": "03:55–05:30"
    },
    {
      "segment_id": 4,
      "start_seconds": 330.0,
      "end_seconds": 430.0,
      "timestamp": "05:30–07:10"
    },
    {
      "segment_id": 5,
      "start_seconds": 430.0,
      "end_seconds": 505.0,
      "timestamp": "07:10–08:25"
    },
    {
      "segment_id": 6,
      "start_seconds": 505.0,
      "end_seconds": 600.0,
      "timestamp": "08:25–10:00"
    },
    {
      "segment_id": 7,
      "start_seconds": 600.0,
      "end_seconds": 690.0,
      "timestamp": "10:00–11:30"
    },
    {
      "segment_id": 8,
      "start_seconds": 690.0,
      "end_seconds": 760.0,
      "timestamp": "11:30–12:40"
    },
    {
      "segment_id": 9,
      "start_seconds": 760.0,
      "end_seconds": 805.0,
      "timestamp": "12:40–13:25"
    }
  ],
  "references": [
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=0",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 00:00–00:50",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 0.0,
      "end_seconds": 50.0,
      "timestamp": "00:00–00:50"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=50",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 00:50–02:20",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 50.0,
      "end_seconds": 140.0,
      "timestamp": "00:50–02:20"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=140",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 02:20–03:55",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 140.0,
      "end_seconds": 235.0,
      "timestamp": "02:20–03:55"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=235",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 03:55–05:30",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 235.0,
      "end_seconds": 330.0,
      "timestamp": "03:55–05:30"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=330",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 05:30–07:10",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 330.0,
      "end_seconds": 430.0,
      "timestamp": "05:30–07:10"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=430",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 07:10–08:25",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 430.0,
      "end_seconds": 505.0,
      "timestamp": "07:10–08:25"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=505",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 08:25–10:00",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 505.0,
      "end_seconds": 600.0,
      "timestamp": "08:25–10:00"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=600",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 10:00–11:30",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 600.0,
      "end_seconds": 690.0,
      "timestamp": "10:00–11:30"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=690",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 11:30–12:40",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 690.0,
      "end_seconds": 760.0,
      "timestamp": "11:30–12:40"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=760",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 12:40–13:25",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 760.0,
      "end_seconds": 805.0,
      "timestamp": "12:40–13:25"
    }
  ],
  "method_note": "Resumen extractivo y determinista: cada punto es una frase textual de la transcripción, con su minuto. No se ha medido cuánto tiempo ahorra y no sustituye ver la clase.",
  "content_notice": "Modo DEMO: el contenido proviene de transcripciones FICTICIAS escritas para el MVP; no es contenido oficial de DMC Institute.",
  "provider_notice": null,
  "telemetry_notice": "Este asistente registra tu actividad académica (por ejemplo, temas consultados y resultados de quizzes) para generar métricas de acompañamiento. No se guarda el contenido de tus mensajes dentro de las métricas."
}
```

## 4. Ausencia inválida (negativa)

`POST http://127.0.0.1:8000/api/lessons/missed-content` → **422** — se rechaza con 422

```json
{
  "error": {
    "code": "invalid_absence_intervals",
    "message": "Hay 1 intervalo(s) de ausencia inválido(s). Los segundos deben estar entre 0 y 805.",
    "details": [
      {
        "index": 0,
        "reason": "negativo",
        "message": "Los segundos no pueden ser negativos."
      }
    ]
  }
}
```

## 5. Lo que te perdiste (intervalos solapados y duplicados, SIMULADOS)

`POST http://127.0.0.1:8000/api/lessons/missed-content` → **200** — se normalizan a 180-420 sin repetir contenido

```json
{
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "lesson_id": "clase-02-uniones-y-agrupacion",
  "lesson_title": "Clase 2 — Uniones y agrupación (DEMO)",
  "mode": "DEMO",
  "normalized_intervals": [
    {
      "start_seconds": 180.0,
      "end_seconds": 420.0,
      "start_label": "03:00",
      "end_label": "07:00",
      "duration_seconds": 240.0
    }
  ],
  "normalization_notes": [
    "Se descartaron 1 intervalo(s) duplicado(s).",
    "Se fusionaron 1 intervalo(s) solapado(s) o contiguo(s) para no repetir contenido."
  ],
  "total_missed_seconds": 240.0,
  "missed_segments": [
    {
      "segment_id": 2,
      "start_seconds": 140.0,
      "end_seconds": 235.0,
      "timestamp": "02:20–03:55",
      "overlap_start_seconds": 180.0,
      "overlap_end_seconds": 235.0,
      "overlap_seconds": 55.0,
      "partially_missed": true,
      "text": "INNER JOIN combina filas de dos tablas cuando hay coincidencia exacta. Escribimos la condición después de la palabra ON, igualando la clave primaria con la clave foránea. Usar alias cortos como e y m hace la consulta más legible.",
      "concepts": [
        "INNER JOIN",
        "ON"
      ]
    },
    {
      "segment_id": 3,
      "start_seconds": 235.0,
      "end_seconds": 330.0,
      "timestamp": "03:55–05:30",
      "overlap_start_seconds": 235.0,
      "overlap_end_seconds": 330.0,
      "overlap_seconds": 95.0,
      "partially_missed": false,
      "text": "Ojo con esto: INNER JOIN solo devuelve las filas que tienen pareja en ambas tablas. Si un estudiante no tiene ninguna matrícula, simplemente no aparece en el resultado.",
      "concepts": [
        "INNER JOIN"
      ]
    },
    {
      "segment_id": 4,
      "start_seconds": 330.0,
      "end_seconds": 430.0,
      "timestamp": "05:30–07:10",
      "overlap_start_seconds": 330.0,
      "overlap_end_seconds": 420.0,
      "overlap_seconds": 90.0,
      "partially_missed": true,
      "text": "Cuando necesitamos conservar todas las filas de la tabla izquierda usamos LEFT JOIN. Si no existe coincidencia en la tabla derecha, esas columnas se rellenan con NULL.",
      "concepts": [
        "LEFT JOIN",
        "NULL"
      ]
    }
  ],
  "concepts_missed": [
    {
      "concept": "INNER JOIN",
      "first_seconds": 180.0,
      "first_minute": "03:00",
      "segment_ids": [
        2,
        3
      ]
    },
    {
      "concept": "ON",
      "first_seconds": 180.0,
      "first_minute": "03:00",
      "segment_ids": [
        2
      ]
    },
    {
      "concept": "LEFT JOIN",
      "first_seconds": 330.0,
      "first_minute": "05:30",
      "segment_ids": [
        4
      ]
    },
    {
      "concept": "NULL",
      "first_seconds": 330.0,
      "first_minute": "05:30",
      "segment_ids": [
        4
      ]
    }
  ],
  "explanation": "En el intervalo 03:00–07:00 de «Clase 2 — Uniones y agrupación (DEMO)» se explicó lo siguiente. Conceptos que pudiste perderte: INNER JOIN, ON, LEFT JOIN, NULL. Solo se perdió una parte de los fragmentos 02:20–03:55, 05:30–07:10.",
  "key_points": [
    {
      "timestamp": "02:20–03:55",
      "text": "Escribimos la condición después de la palabra ON, igualando la clave primaria con la clave foránea."
    },
    {
      "timestamp": "03:55–05:30",
      "text": "Ojo con esto: INNER JOIN solo devuelve las filas que tienen pareja en ambas tablas."
    },
    {
      "timestamp": "05:30–07:10",
      "text": "Si no existe coincidencia en la tabla derecha, esas columnas se rellenan con NULL."
    }
  ],
  "references": [
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=140",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 02:20–03:55",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 140.0,
      "end_seconds": 235.0,
      "timestamp": "02:20–03:55"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=235",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 03:55–05:30",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 235.0,
      "end_seconds": 330.0,
      "timestamp": "03:55–05:30"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=330",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 05:30–07:10",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 330.0,
      "end_seconds": 430.0,
      "timestamp": "05:30–07:10"
    }
  ],
  "attendance_notice": "Los intervalos de ausencia de este endpoint son SIMULADOS (ingresados en la solicitud). No provienen de Zoom ni de ningún registro real de asistencia, y esta respuesta no modifica ni calcula asistencia institucional.",
  "content_notice": "Modo DEMO: el contenido proviene de transcripciones FICTICIAS escritas para el MVP; no es contenido oficial de DMC Institute.",
  "telemetry_notice": "Este asistente registra tu actividad académica (por ejemplo, temas consultados y resultados de quizzes) para generar métricas de acompañamiento. No se guarda el contenido de tus mensajes dentro de las métricas."
}
```

## 6. Chat (primer envío)

`POST http://127.0.0.1:8000/api/chat` → **200**

```json
{
  "response": "Esto es lo que se explica sobre «LEFT JOIN» en la grabación:\n\n«Un uso muy práctico de LEFT JOIN es encontrar registros sin actividad. Después del join agregamos WHERE con IS NULL sobre la clave de la tabla derecha, y así vemos, por ejemplo, a los estudiantes que aún no tienen matrícula.» (min 07:10–08:25)\n\nTambién se menciona aquí: «Cuando necesitamos conservar todas las filas de la tabla izquierda usamos LEFT JOIN. Si no existe coincidencia en la tabla derecha, esas columnas se rellenan con NULL.» (min 05:30–07:10)\n\nPara comprobar que lo tienes claro: ¿cómo explicarías con tus palabras lo que dice el docente sobre LEFT JOIN?",
  "session_id": "d8770e06156741a992756c7b5cf1e4a6",
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "mode": "DEMO",
  "references": [
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=430",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 07:10–08:25",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 430.0,
      "end_seconds": 505.0,
      "timestamp": "07:10–08:25"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=330",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 05:30–07:10",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 330.0,
      "end_seconds": 430.0,
      "timestamp": "05:30–07:10"
    }
  ],
  "topic_detected": "left_join",
  "telemetry_event_id": null,
  "disclaimer": "DMC RescuePath AI — Asistente de orientación académica preventiva. No reemplaza al docente titular.",
  "lesson_id": "clase-02-uniones-y-agrupacion",
  "response_type": "grounded",
  "grounded": true,
  "follow_up_question": "Para comprobar que lo tienes claro: ¿cómo explicarías con tus palabras lo que dice el docente sobre LEFT JOIN?",
  "content_notice": "Modo DEMO: el asistente usa transcripciones y material FICTICIOS escritos para el MVP; no es contenido oficial de DMC Institute.",
  "provider": "demo",
  "model": null,
  "fallback_used": false,
  "provider_notice": null,
  "telemetry_notice": "Este asistente registra tu actividad académica (por ejemplo, temas consultados y resultados de quizzes) para generar métricas de acompañamiento. No se guarda el contenido de tus mensajes dentro de las métricas."
}
```

## 7. Chat (reintento con la misma Idempotency-Key)

`POST http://127.0.0.1:8000/api/chat` → **200** — devuelve la respuesta original sin duplicar mensajes ni eventos

```json
{
  "response": "Esto es lo que se explica sobre «LEFT JOIN» en la grabación:\n\n«Un uso muy práctico de LEFT JOIN es encontrar registros sin actividad. Después del join agregamos WHERE con IS NULL sobre la clave de la tabla derecha, y así vemos, por ejemplo, a los estudiantes que aún no tienen matrícula.» (min 07:10–08:25)\n\nTambién se menciona aquí: «Cuando necesitamos conservar todas las filas de la tabla izquierda usamos LEFT JOIN. Si no existe coincidencia en la tabla derecha, esas columnas se rellenan con NULL.» (min 05:30–07:10)\n\nPara comprobar que lo tienes claro: ¿cómo explicarías con tus palabras lo que dice el docente sobre LEFT JOIN?",
  "session_id": "d8770e06156741a992756c7b5cf1e4a6",
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "mode": "DEMO",
  "references": [
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=430",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 07:10–08:25",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 430.0,
      "end_seconds": 505.0,
      "timestamp": "07:10–08:25"
    },
    {
      "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=330",
      "topic": "clase-02-uniones-y-agrupacion",
      "title": "Clase 2 — Uniones y agrupación (DEMO) — min 05:30–07:10",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "start_seconds": 330.0,
      "end_seconds": 430.0,
      "timestamp": "05:30–07:10"
    }
  ],
  "topic_detected": "left_join",
  "telemetry_event_id": null,
  "disclaimer": "DMC RescuePath AI — Asistente de orientación académica preventiva. No reemplaza al docente titular.",
  "lesson_id": "clase-02-uniones-y-agrupacion",
  "response_type": "grounded",
  "grounded": true,
  "follow_up_question": "Para comprobar que lo tienes claro: ¿cómo explicarías con tus palabras lo que dice el docente sobre LEFT JOIN?",
  "content_notice": "Modo DEMO: el asistente usa transcripciones y material FICTICIOS escritos para el MVP; no es contenido oficial de DMC Institute.",
  "provider": "demo",
  "model": null,
  "fallback_used": false,
  "provider_notice": null,
  "telemetry_notice": "Este asistente registra tu actividad académica (por ejemplo, temas consultados y resultados de quizzes) para generar métricas de acompañamiento. No se guarda el contenido de tus mensajes dentro de las métricas."
}
```

## 8. Inicio del quiz

`POST http://127.0.0.1:8000/api/quiz/start` → **200**

```json
{
  "attempt_id": "fb185c956da0416c8268376f70d4ec7c",
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "lesson_id": "clase-02-uniones-y-agrupacion",
  "lesson_title": "Clase 2 — Uniones y agrupación (DEMO)",
  "status": "started",
  "num_questions": 4,
  "questions": [
    {
      "question_id": "c2-q05",
      "position": 0,
      "prompt": "Para encontrar a los estudiantes que aún no tienen matrícula, ¿qué se agrega después del LEFT JOIN?",
      "options": [
        {
          "option_id": "a",
          "text": "Un segundo INNER JOIN"
        },
        {
          "option_id": "b",
          "text": "Un WHERE con IS NULL sobre la clave de la tabla derecha"
        },
        {
          "option_id": "c",
          "text": "Un ORDER BY sobre la tabla izquierda"
        },
        {
          "option_id": "d",
          "text": "Un COUNT DISTINCT"
        }
      ]
    },
    {
      "question_id": "c2-q07",
      "position": 1,
      "prompt": "¿Qué cláusula filtra los grupos después de agrupar?",
      "options": [
        {
          "option_id": "a",
          "text": "HAVING"
        },
        {
          "option_id": "b",
          "text": "WHERE"
        },
        {
          "option_id": "c",
          "text": "ON"
        },
        {
          "option_id": "d",
          "text": "LIKE"
        }
      ]
    },
    {
      "question_id": "c2-q03",
      "position": 2,
      "prompt": "En un INNER JOIN con la tabla de matrículas, ¿qué pasa con un estudiante que no tiene ninguna matrícula?",
      "options": [
        {
          "option_id": "a",
          "text": "No aparece en el resultado"
        },
        {
          "option_id": "b",
          "text": "Aparece duplicado"
        },
        {
          "option_id": "c",
          "text": "Se produce un error"
        },
        {
          "option_id": "d",
          "text": "Aparece con valores NULL"
        }
      ]
    },
    {
      "question_id": "c2-q01",
      "position": 3,
      "prompt": "¿Mediante qué se relacionan la tabla de estudiantes y la tabla de matrículas según la clase?",
      "options": [
        {
          "option_id": "a",
          "text": "Una clave foránea"
        },
        {
          "option_id": "b",
          "text": "Un alias"
        },
        {
          "option_id": "c",
          "text": "Una función de agregado"
        },
        {
          "option_id": "d",
          "text": "Un comodín"
        }
      ]
    }
  ],
  "created_at": "2026-10-13T02:35:40.204044+00:00",
  "expires_at": "2026-10-13T03:35:40.204044+00:00",
  "resumed": false,
  "attempts_used": 1,
  "attempts_allowed": 3,
  "pass_threshold_percent": 60.0,
  "mode": "DEMO",
  "content_notice": "Modo DEMO: preguntas redactadas para el MVP a partir de una transcripción FICTICIA; no es material oficial de DMC Institute.",
  "telemetry_notice": "Este asistente registra tu actividad académica (por ejemplo, temas consultados y resultados de quizzes) para generar métricas de acompañamiento. No se guarda el contenido de tus mensajes dentro de las métricas."
}
```

## 9. Envío del quiz

`POST http://127.0.0.1:8000/api/quiz/submit` → **200** — 3 de 4 correctas

```json
{
  "attempt_id": "fb185c956da0416c8268376f70d4ec7c",
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "lesson_id": "clase-02-uniones-y-agrupacion",
  "total_questions": 4,
  "answered_count": 4,
  "correct_count": 3,
  "score_percent": 75.0,
  "grade_20": 15.0,
  "passed": true,
  "pass_threshold_percent": 60.0,
  "results": [
    {
      "question_id": "c2-q05",
      "position": 0,
      "prompt": "Para encontrar a los estudiantes que aún no tienen matrícula, ¿qué se agrega después del LEFT JOIN?",
      "selected_option_id": "b",
      "correct_option_id": "b",
      "is_correct": true,
      "explanation": "El docente indica que después del join se agrega WHERE con IS NULL sobre la clave de la tabla derecha.",
      "concept": "LEFT JOIN",
      "reference": {
        "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=430",
        "topic": "clase-02-uniones-y-agrupacion",
        "title": "Clase 2 — Uniones y agrupación (DEMO) — min 07:10–08:25",
        "lesson_id": "clase-02-uniones-y-agrupacion",
        "start_seconds": 430.0,
        "end_seconds": 505.0,
        "timestamp": "07:10–08:25"
      }
    },
    {
      "question_id": "c2-q07",
      "position": 1,
      "prompt": "¿Qué cláusula filtra los grupos después de agrupar?",
      "selected_option_id": "a",
      "correct_option_id": "a",
      "is_correct": true,
      "explanation": "El docente explica que WHERE filtra filas antes de agrupar y HAVING filtra grupos después de agrupar.",
      "concept": "HAVING",
      "reference": {
        "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=690",
        "topic": "clase-02-uniones-y-agrupacion",
        "title": "Clase 2 — Uniones y agrupación (DEMO) — min 11:30–12:40",
        "lesson_id": "clase-02-uniones-y-agrupacion",
        "start_seconds": 690.0,
        "end_seconds": 760.0,
        "timestamp": "11:30–12:40"
      }
    },
    {
      "question_id": "c2-q03",
      "position": 2,
      "prompt": "En un INNER JOIN con la tabla de matrículas, ¿qué pasa con un estudiante que no tiene ninguna matrícula?",
      "selected_option_id": "a",
      "correct_option_id": "a",
      "is_correct": true,
      "explanation": "INNER JOIN solo devuelve filas con pareja en ambas tablas; el estudiante sin matrícula simplemente no aparece.",
      "concept": "INNER JOIN",
      "reference": {
        "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=235",
        "topic": "clase-02-uniones-y-agrupacion",
        "title": "Clase 2 — Uniones y agrupación (DEMO) — min 03:55–05:30",
        "lesson_id": "clase-02-uniones-y-agrupacion",
        "start_seconds": 235.0,
        "end_seconds": 330.0,
        "timestamp": "03:55–05:30"
      }
    },
    {
      "question_id": "c2-q01",
      "position": 3,
      "prompt": "¿Mediante qué se relacionan la tabla de estudiantes y la tabla de matrículas según la clase?",
      "selected_option_id": "b",
      "correct_option_id": "a",
      "is_correct": false,
      "explanation": "El docente explica que las tablas se relacionan mediante una clave foránea: la matrícula guarda el id_estudiante de quien se inscribió.",
      "concept": "Clave foránea",
      "reference": {
        "reference": "ref:sql-demo/clase-02-uniones-y-agrupacion#t=50",
        "topic": "clase-02-uniones-y-agrupacion",
        "title": "Clase 2 — Uniones y agrupación (DEMO) — min 00:50–02:20",
        "lesson_id": "clase-02-uniones-y-agrupacion",
        "start_seconds": 50.0,
        "end_seconds": 140.0,
        "timestamp": "00:50–02:20"
      }
    }
  ],
  "submitted_at": "2026-10-13T02:35:40.224526+00:00",
  "institutional_notice": "Este resultado es solo académico y de demostración: no modifica la asistencia ni implica certificación. Otro módulo aplica las reglas institucionales.",
  "content_notice": "Modo DEMO: preguntas redactadas para el MVP a partir de una transcripción FICTICIA; no es material oficial de DMC Institute.",
  "mode": "DEMO"
}
```

## 10. Segundo envío del mismo intento

`POST http://127.0.0.1:8000/api/quiz/submit` → **409** — debe rechazarse

```json
{
  "error": {
    "code": "quiz_already_submitted",
    "message": "Este intento ya fue enviado; no se puede responder dos veces.",
    "details": null
  }
}
```

## 11. Micro-pregunta (¿corresponde?)

`GET http://127.0.0.1:8000/api/micro-question?student_id=demo-student-etapa4&course_id=sql-demo` → **200**

```json
{
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "due": true,
  "reason": "corresponde_en_este_periodo",
  "as_of": "2026-10-13",
  "period_index": 3,
  "question": {
    "question_id": "mq-contenido",
    "period_index": 3,
    "prompt": "¿Hay algún tema de las últimas clases que te haya costado más de lo esperado?",
    "optional": true
  },
  "notice": "Pregunta opcional: puedes omitirla. Tu respuesta se guarda para acompañarte mejor; no la uses para compartir datos personales."
}
```

## 12. Respuesta a la micro-pregunta

`POST http://127.0.0.1:8000/api/micro-question/answer` → **200**

```json
{
  "response_id": "e24f5c6e4f374feb90c36ebbd75a5cfc",
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "question_id": "mq-contenido",
  "period_index": 3,
  "status": "answered",
  "expressed_text": "Me cuesta un poco por el trabajo, llego justo de tiempo",
  "interpretation": {
    "category": "disponibilidad_tiempo",
    "candidate_categories": [
      "disponibilidad_tiempo",
      "trabajo",
      "dificultad_contenido"
    ],
    "confidence": "baja",
    "method": "reglas_conservadoras",
    "matched_cues": [
      "tiempo",
      "llego justo",
      "trabajo",
      "me cuesta",
      "cuesta"
    ],
    "disclaimer": "Interpretación automática por reglas sobre palabras clave: indica el TEMA que mencionaste, no es un diagnóstico ni una valoración psicológica, y no se usa para decidir nada sobre ti."
  },
  "notice": "¡Gracias! Tu respuesta es opcional y no cambia tus resultados del curso.",
  "telemetry_notice": "Este asistente registra tu actividad académica (por ejemplo, temas consultados y resultados de quizzes) para generar métricas de acompañamiento. No se guarda el contenido de tus mensajes dentro de las métricas."
}
```

## 13. Micro-pregunta (segunda consulta del mismo periodo)

`GET http://127.0.0.1:8000/api/micro-question?student_id=demo-student-etapa4&course_id=sql-demo` → **200** — ya no se vuelve a preguntar

```json
{
  "student_id": "demo-student-etapa4",
  "course_id": "sql-demo",
  "due": false,
  "reason": "ya_respondida_u_omitida_en_este_periodo",
  "as_of": "2026-10-13",
  "period_index": 3,
  "question": null,
  "notice": "Pregunta opcional: puedes omitirla. Tu respuesta se guarda para acompañarte mejor; no la uses para compartir datos personales."
}
```

## 14. Telemetría: resumen agregado

`GET http://127.0.0.1:8000/api/telemetry/summary` → **200**

```json
{
  "generated_at": "2026-10-13T02:35:40.274372+00:00",
  "filters": {
    "course_id": null,
    "lesson_id": null,
    "since": null,
    "until": null
  },
  "total_events": 8,
  "distinct_students": 1,
  "events_by_type": [
    {
      "key": "chat_question",
      "count": 1
    },
    {
      "key": "topic_difficulty",
      "count": 1
    },
    {
      "key": "content_retrieved",
      "count": 1
    },
    {
      "key": "lesson_summary_requested",
      "count": 1
    },
    {
      "key": "missed_content_requested",
      "count": 1
    },
    {
      "key": "quiz_started",
      "count": 1
    },
    {
      "key": "quiz_completed",
      "count": 1
    },
    {
      "key": "micro_question_answered",
      "count": 1
    }
  ],
  "top_topics": [
    {
      "key": "LEFT JOIN",
      "count": 1
    }
  ],
  "topics_with_difficulty_signals": [
    {
      "key": "Clave foránea",
      "count": 1
    }
  ],
  "most_retrieved_lessons": [
    {
      "key": "clase-02-uniones-y-agrupacion",
      "count": 1
    }
  ],
  "quiz": {
    "started": 1,
    "completed": 1,
    "average_score_percent": 75.0,
    "passed": 1,
    "pass_rate_percent": 100.0
  },
  "missed_content_requests": 1,
  "summary_requests": 1,
  "micro_question_categories": [
    {
      "key": "disponibilidad_tiempo",
      "count": 1
    }
  ],
  "mode_breakdown": [
    {
      "key": "DEMO",
      "count": 8
    }
  ],
  "data_scope": "demo_synthetic",
  "is_demo_metrics": true,
  "methodology_notice": "Conteos de actividad registrada por el asistente (DEMO). No son una probabilidad de deserción, no miden asistencia institucional y no sustituyen las reglas académicas de DMC."
}
```

## 15. Telemetría: eventos SIN token

`GET http://127.0.0.1:8000/api/telemetry/events` → **401** — debe rechazarse

```json
{
  "error": {
    "code": "telemetry_unauthorized",
    "message": "Token de telemetría ausente o inválido.",
    "details": null
  }
}
```

## 16. Telemetría: eventos con token

`GET http://127.0.0.1:8000/api/telemetry/events?limit=50` → **200**

```json
{
  "events": [
    {
      "event_id": "e9351f4a9f6d443b97ae08f2f14b9986",
      "event_type": "micro_question_answered",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": null,
      "session_id": null,
      "mode": "DEMO",
      "payload": {
        "category": "disponibilidad_tiempo",
        "confidence": "baja",
        "period_index": 3,
        "question_id": "mq-contenido"
      },
      "timestamp": "2026-10-13T02:35:40.254113+00:00"
    },
    {
      "event_id": "b66c1fd8b5654fc880b9d1c528aae514",
      "event_type": "topic_difficulty",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "session_id": null,
      "mode": "DEMO",
      "payload": {
        "attempt_id": "fb185c956da0416c8268376f70d4ec7c",
        "question_id": "c2-q01",
        "signal": "quiz_incorrect",
        "topic": "Clave foránea"
      },
      "timestamp": "2026-10-13T02:35:40.226760+00:00"
    },
    {
      "event_id": "c19c32292d684ec28ece3bcdd992095c",
      "event_type": "quiz_completed",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "session_id": null,
      "mode": "DEMO",
      "payload": {
        "attempt_id": "fb185c956da0416c8268376f70d4ec7c",
        "correct_count": 3,
        "passed": true,
        "score_percent": 75.0,
        "total_questions": 4
      },
      "timestamp": "2026-10-13T02:35:40.226581+00:00"
    },
    {
      "event_id": "493cf61ce1aa4b5cb956f8b300225760",
      "event_type": "quiz_started",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "session_id": null,
      "mode": "DEMO",
      "payload": {
        "attempt_id": "fb185c956da0416c8268376f70d4ec7c",
        "num_questions": 4,
        "resumed": false
      },
      "timestamp": "2026-10-13T02:35:40.208695+00:00"
    },
    {
      "event_id": "bf09b90b4ede4b01b9c657a8710e592d",
      "event_type": "content_retrieved",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "session_id": "d8770e06156741a992756c7b5cf1e4a6",
      "mode": "DEMO",
      "payload": {
        "segments": [
          {
            "end_seconds": 505.0,
            "lesson_id": "clase-02-uniones-y-agrupacion",
            "start_seconds": 430.0
          },
          {
            "end_seconds": 430.0,
            "lesson_id": "clase-02-uniones-y-agrupacion",
            "start_seconds": 330.0
          }
        ],
        "source": "chat"
      },
      "timestamp": "2026-10-13T02:35:40.169928+00:00"
    },
    {
      "event_id": "64b592060a70447da488917ab27be4dc",
      "event_type": "chat_question",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "session_id": "d8770e06156741a992756c7b5cf1e4a6",
      "mode": "DEMO",
      "payload": {
        "grounded": true,
        "message_length": 19,
        "response_type": "grounded",
        "topic": "LEFT JOIN",
        "topic_id": "left_join",
        "user_intent": "question"
      },
      "timestamp": "2026-10-13T02:35:40.156810+00:00"
    },
    {
      "event_id": "35ba606b3a984445a319a86b232bfc01",
      "event_type": "missed_content_requested",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "session_id": null,
      "mode": "DEMO",
      "payload": {
        "concepts": [
          "INNER JOIN",
          "ON",
          "LEFT JOIN",
          "NULL"
        ],
        "interval_count": 1,
        "segment_count": 3,
        "total_missed_seconds": 240.0
      },
      "timestamp": "2026-10-13T02:35:40.111520+00:00"
    },
    {
      "event_id": "2aac2d95cfe5471280d1a08feed6446b",
      "event_type": "lesson_summary_requested",
      "student_ref": "est_283816cc3e5b",
      "course_id": "sql-demo",
      "lesson_id": "clase-02-uniones-y-agrupacion",
      "session_id": null,
      "mode": "DEMO",
      "payload": {
        "concept_count": 11,
        "engine": "extractive",
        "section_count": 4
      },
      "timestamp": "2026-10-13T02:35:40.084517+00:00"
    }
  ],
  "count": 8,
  "limit": 50,
  "next_before": null,
  "data_scope": "demo_synthetic",
  "privacy_notice": "Datos SINTÉTICOS de demostración. Los estudiantes se muestran con un seudónimo estable (student_ref), nunca con su identificador original. Con estudiantes reales este endpoint exige autenticación y autorización."
}
```
