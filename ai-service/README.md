# AI Service

Carpeta reservada para el módulo de inteligencia artificial y sus APIs.

La idea es mantener esta lógica separada del frontend para que ambos equipos puedan trabajar sin pisarse archivos. Cuando el servicio esté listo, el frontend se conectará mediante endpoints definidos por el equipo de IA/backend.

## Etapa 7: calidad del tutor, citas y resumen local

Todo local y determinista: **no se hizo ninguna llamada a NVIDIA** (las pruebas usan transporte simulado y `conftest` bloquea la red externa).

- **Recuperador / citas** (`app/knowledge/intent_signals.py`, `retriever.py`, `services/intents.py`, `tutor_service.py`):
  las frases de estilo de la pregunta ("de forma sencilla", "según lo enseñado por el profesor", "por favor") ya no entran a la búsqueda ni cruzan frases literales;
  en preguntas de definición ("¿qué es X?", "¿para qué sirve X?") se priorizan los segmentos donde X aparece junto a una expresión de función o donde la clase plantea esa pregunta
  (`SegmentMatch.direct`); un segmento secundario solo acompaña si tiene ≥ 70 % (NVIDIA) o ≥ 85 % (definiciones) de la puntuación del principal. Sin `intent` el orden es el de siempre.
- **Prompt v1.1.0** (`llm/prompts.py`): responder directo y breve, parafrasear, no atribuir al docente lo que no dice, separar un "Complemento (no es de la clase)",
  corregir con cautela errores evidentes del reconocimiento de voz (`NHN`→`n8n`, solo en el texto que viaja al modelo), `answerable:false` cuando la clase no cubre la pregunta, y avisos
  de contexto (tema solo mencionado / términos ausentes). **Verificador** (`llm/verification.py`): rechaza «citas textuales» de 4+ palabras que no estén en los segmentos.
- **Resumen local por temas** (`knowledge/topic_lexicon.py`, `lesson_analysis.py`): títulos académicos asignados solo si las palabras clave del tema aparecen en esa parte de la clase,
  bloques contiguos unidos (≤ 30 min), frase textual más informativa por capítulo y un resumen general que es un **índice con minutos**. `PIPELINE_VERSION = summary/1.2`: se regenera una
  vez; después, caché SQLite. La pantalla distingue "Resumen local por temas · sin IA generativa" de "Resumen generado con Kimi".
- **Resumen con Kimi:** acción administrativa explícita ya existente; `scripts/plan_kimi_summary.py` muestra (sin red) cuántas solicitudes haría. No se ejecutó.
- **Evaluación reproducible:** `eval/tutor_eval_cases.json` + `scripts/eval_tutor.py` (modo DEMO real y modo "LIVE simulado", etiquetado siempre como simulado). Comprueba comportamiento,
  pertinencia, citas exactas y minutos, estado declarado. Informes en `data/real/` (ignorado por Git).
- **Límites conocidos:** el modo DEMO no sabe que un tema solo se *menciona* (p. ej. "¿Qué es Git?"); con Kimi lo señala el aviso de evidencia. Un seguimiento tipo "otro ejemplo" solo recuerda los
  términos de la pregunta anterior. El tiempo de una respuesta LIVE es el de NVIDIA (no medido con este prompt): el backend local responde en ~10 ms.

## Etapa 6.2: tiempos de espera de Kimi K3 (NVIDIA)

**Causa del `llm_timeout: NVIDIA no respondió en 60 s`:** el valor por defecto de `NVIDIA_TIMEOUT_SECONDS` era 60 s (el `.env` no lo definía) y la
pregunta pedía hasta 1200 tokens de salida. Kimi K3 siempre razona y esos tokens cuentan; con el ritmo medido (258 tokens en 42 s, ~6 tokens/s) 1200 tokens
pueden tardar ~200 s. No hay otros límites en el camino: ni el servicio de chat, ni el servidor ni el navegador cortaban a los 60 s, y la pregunta hace
**una sola** llamada al modelo (el guardián heurístico la da por pertinente; el guardián LLM solo se consulta si la heurística duda).

