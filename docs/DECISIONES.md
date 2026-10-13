# Decisiones del MVP (rama `mateo`)

Referencia: [`SOLUCION_DMC_CURSOR.md`](SOLUCION_DMC_CURSOR.md). Este documento registra las elecciones que no salen literalmente de la especificación o que conviene dejar explícitas para la demo y el mantenimiento.

## Reloj simulado

`app/config.py` expone `now()` / `set_now()`. Si `SIM_NOW` está definido en `backend/.env`, esa fecha es la «hora actual» del sistema (plazos de 7 días, escalamiento a 48 h, demo del mes 3). Vacío = hora real del servidor. El endpoint `GET/POST /api/admin/clock` permite mover el reloj en caliente sin reiniciar. Los tests dejan `SIM_NOW` vacío y usan `set_now()` por caso.

Valor de demo: `2026-10-12T19:00:00` (sesión 24 recién dictada; plazos de las sesiones 23 y 24 aún abiertos).

## Curso de demo

`course-detail.html` del frontend base habla de un curso de 1 mes. El seed crea el curso real de la especialización n8n con **52 sesiones** (martes y jueves, 19:30–22:30), empezando el 2026-07-21, de modo que con `SIM_NOW` se está en el mes 3 (24 sesiones dictadas). El texto estático de fechas del HTML no se reescribió; la barra de asistencia efectiva, las grabaciones y el retiro sí leen del API.

## Roles y Asistente Académico

- Docente: Lincoln Villanueva (`docente`).
- Asistente Académica del curso: Michela López (`asistente_academico`), guardada en `course.academic_assistant_id`. Las alertas de nivel 2 se asignan a ese rol.
- Tutor y coordinador también son staff (`tutor`, `coordinador`).
- El tablero `/tutor.html` y los endpoints `/api/tutor/*` exigen rol en `{tutor, asistente_academico, docente, coordinador}`.
- «Entrar como tutor» en el login de demo entra como Michela (Asistente Académica), porque es quien recibe la cola de nivel 2.

## LLM (Kimi / Moonshot)

- Cliente OpenAI-compatible contra `LLM_BASE_URL` (por defecto `https://api.moonshot.ai/v1`).
- Modelos: `LLM_MODEL_CHAT` y `LLM_MODEL_FAST` = `kimi-k2.6` (verificar en la consola de Moonshot el nombre exacto habilitado para la cuenta).
- `LLM_MOCK=1`, o una API key vacía / `...`, activa respuestas simuladas (sin costo). Pytest fuerza mock.
- Prompts en español en `backend/app/prompts/`. Salida JSON validada con Pydantic y un reintento.
- Chat: si el contexto supera el 70 % de `LLM_CONTEXT_TOKENS`, se reduce con BM25 (`rank-bm25`) a los fragmentos más relevantes. Si la pregunta no está en la clase, la respuesta fija es «Eso no se vio en esta clase…».
- Criterio «16 de 20» del chat: `scripts/eval_chat.py`. Con mock da 19/20; con key real hay que volver a correrlo.

## Sesión de muestra y video

Mientras el equipo no deje exportaciones en `backend/data/zoom/<id>/`, el seed usa `backend/data/sample/session_demo/` (VTT + chat + participantes + `processed.json`). Si no hay `recording.mp4`, el frontend muestra un `TranscriptPlayer` con la misma interfaz que `<video>` (incluye velocidades 1×–30×) y el seguimiento de rangos vistos funciona igual.

## Estilos

Solo se agregó `frontend/solution.css`, reutilizando variables de `dmc.css`, Poppins y Font Awesome 6.5.2. **No se modificaron** `dmc.css`, `styles.css`, `enhancements.css` ni `fine-tuning.css`. Las páginas nuevas copian el shell (`header.topbar` / `aside#sidebar` / `main.page` / footer) y cargan `app.js` + `portal.js`. El layout móvil (≤900 px) se activa solo en páginas con `body.sol-responsive`.

## Asistencia efectiva

- En vivo: tiempo conectado ≥ 70 % del horario (`live_min_ratio`). Cortes de Zoom &lt; 2 min se unen (reconexión) y cuentan como conectado.
- Recuperada: antes del plazo (fecha de la clase + 7 días a las 23:59) el alumno vio ≥ 90 % de los tramos clave **y** aprobó el quiz (≥ 3 de 5). Fuera de plazo = `no_cumplida` aunque después vea y apruebe.
- Proyección: `(cumplidas + restantes × tasa de las últimas 4 decididas) / total`. Las `por_recuperar` no entran en la tasa reciente.
- Umbral de certificación del curso: 80 %.

## Diagnóstico (4 estados)

Orden de prioridad: `inactivo` (14 días sin actividad) → `en_riesgo` → `en_adaptacion` → `activo`. Los motivos van en lenguaje claro (proyectados al alumno y al tutor). Umbrales calibrados en `config.py` hasta cumplir `scripts/evaluate.py` (recall ≥ 80 % con ≥ 14 días de anticipación, falsas alarmas ≤ 20 %, adaptados no quedan en riesgo tras el cambio).

## Rescate en 2 niveles

- Nivel 1: al pasar a `en_riesgo` o `inactivo`, o al quedar una sesión `por_recuperar`, se escribe en `outbox` (portal + email + WhatsApp simulados) con plantilla que menciona la clase y ~20 minutos.
- Nivel 2: a las 48 h sin actividad (watch/heartbeat/quiz/chat) o al pasar a `inactivo`, o ante solicitud de retiro. Asignado a `asistente_academico`.
- Cierre automático si el alumno recupera o deja de estar en riesgo; cierre manual al registrar contacto desde el tablero.

## Recálculo

`services/recompute.py` es el único camino que persiste cumplimientos, features, snapshots de riesgo y alertas. Corre desde `POST /api/admin/recompute`, tras eventos del alumno (watch, quiz, micro, retiro) y cada hora vía APScheduler (desactivable con `DISABLE_SCHEDULER=1`). El seed además recorre los 28 días previos a `SIM_NOW` para dejar historial de estados y alertas coherente con la demo.

## Autenticación de demo

Sin OAuth. Header `X-User-Id` (o `?uid=` para `sendBeacon`). El login ofrece «Entrar como alumno» (elige un caso `cambio_horario` con proyección bajo el umbral y 1–2 pendientes), «Entrar como tutor» (Michela) y un selector con todos los usuarios sintéticos.

## Cohorte sintética

30 alumnos × 52 sesiones, 5 perfiles (`estable`, `mala_conexion`, `cambio_horario`, `desinteres`, `adaptado`) en `ground_truth.csv`. Una fracción de `cambio_horario` recupera **después** del plazo de 7 días para ejercitar el caso «fuera de plazo» del criterio 12. Verificación: `scripts/check_attendance.py`.

## Tensión de los «20 minutos»

Los tramos clave de la sesión de muestra suman ~83 min (46 % de 3 h). Con el interruptor «Reproducir solo tramos clave» y velocidad 5×–10× en el reproductor de transcripción, la recuperación cabe en ~10–20 minutos de reloj; a 1× no. Queda documentado como límite de la sesión de muestra: cuando haya video y tramos validados por el docente, el tiempo real bajará.

## Datos y secretos

- Grabaciones, exportaciones Zoom, cohorte generada y `*.db` viven bajo `backend/data/` y están en `.gitignore` (excepto `data/sample/`).
- `backend/.env` no se versiona; plantilla en `.env.example`.
- Consentimiento (Ley 29733) versionado (`CONSENT_VERSION`); el modal aparece a los alumnos hasta que aceptan.

## Alcance fuera del MVP

Encuesta final y pagos aparecen en «Mi avance» como `pendiente_verificar`. El servicio `ai-service/` del repo original no se usa; toda la lógica de IA está en `backend/`.