- `NVIDIA_TIMEOUT_SECONDS=120` (por llamada, 5-300 s) · `NVIDIA_TOTAL_TIMEOUT_SECONDS=150` (por pregunta, guardián + tutor, 5-600 s) ·
  `NVIDIA_GUARDIAN_TIMEOUT_SECONDS=25` (3-120 s) · `NVIDIA_MAX_OUTPUT_TOKENS=700` (el proveedor aplica un mínimo de 512). Cualquier valor ilegible o
  fuera de rango se acota: ningún ajuste puede producir una espera infinita. Se ven en `/health` → `llm.limits`.
- Un solo reloj por pregunta (`NvidiaKimiProvider.request_scope`, usado por `TutorService.chat`): cuando se agota no se inicia otra llamada. No hay reintentos.
- Si NVIDIA no responde a tiempo, la respuesta sale en **DEMO con las mismas citas** y el aviso "Kimi no participó en esta respuesta".
- Frontend (`demo-n8n.html`): "Kimi está preparando tu respuesta…" con contador de segundos, aviso si pasan 15 s, una sola pregunta a la vez (botón, sugerencias y
  Enter), cancelación del navegador a los 190 s y la etiqueta "Respuesta DEMO · NO la generó Kimi" cuando hubo fallback.
- **Pruebas herméticas:** `tests/conftest.py` fuerza DEMO y bloquea toda conexión que no sea local en cada prueba. Antes, una prueba con la configuración por
  defecto heredaba el `.env` real (LIVE) y podía intentar una solicitud real a NVIDIA.
- No es un servicio para producción: el tier gratuito de NVIDIA puede tardar más en horas de mucha demanda. Una opción futura es transmitir la respuesta (`stream`).

## Etapa 6.1: privacidad antes de usar la clase real con NVIDIA

`NVIDIA_ALLOW_CLASS_CONTENT` sigue en `false`: esta etapa **prepara** la activación, no la hace.

- **Barrera final** (`NvidiaKimiProvider.attach_privacy`, cableada en `main.py`): justo antes de cada envío, todo mensaje que no sea el prompt de
  sistema (pregunta, historial y fragmentos recuperados) se vuelve a enmascarar y se verifica con una comprobación independiente
  (`PrivacyFilter.known_name_hits`). Si aun así quedara un nombre conocido, la petición **no se envía** (`llm_unmasked_content`) y la respuesta
  sale en DEMO con aviso visible. Cubre `generate`, `classify_relevance` y `summarize_segments`: las tres pasan por `_post`.
  `/health` → `llm.egress_guard` informa cuántos nombres conoce el filtro y cuántos envíos se bloquearon (nunca los nombres).
- **Historial**: el servicio lo enmascara **antes** de truncarlo a 500 caracteres (un corte a mitad de un nombre dejaría un trozo irreconocible).
- **`app/privacy.py`**: nuevos disparadores de interpelación (`dime (este) X`, `dinos X`, `perdona/perdóname X`), las entradas de varias palabras de
  `mask-names.txt` se registran palabra por palabra, se ignoran caracteres invisibles que partirían un nombre, y más productos quedan fuera de
  "nombre" (Docker Desktop, Git, Slack...).
- **`demo-assets/mask-names.txt`** (privado, ignorado por Git): un nombre por línea; incluye variantes que el reconocimiento automático deformó.
  Tras editarlo, reinicia el backend.
- **Procedimiento antes de autorizar** (todo local, sin red, sin leer `.env`):
  1. `.\.venv\Scripts\python.exe -X utf8 scripts\review_privacy.py` → `data/real/revision_privacidad.md` (candidatos a nombre, URLs, números).
  2. `.\.venv\Scripts\python.exe -X utf8 scripts\inspect_nvidia_payload.py` → `data/real/payload_nvidia_preview.md`: las solicitudes exactas que
     SALDRÍAN (sanitizadas: sin clave, sin prompt de sistema, sin nombres), capturadas con un transporte simulado y sockets bloqueados.
     Usa una clave de mentira y concede la autorización de contenido solo dentro de ese proceso.
- **Límite**: es heurístico. Demuestra que los nombres *conocidos* no salen; no puede demostrar que no exista un nombre que ni la lista ni las
  reglas reconocen, ni cubre datos personales sin forma de nombre (empleador, ciudad, anécdotas).

## Etapa 6: Kimi K3 mediante NVIDIA Build

Proveedor `NvidiaKimiProvider` (`app/llm/nvidia_provider.py`), **independiente** de `KimiProvider` (Moonshot) y de `DemoProvider`, que no se modificaron.
Endpoint `https://integrate.api.nvidia.com/v1/chat/completions`, modelo `moonshotai/kimi-k3`, `Authorization: Bearer <NVIDIA_API_KEY>`.

- **Activación (dos condiciones explícitas):** `LLM_PROVIDER=nvidia` **y** `LLM_LIVE_AUTHORIZED=true`, además de `NVIDIA_API_KEY` en `ai-service/.env`.
  Con solo la clave, el sistema sigue en **DEMO** (así lo comprueba `/health` → `llm.selection_reason`).
- **Parámetros (según la documentación oficial):** `reasoning_effort` (`low|high|max`, por defecto `low`); sin `thinking` ni `response_format` por defecto
  (no documentados para NVIDIA). El razonamiento (`reasoning_content`) se **ignora**: no se muestra, guarda ni registra.
- **Errores (sin reintentos):** 401/403/404/429/5xx → mensaje preciso y **respuesta DEMO con aviso visible** (`fallback_used`, `provider_notice`).
  Tras un 401/403/404/402 (300 s) o 429 (60 s o `Retry-After`) el proveedor se **pausa** y no vuelve a llamar hasta que pase el enfriamiento;
  `/health` → `llm.provider_status` indica el motivo.
- **Privacidad:** NVIDIA puede registrar entradas y salidas. El contenido de la **clase real** no se envía mientras `NVIDIA_ALLOW_CLASS_CONTENT=false`.
  Nunca viajan el video, la transcripción completa ni identificadores de estudiantes; los datos personales reconocibles se enmascaran.
  `scripts/review_privacy.py` (local, sin red) revisa qué podría salir.
- **Primera prueba real (una sola solicitud, texto ficticio):**
  `.\.venv\Scripts\python.exe scripts\verify_nvidia_live.py --i-authorize-one-nvidia-request`
- El resumen generativo de la clase **no** se activa solo: sigue siendo una acción administrativa explícita; el resumen heurístico persistido es el que se muestra.
- Las pruebas usan **solo** transporte simulado: ninguna llama a NVIDIA. Que el modelo responda de verdad solo lo demuestra la prueba real autorizada.

## Etapa 5: clase real de n8n (video + resumen persistido + tutor con citas)

Demostración con **una clase real de DMC sobre introducción a n8n** (curso `n8n-intro`, clase `clase-01-introduccion-n8n`).
Guía paso a paso: [`../DEMO_JUEVES.md`](../DEMO_JUEVES.md). Pantalla: `frontend/demo-n8n.html`.

- **Material privado:** `demo-assets/` (MP4 + TXT) está en el `.gitignore` de la raíz y nunca se copia, se sube ni se envía a un servicio externo.
  La transcripción se ingiere **en memoria** (el original no se modifica) y los datos personales reconocibles se enmascaran.
- **Ingesta del TXT** (`app/knowledge/txt_transcript.py`): marcas `M:SS`/`H:MM:SS` solas en una línea; los números pronunciados ("a las 2:30")
  no se confunden con marcas; lo dudoso se **reporta** (no se corrige en silencio). Renglones de ~5 s → segmentos de ~40 s con su marca y línea originales.
- **Video** (`GET /api/demo/lessons/{id}/video`): streaming con HTTP Range desde su ubicación original (nunca en memoria, sin base64, sin rutas
  elegidas por el cliente). `app/media/mp4_probe.py` comprueba códecs y duración.
- **Resumen persistido** (`lesson_summaries`): se genera una vez por versión de la transcripción (huella SHA-256) al procesar la clase y se reutiliza;
  cambia la transcripción ⇒ se regenera. `engine=best` devuelve el de Kimi si existe; si no, el **heurístico DEMO** (sin modelo de lenguaje).
  El resumen con Kimi se genera **por fragmentos** y solo mediante `POST /api/admin/lessons/{curso}/{clase}/summary?engine=kimi` (token admin, **de pago**).
- **Guardián + tutor:** el guardián clasifica la pregunta (relevante / no relevante / incierta) y el tutor responde solo con segmentos recuperados.
  En DEMO el guardián es **heurístico** (`method: "heuristic"`); solo con Kimi LIVE autorizado puede ser un LLM (`method: "kimi"`, `is_llm: true`).
- **Citas:** `references[]` incluye `seek_seconds`, el renglón exacto al que debe saltar el reproductor.

## Etapa 4: recuperación académica (resumen, capítulos, "lo que te perdiste", quiz, micro-pregunta y telemetría)

Todo funciona en modo DEMO, **sin ninguna API externa**: lógica determinista sobre las transcripciones, con datos y estudiantes
**ficticios**. Los identificadores de estudiante son sintéticos (el backend aún no autentica).

| Endpoint | Qué hace |
|---|---|
| `GET /api/courses/{c}/lessons/{l}/summary` | Resumen **extractivo**: frases textuales con su minuto, conceptos clave y segmentos usados. `?engine=llm` está preparado pero deshabilitado (no se llama a ningún LLM) |
| `GET /api/courses/{c}/lessons/{l}/chapters` | Capítulos con inicio/fin reales (los de los segmentos), título (ficha del curso), descripción textual y referencias |
| `POST /api/lessons/missed-content` | Segmentos que se solapan con intervalos de ausencia **simulados**; normaliza duplicados/solapes y rechaza intervalos negativos, invertidos, vacíos o fuera de la duración (422). No consulta Zoom ni infiere ausencias |
| `POST /api/quiz/start` · `POST /api/quiz/submit` | Quiz de 3-5 preguntas (4 alternativas) fundamentadas en la transcripción. Las respuestas correctas **no** salen en `start`; la nota se calcula en el backend; un intento solo se envía una vez; límites de intentos y caducidad |
| `GET /api/micro-question` · `POST /api/micro-question/answer` | Una micro-pregunta breve y **opcional** por periodo (≈14 días desde `MICRO_QUESTION_REFERENCE_DATE`, tras completar un quiz). Se guarda aparte el texto expresado y la categoría interpretada por reglas conservadoras |
| `GET /api/telemetry/summary` · `/events` · `/catalog` | Telemetría del asistente para el dashboard (ver [`docs/telemetry_contract.md`](docs/telemetry_contract.md)) |

- **Aprobar un quiz no modifica asistencia ni implica certificación**: solo entrega el resultado académico para que otro módulo aplique las reglas institucionales. El umbral (`QUIZ_PASS_THRESHOLD_PERCENT`) es de demostración.
- **Quiz fundamentado:** cada pregunta de `app/knowledge/quizzes/` cita un `segment_id` y una `evidence_quote` que debe aparecer textualmente en ese segmento; si no, la pregunta se rechaza al cargar el banco.
- **Telemetría:** catálogo cerrado de 8 eventos (`chat_question`, `topic_difficulty`, `content_retrieved`, `lesson_summary_requested`, `missed_content_requested`, `quiz_started`, `quiz_completed`, `micro_question_answered`), sin guardar el texto de los mensajes. El estudiante recibe un `telemetry_notice` en las respuestas. No hay clasificador de riesgo ni cálculo de deserción.
- **Idempotencia:** los `POST` aceptan `Idempotency-Key`; un reintento devuelve la respuesta original sin duplicar efectos.
- **Evidencia:** [`docs/evidence/etapa4_demo_http.md`](docs/evidence/etapa4_demo_http.md) se genera con `scripts/demo_stage4_http.py` contra un servidor real.

```powershell
# servidor de demostración con micro-pregunta y consulta de eventos habilitadas
$env:MICRO_QUESTION_REFERENCE_DATE='2026-09-01'; $env:TELEMETRY_API_TOKEN='demo-token-etapa4'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# en otra terminal:
.\.venv\Scripts\python.exe scripts\demo_stage4_http.py --db data\rescuepath.db
```

## Etapa 3: tutor con IA generativa (Kimi) y tutoría socrática

El servicio arranca en **DEMO** (plantillas, sin red, sin costo). El modo **LIVE** usa la API de Moonshot/Kimi y solo se activa cuando se cumplen **las tres** condiciones: `LLM_PROVIDER=kimi`, `LLM_API_KEY` definida y `LLM_LIVE_AUTHORIZED=true`. Si falta cualquiera, sigue en DEMO y `GET /health` → `llm.selection_reason` dice por qué.

> **Costo.** La API de Kimi **no es gratuita**: se factura por tokens (los precios oficiales vigentes figuran en <https://platform.kimi.ai/docs/pricing/chat>; por ejemplo, `kimi-k2.6`: USD 0,95 / 1 M tokens de entrada y USD 4,00 / 1 M de salida; `kimi-k3`: USD 3,00 y USD 15,00; verifíquelos antes de activar, pueden cambiar). El sistema acota el gasto con `LLM_MAX_OUTPUT_TOKENS`, `LLM_MAX_HISTORY_MESSAGES`, `LLM_MAX_EVIDENCE` y `LLM_MAX_LIVE_CALLS`, y envía solo los segmentos relevantes (nunca la transcripción completa). Aun así, **no active LIVE sin un presupuesto autorizado**.

### Cómo activar LIVE
1. Cree una clave en la consola de Kimi (<https://platform.kimi.ai>) con saldo y un límite de gasto.
2. Copie `.env.example` a `.env` (está en `.gitignore`; **nunca** suba una clave) y complete:
   ```
   LLM_PROVIDER=kimi
   LLM_API_KEY=<su clave>
   LLM_LIVE_AUTHORIZED=true
   LLM_MODEL=kimi-k2.6
   ```
3. Reinicie el servidor y confirme en `GET /health`: `"mode": "LIVE"`, `"llm": {"active_provider": "kimi", ...}` (la clave nunca se muestra).
4. (Opcional, **facturable**) verificación real de una sola llamada:
   `.\.venv\Scripts\python.exe scripts\verify_kimi_live.py --confirm-billable-call`

### Cómo responde (flujo)
`pregunta → recuperador (Etapa 1) → contexto mínimo → LLM → verificación → citas construidas por el backend`
- **Tutoría socrática:** el prompt de sistema versionado (`app/llm/prompts.py`, `rescuepath-socratic-tutor/1.0.0`) pide explicar primero y, si aporta valor, cerrar con UNA pregunta socrática breve; distingue duda académica, explicación adicional, repetir y seguimiento; y obliga a no inventar contenido de DMC ni prometer certificados, notas o asistencia.
- **Citas verificables:** el modelo solo puede nombrar etiquetas (`S1`, `S2`…) de los segmentos que se le entregaron. `app/llm/verification.py` valida las etiquetas, sustituye `[S1]` por el minuto real (`min 06:22–07:20`) y rechaza minutos inventados, fugas del prompt, enlaces y afirmaciones sin respaldo. `references[]` lo construye el backend con el recuperador. **Esto reduce, pero no elimina, el riesgo de que el modelo parafrasee mal un segmento**: por eso cada respuesta muestra el minuto exacto para contrastar.
- **Sin evidencia:** no se llama al modelo; se responde con la plantilla de falta de información (`mode: DEMO`, sin citas).
- **Si Kimi falla:** timeout, 5xx, 429 o respuesta no verificable → con `LLM_FALLBACK_TO_DEMO=true` se responde con DEMO **avisándolo** (`mode: "DEMO"`, `fallback_used: true`, `provider_notice`, y el motivo queda en SQLite); con `false` se devuelve un error 5xx estructurado. Un error de autenticación (401/403) o de configuración (400/404) **nunca** se disfraza de éxito: devuelve 502 `llm_auth_error` / `llm_request_rejected`.
- **Seguridad:** transcripciones y mensajes del estudiante se tratan como *datos*: van delimitados y neutralizados, y un segmento que da órdenes al asistente no se envía al modelo. Son defensas básicas, no una garantía absoluta.
- **`mode` y `provider`** de cada respuesta indican qué generó realmente el texto.

### Pruebas
```powershell
cd ai-service
.\.venv\Scripts\python.exe -m pytest          # toda la suite; NO consume tokens
```
Las pruebas de Kimi usan un transporte HTTP simulado (`tests/llm_helpers.py`, `httpx.MockTransport`): prueban nuestro código (contexto, verificación, fallback), **no** que la API real responda. Eso solo lo comprueba `scripts/verify_kimi_live.py` con una clave real y confirmación explícita.

## Etapa 2: asistente conversacional DEMO + API

Backend FastAPI ejecutable, **sin ningún modelo de IA externo**. El modo DEMO responde con plantillas pedagógicas deterministas y citas textuales de la transcripción recuperada.

```powershell
cd ai-service
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload     # docs interactivas: http://127.0.0.1:8000/docs
```

| Endpoint | Descripción |
|---|---|
| `GET /health` | Estado, modo (DEMO), almacenamiento SQLite y conocimiento cargado (503 si la base falla) |
| `GET /api/courses` | Cursos disponibles |
| `GET /api/courses/{course_id}/lessons` | Clases con transcripción y metadatos |
| `POST /api/chat` | Mensaje del estudiante → respuesta con `references[]` (clase, `start_seconds`, `end_seconds`, `timestamp`) |

Errores siempre como `{"error": {"code", "message", "details"}}` (422 validación, 404 curso/clase/sesión, 403 sesión ajena, 503 almacenamiento, 500 interno genérico).

- **Memoria:** una sesión pertenece a un único (estudiante, curso, clase). Guarda el tema actual para interpretar "otro ejemplo", "no entendí" o "¿qué pasa si no uso eso?". Al proveedor solo se le entrega una ventana de los últimos 6 mensajes.
- **Almacenamiento:** SQLite en `ai-service/data/rescuepath.db` (ruta absoluta, independiente del directorio de ejecución; fuera de Git). Tablas `sessions` y `messages`.
- **Datos ficticios:** `student_id` es un identificador **sintético de demostración, no una credencial**: el backend todavía no autentica a nadie. No introduzca datos personales reales.
- **Proveedores:** `app/llm/` define la interfaz `LLMProvider`; solo existe `DemoProvider`. Un proveedor LLM real recibirá el mismo `TutorContext` y no podrá crear citas.

## Etapa 1: base de conocimiento

Capa de recuperación sobre la que se construye el asistente, que conversa sobre las grabaciones de clase y **cita los minutos** de cada explicación.

```
app/knowledge/
  retriever.py        KnowledgeRetriever: temas del curso (retrieve) y segmentos de clase (retrieve_segments)
  text_utils.py       normalización: minúsculas, sin acentos, palabras vacías, stemming ligero
  transcripts.py      lectores de transcripciones VTT y JSON
  courses/            fichas de tema por curso (*.json)
  transcripts/<course_id>/<lesson_id>.vtt|json   transcripciones (course_id y lesson_id salen de la ruta)
app/models/transcript.py   TranscriptSegment, LessonTranscript, SegmentMatch
```

Los tiempos se guardan en **segundos** (`start_seconds`, `end_seconds`); `minute_ref` (`MM:SS`) y `timestamp_range` se derivan de ellos.

Todo el material incluido (`sql-demo`) es **ficticio y no oficial de DMC**.

## Pruebas

```powershell
cd ai-service
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
```
