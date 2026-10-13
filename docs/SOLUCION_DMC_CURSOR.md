# Especificación de la solución – Reto DMC (MVP)

> **Para Cursor.** Este documento describe qué construir, cómo debe funcionar y con qué tecnologías. Léelo completo antes de empezar. Si algo no está definido aquí, elige la opción más simple y déjala anotada en `docs/DECISIONES.md`.

---

## 0. Instrucciones de arranque (hazlo primero)

1. **Regla obligatoria: todo el trabajo se hace únicamente en la rama `mateo`.** Créala desde `main` antes de tocar cualquier archivo:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b mateo
   git push -u origin mateo
   ```
   - Antes de cada cambio, confirma con `git branch --show-current` que estás en `mateo`.
   - **Prohibido** hacer commits, push, merge, rebase o pull requests hacia `main` o cualquier otra rama. No borres ni renombres ramas, y no reescribas el historial (`git push --force`).
   - `main` solo se usa como origen de la copia inicial. La integración con `main` la decide el equipo después, fuera de esta tarea.
   - Si algún paso parece requerir cambios fuera de `mateo`, detente y pregunta.
2. La base visual ya existe en `frontend/` (HTML + CSS + JavaScript sin framework, réplica del aula virtual de DMC). **Úsala como plantilla y no la rediseñes.** Cada página nueva copia la misma estructura de las existentes: `header.topbar`, `aside#sidebar.sidebar`, `div.page-shell > main.page`, `div.top-footer`, y carga `dmc.css` y `app.js`.
3. Referencia visual del sitio original, solo si hace falta: https://dmc.pe/
4. Considera únicamente la carpeta `frontend/` como punto de partida. El backend nuevo va en `backend/` (ver sección 5).
5. No modifiques los estilos existentes (`dmc.css`, `styles.css`, `enhancements.css`, `fine-tuning.css`). Los estilos nuevos van en `frontend/solution.css` y reutilizan las variables de `dmc.css` (`--blue`, `--cyan`, `--heading`, `--muted`, `--border`, `--page`), la fuente Poppins y Font Awesome 6.5.2, que ya se cargan.
6. Haz commits pequeños y descriptivos en español (por ejemplo: `feat(backend): motor de asistencia efectiva`).

---

## 1. Contexto y problema

- DMC Institute dicta cursos online en vivo por Zoom para profesionales, principalmente de ingeniería y de 25 a 35 años. Las clases son de noche (en el ejemplo del repo: martes y jueves de 7:30 p. m. a 10:30 p. m.), con 2 sesiones por semana.
- **Deserción:** ~15 % mensual de abandono completo, como promedio de todos los programas. Los programas cortos (2 meses) casi no tienen deserción; en los largos (más de 3 meses) el abandono se concentra entre el 4.º y el 5.º mes.
- **Causa principal:** falta de tiempo (nuevo empleo, cambio de horario), no falta de interés. Con horario fijo y asistencia mínima obligatoria, el alumno que cambia de horario no tiene otra salida que abandonar.
- **Las encuestas no sirven para diagnosticar:** no se responden, las escalas del 1 al 5 o del 1 al 10 son engañosas, y quien abandona no contesta después. Lo que más valor tiene son las respuestas abiertas.
- **Datos:** Zoom es de pago y graba en la nube (grabación, transcripción, chat y reporte de participantes). Moodle no registra quién ve las grabaciones. DMC está abierta a cambiar de plataforma.
- **Requisito actual de certificado** (página del curso): nota ≥ 14, 80 % de asistencia, encuesta final y pagos al día.

## 2. Qué resuelve la solución (cuatro pilares)

| # | Pilar | Qué hace |
|---|---|---|
| 1 | **Chat dentro de las grabaciones** | Vista de clase con video, capítulos, resumen, «Lo que te perdiste», chat que responde solo con la transcripción (citando el minuto), quiz de la sesión y micro-preguntas abiertas. |
| 2 | **Asistencia efectiva** | Una sesión cuenta como **cumplida** si el alumno asistió en vivo **o** la recuperó: dentro de 7 días vio los **tramos clave** en la vista con chat y aprobó el **quiz de esa sesión**. El certificado exige un % de sesiones cumplidas, configurable por curso. |
| 3 | **Diagnóstico y rescate** | Calcula variables de conducta real (Zoom, plataforma y chat), las compara con el patrón propio de cada alumno y lo clasifica en 4 estados. Si pasa a «en riesgo», se activa el rescate en 2 niveles. |
| 4 | **Plataforma web** | Portal del alumno («Mi avance», «Grabaciones», «Clase») y tablero del tutor, sobre el frontend existente. |

Decisiones ya tomadas por DMC (no las cambies):
- El plazo para recuperar una sesión es de **7 días** desde la fecha de la clase.
- **No hay un mínimo de sesiones en vivo.** Se mide qué pasa (ver indicadores en la sección 9).
- Rescate en **2 niveles**: (1) aviso automático; (2) si no hay respuesta ni actividad en 48 h, contacto de una persona. **Esa persona es el Asistente Académico del curso** (confirmado por el equipo; en el curso de ejemplo, el que figura en `course-detail.html`). Se guarda en `course.academic_assistant_id`.
- El umbral de asistencia es **configurable por curso**. El valor por defecto es 80 %, igual que la web actual.

## 3. Alcance del MVP

El MVP debe funcionar **sin datos reales de DMC**. Usa dos fuentes de datos de prueba:

1. **Sesiones simuladas reales:** el equipo graba 1 o 2 clases en Zoom y coloca las exportaciones en `backend/data/zoom/<session_id>/` (ver formatos en la sección 6.1).
2. **Cohorte sintética:** un script genera ~30 alumnos × 52 sesiones (2 por semana durante 6 meses) con perfiles conocidos, en el mismo formato que exporta Zoom, más eventos de plataforma. Sirve para probar y medir el diagnóstico (sección 6.6).

Fuera del MVP: bot propio dentro de Zoom, conexión con la API de Zoom de DMC, reemplazo de Moodle, envío real de WhatsApp o correo (en el MVP se simulan con una bandeja de salida), pausa o cambio de cohorte como trámite real (solo se registra la solicitud).

## 4. Arquitectura

```
Zoom (exportaciones)      Plataforma web (eventos)      Chat de grabaciones
  participantes CSV         heartbeats de actividad        preguntas y respuestas
  chat TXT                  progreso del video             quiz y micro-preguntas
  transcripción VTT         baja asistida
  grabación MP4
          \                         |                          /
           +------------> Backend FastAPI + SQLite <----------+
                          - Ingesta y modelo de datos
                          - Motor de asistencia efectiva
                          - Motor de diagnóstico (variables + estados)
                          - Rescate (alertas nivel 1 y 2)
                          - Servicio LLM (resumen, capítulos, quiz, chat, clasificación)
                                   |
                     API REST JSON  (/api/...)
                                   |
             Frontend HTML/CSS/JS existente + páginas nuevas
             - Alumno: Mi avance, Grabaciones, Clase, baja asistida
             - Tutor: Seguimiento (tablero), validación de tramos clave
```

El backend sirve también los archivos de `frontend/` como estáticos, así todo corre en un solo puerto y no hay problemas de CORS.

## 5. Tecnologías

| Capa | Tecnología | Motivo |
|---|---|---|
| Frontend | HTML + CSS + JavaScript vanilla (ES modules), sobre `frontend/` | Ya está diseñado; no se introduce framework. |
| Gráficos (tablero) | Chart.js 4.4.x por CDN (cdnjs) | Barras de avance y tendencias simples. |
| Backend | Python 3.11+, **FastAPI**, Uvicorn | Datos, reglas y LLM en el mismo lenguaje. |
| Base de datos | **SQLite** con SQLModel (o SQLAlchemy 2) | Cero configuración; migrable a PostgreSQL después. |
| Procesamiento | pandas, `webvtt-py` (transcripciones), `python-dateutil` | Lectura de las exportaciones de Zoom y cálculo de variables. |
| Búsqueda en transcripción | `rank-bm25` (solo si la transcripción no cabe en el contexto del modelo) | Recuperación simple, sin base vectorial. |
| LLM | SDK `openai` de Python apuntando a un proveedor compatible con OpenAI (Kimi por defecto) | Un solo cliente; el proveedor se cambia por variables de entorno. |
| Transcripción (plan B) | `faster-whisper` en local, o Whisper vía Groq | Por si una grabación no trae VTT de Zoom. |
| Datos sintéticos | Faker (`es_ES`), numpy | Nombres y comportamiento de la cohorte. |
| Tareas periódicas | Endpoint `POST /api/admin/recompute` + APScheduler (cada hora) | Recalcular estados y alertas. |
| Pruebas | pytest | Reglas de asistencia, estados y alertas. |
| Configuración | `python-dotenv` + `.env` (no se sube; incluir `.env.example`) | Llaves y parámetros. |

### 5.1 Proveedor de LLM

Usa una sola capa `backend/app/services/llm.py` con el cliente `openai.OpenAI(base_url=..., api_key=...)` y estas variables:

```env
LLM_BASE_URL=https://api.moonshot.ai/v1
LLM_API_KEY=...
LLM_MODEL_CHAT=kimi-k2.6
LLM_MODEL_FAST=kimi-k2.6
```

- **Opción por defecto: Kimi (Moonshot).** Compatible con OpenAI y con contexto largo: una transcripción completa de 3 horas cabe entera en el prompt, sin base vectorial. Verifica en https://platform.moonshot.ai el nombre exacto del modelo disponible para tu cuenta: los nombres cambian (por ejemplo, `kimi-k2.5` cierra para usuarios nuevos).
- **Alternativas compatibles** (solo cambia las 3 variables):
  - **Groq** (`https://api.groq.com/openai/v1`): tiene capa gratuita y es muy rápido. Útil para `LLM_MODEL_FAST` (clasificación) y para transcribir con Whisper.
  - **Google Gemini** (endpoint compatible con OpenAI: `https://generativelanguage.googleapis.com/v1beta/openai/`): la capa gratuita incluye los modelos Flash.
  - **OpenRouter** (`https://openrouter.ai/api/v1`): da acceso a modelos marcados como gratuitos.
- **Regla de costo:** el resumen, los capítulos, los tramos clave y el quiz se generan **una sola vez por sesión** (script `scripts/process_session.py`) y se guardan en la base. En línea solo corren el chat y la clasificación de micro-preguntas.
- Todas las respuestas estructuradas se piden en **JSON** y se validan con Pydantic. Si falla la validación, se reintenta una vez y luego se registra el error.

## 6. Funcionamiento detallado

### 6.1 Ingesta de Zoom

Coloca los archivos en `backend/data/zoom/<session_id>/`. El parser debe ser tolerante: mapear columnas por nombre e ignorar las que no conoce.

| Archivo | Contenido | Uso |
|---|---|---|
| `participants.csv` (reporte de participantes) | Nombre, correo, hora de entrada, hora de salida, duración. Un alumno que sale y vuelve aparece en **varias filas**. | Asistencia, retraso, salida anticipada, reingresos |
| `chat.txt` | Una línea por mensaje: hora, autor, destinatario y texto. El formato varía según la versión de Zoom; soporta al menos `HH:MM:SS\t De Nombre : texto` y `HH:MM:SS De Nombre a Todos: texto`. | Mensajes por alumno |
| `transcript.vtt` | Bloques con tiempo y `Nombre: texto` | Contexto del chat, intervenciones por voz, capítulos |
| `recording.mp4` | Video | Reproductor de la vista de clase |

- **Identidad:** el alumno se vincula por **correo**. Si no hay correo, se usa el nombre normalizado (sin tildes, en minúsculas) y la fila queda marcada `match_confidence=low`.
- **Intervalos:** las filas del mismo alumno se unen en intervalos; un corte menor a 2 minutos se considera **problema de conexión**, no salida.
- **Voz:** se cuenta un turno por cada bloque del VTT cuyo hablante coincida con el alumno.

### 6.2 Procesamiento de cada sesión con el LLM (una vez)

`scripts/process_session.py <session_id>` genera y guarda:

1. **Capítulos:** lista `{start, end, title, summary}` de 5 a 12 capítulos que cubren toda la clase.
2. **Tramos clave:** marca `is_key=true` en los capítulos esenciales (entre 30 % y 50 % de la duración). El docente o tutor los **valida** en el tablero (`validated=true`). Mientras no estén validados, se usan igual, con una etiqueta que lo indique.
3. **Resumen** de unos 10 minutos de lectura, con referencias `[mm:ss]`.
4. **Banco de quiz:** 8 preguntas de opción múltiple (4 alternativas, una correcta, explicación breve), generadas a partir de los tramos clave. En cada intento se toman 5 al azar y se mezclan las alternativas.

### 6.3 Pilar 1 – Vista de clase con chat (`frontend/recording.html?session=<id>`)

Distribución en escritorio: el video y los capítulos a la izquierda (60 %) y un panel con pestañas a la derecha (40 %). En celular, todo apilado. Pestañas del panel:

- **Resumen:** el resumen con marcas de tiempo; al hacer clic en `[mm:ss]` el video salta a ese minuto.
- **Lo que te perdiste:** solo aparece si el alumno tuvo minutos ausentes en vivo. Usa los intervalos de Zoom para listar los capítulos que se superponen con su ausencia, cada uno con su resumen y un botón para ir a ese tramo.
- **Chat:**
  - El system prompt (en español) indica responder **solo** con la transcripción y los capítulos de esa sesión, citar `[mm:ss]` y, si la respuesta no está, decir: «Eso no se vio en esta clase. Puedes consultarlo con el docente o en el grupo del curso.»
  - Contexto: capítulos + transcripción completa. Si supera el 70 % de la ventana de contexto del modelo, se envían solo los 8 fragmentos más relevantes según BM25.
  - Historial: las últimas 6 interacciones de esa sesión.
  - Cada pregunta del alumno se clasifica como `contenido`, `logistica` u `otro` (modelo rápido, salida JSON) y se guarda.
- **Quiz:** 5 preguntas. Se aprueba con 3 o más correctas (valor configurable por curso). Al terminar, muestra resultado y explicaciones. Intentos ilimitados; se guarda cada intento y vale el mejor.

**Seguimiento del video** (es la fuente de la actividad asíncrona, porque Moodle no la registra):
- Con el evento `timeupdate` del `<video>`, acumula los rangos de segundos realmente reproducidos. Cada 15 s, y al salir de la página, los envía a `POST /api/watch` (`{session_id, ranges:[[s,e],...]}`).
- El backend une los rangos y calcula la **cobertura de tramos clave** = segundos vistos dentro de los tramos clave ÷ duración total de los tramos clave.

**Micro-pregunta abierta:**
- Cada 14 días por alumno y por curso, después de terminar un quiz, el chat hace una sola pregunta abierta. Rota entre: «¿Cómo te está acomodando el horario del curso estas semanas?», «¿Qué es lo que más te está costando del curso ahora?» y «¿Hay algo que te haría más fácil seguir el ritmo?».
- La respuesta es opcional. Se clasifica con el LLM en JSON: `{topic: tiempo|trabajo|contenido|ritmo|satisfaccion|otro, sentiment: positivo|neutro|negativo, summary}`.

**Banner de estado** sobre el video, por ejemplo: «Sesión 12 – No asististe en vivo. Para que cuente: ve los tramos clave (45 %) y aprueba el quiz. Vence el jueves 22 de octubre.»

### 6.4 Pilar 2 – Motor de asistencia efectiva

Parámetros por curso (tabla `course`, con estos valores por defecto):

| Parámetro | Defecto | Descripción |
|---|---|---|
| `attendance_threshold` | 0.80 | Porcentaje de sesiones cumplidas para certificar |
| `live_min_ratio` | 0.70 | Porcentaje de la duración de la clase conectado para contar como asistencia en vivo |
| `recovery_days` | 7 | Días para recuperar |
| `key_coverage_min` | 0.90 | Cobertura mínima de tramos clave |
| `quiz_pass_score` | 3 (de 5) | Nota mínima del quiz |
| `level2_role` | `asistente_academico` | Quién hace el contacto del nivel 2 |

Estado de cada par alumno–sesión (`session_compliance`):
- `en_vivo`: minutos conectados (unión de intervalos) ÷ duración de la clase ≥ `live_min_ratio`.
- `recuperada`: no fue en vivo, pero **dentro de** `fecha_clase + recovery_days` alcanzó cobertura de tramos clave ≥ `key_coverage_min` **y** un intento de quiz con nota ≥ `quiz_pass_score`. La fecha que cuenta es la del último de esos dos hitos.
- `por_recuperar`: no fue en vivo y el plazo sigue vigente.
- `no_cumplida`: no fue en vivo y el plazo venció sin recuperarla.

Indicadores por alumno:
- **Asistencia efectiva** = (en_vivo + recuperada) ÷ sesiones ya dictadas.
- **Proyección al cierre** = (cumplidas + sesiones_restantes × tasa de cumplimiento de las últimas 4 sesiones) ÷ total de sesiones del programa.
- **Sesiones que le faltan** para el umbral = máx(0, ⌈umbral × total⌉ − cumplidas).
- **Certificable** = asistencia efectiva final ≥ umbral **y** nota ≥ 14 **y** proyecto aprobado. La encuesta y los pagos quedan fuera del MVP; mostrarlos como «pendiente de verificar».

### 6.5 Pilar 3 – Variables de diagnóstico

Calcula estas variables por alumno, curso y sesión (o semana) y guárdalas en `student_features`:

| Fuente | Variable | Cálculo |
|---|---|---|
| Zoom | `live_ratio` | minutos conectados ÷ duración |
| Zoom | `late_min` | máx(0, primera entrada − inicio) |
| Zoom | `early_leave_min` | máx(0, fin − última salida) |
| Zoom | `reconnects`, `gap_min` | cortes menores a 2 min (conexión) y salidas reales |
| Zoom | `chat_msgs`, `voice_turns` | conteos por sesión |
| Plataforma | `active_min_week` | suma de los heartbeats activos (ver 6.8) |
| Plataforma | `days_since_activity` | días desde la última actividad (en vivo, video, chat o quiz) |
| Plataforma | `study_hour_mode` | hora habitual de estudio asíncrono; un cambio sugiere nuevo horario |
| Plataforma | `key_coverage` | por sesión, de 6.3 |
| Chat | `questions_content`, `questions_logistics` | conteos |
| Chat | `quiz_score`, `quiz_trend` | nota y pendiente de las últimas 4 |
| Chat | `micro_topic`, `micro_sentiment` | de la última micro-pregunta |
| Derivada | `pattern_drop` | promedio de `live_ratio` de las últimas 3 sesiones < 0.7 × promedio de sus sesiones anteriores (requiere 4 o más sesiones previas) |
| Derivada | `late_shift` | `late_min` + `early_leave_min` de las últimas 3 sesiones > el anterior + 20 min |
| Derivada | `mode_shift` | 2 o más de las últimas 3 sesiones `recuperada` tras un historial mayormente `en_vivo` |
| Derivada | `projection` | de 6.4 |

**Estados** (después de cada sesión y en cada recálculo), evaluados en este orden:
1. **inactivo:** `days_since_activity` ≥ 14.
2. **en_riesgo:** se cumple al menos una de estas: `projection` < umbral; 2 sesiones seguidas `no_cumplida`; `pattern_drop` o `late_shift` **sin** recuperar lo perdido; `quiz_trend` negativo con nota < aprobatoria; micro-pregunta con `topic ∈ {tiempo, trabajo}` y `sentiment = negativo`.
3. **en_adaptacion:** `mode_shift` verdadero y `projection` ≥ umbral.
4. **activo:** en cualquier otro caso.

Cada estado guarda sus **motivos** en lenguaje claro (por ejemplo: «Llega 35 min tarde en las últimas 3 clases», «Proyección 72 % < 80 %»). Son el «motivo probable» que ve el tutor.

> Los umbrales son valores iniciales; ponlos en `backend/app/config.py` para recalibrarlos con datos reales.

### 6.6 Cohorte sintética y evaluación

`scripts/generate_cohort.py --students 30 --weeks 26 --seed 42` genera las exportaciones tipo Zoom (CSV/TXT) de las 52 sesiones (martes y jueves, de 19:30 a 22:30), además de eventos de plataforma, video, quiz y micro-preguntas. Perfiles plantados, con su etiqueta guardada en `ground_truth.csv`:

| Perfil | Cantidad | Comportamiento |
|---|---|---|
| estable | 12 | ~90 % en vivo, puntual, participa |
| cambio_horario | 6 | Desde el mes 3 llega tarde y sale antes, no recupera y abandona en el mes 4–5 |
| adaptado | 5 | Desde el mes 3 pasa a recuperar las clases y cumple a tiempo |
| mala_conexion | 4 | Muchos cortes menores a 2 min, pero asistencia completa |
| desinteres | 3 | Baja gradual de participación y quizzes; abandona en el mes 5 |

`scripts/evaluate.py` recorre las sesiones en orden y reporta:
- **Recall:** % de desertores marcados `en_riesgo` antes de abandonar. Meta: ≥ 80 %, con 2 semanas o más de anticipación.
- **Falsas alarmas:** % de `estable` y `mala_conexion` marcados `en_riesgo`. Meta: ≤ 20 %.
- % de `adaptado` clasificados como `en_adaptacion` (no `en_riesgo`).

### 6.7 Rescate en 2 niveles (`alerts`)

- **Nivel 1 (automático):** al pasar a `en_riesgo`, se crea una alerta de nivel 1 y un mensaje en `outbox` (canal `portal`; también `email` y `whatsapp` simulados). El mensaje es concreto y se genera con plantilla, sin LLM: «Tienes {n} clases por recuperar; la primera vence el {fecha}. Recuperar una clase toma unos 20 minutos.» Aparece en la campana (`.top-icon` de notificaciones) del portal.
- **Nivel 2 (humano):** si en 48 h no hay actividad nueva, o si el alumno pasa a `inactivo`, la alerta escala a nivel 2 y entra en la cola del tablero del rol `level2_role` del curso. La persona registra: fecha de contacto, canal, si respondió, motivo (`tiempo`, `trabajo`, `contenido`, `economico`, `salud`, `otro`), acción ofrecida (`recuperar`, `pausa`, `otra_cohorte`, `ninguna`) y resultado.
- Una alerta se cierra cuando el alumno vuelve a `activo` o `en_adaptacion`, o cuando el tutor la cierra con un resultado.

### 6.8 Actividad en la plataforma

- `frontend/js/tracker.js`, incluido en todas las páginas, envía `POST /api/heartbeat` cada 30 s **solo si** la pestaña está visible (`document.visibilityState`) **y** hubo interacción (mouse, teclado, scroll o video reproduciéndose) en los últimos 60 s. Payload: `{course_id, page, active_seconds}`.
- Esto mide **tiempo activo**, no tiempo con la pestaña abierta.

### 6.9 Baja asistida

- En la página del curso, botón discreto «Solicitar retiro del curso». Abre un modal que pide el motivo con un clic (mismas opciones del nivel 2) y un comentario opcional, y **antes de confirmar** ofrece: recuperar con grabaciones, pausar y retomar en otra cohorte, o hablar con el Asistente Académico.
- Se guarda en `withdrawal_requests` y crea una alerta de nivel 2 inmediata.

### 6.10 Consentimiento y privacidad (Ley 29733)

- En el primer ingreso, un modal explica qué se mide (asistencia y participación en Zoom, tiempo activo, avance de las grabaciones, preguntas al chat, quizzes) y para qué (ayudar al alumno a certificarse), con la opción de aceptar. Se guarda en `consents`.
- El tablero del tutor solo es visible para los roles `tutor`, `asistente_academico`, `docente` y `coordinador`.
- El alumno ve sus propios datos en «Mi avance».
- No se guardan grabaciones ni transcripciones fuera de `backend/data/` y no se suben al repositorio (agregar a `.gitignore`).

## 7. Páginas del frontend

Todas reutilizan el header, el sidebar y el footer existentes. Para el MVP, el login (`index.html`) agrega dos accesos de demostración: «Entrar como alumno» y «Entrar como tutor». Cada uno guarda el rol y el `user_id` en `localStorage`.

| Archivo | Rol | Contenido |
|---|---|---|
| `course-detail.html` (modificar) | Alumno | Reemplazar «Progreso general % 0» por la **asistencia efectiva** (barra con marca del umbral). La tarjeta «02 - Grabaciones» lleva a `recordings.html`. Agregar el botón de baja asistida. |
| `recordings.html` (nuevo) | Alumno | Lista de sesiones con estado: ✅ En vivo · ✅ Recuperada · ⏳ Por recuperar (vence dd/mm) · ❌ No cumplida; botón «Ver clase». |
| `recording.html` (nuevo) | Alumno | Vista de clase (6.3). |
| `progress.html` (nuevo, «Mi avance») | Alumno | Asistencia efectiva frente al umbral, proyección, «Te faltan N sesiones», clases por recuperar con plazo, notas de quiz y requisitos del certificado. Agregar al sidebar debajo de «Área personal». |
| `tutor.html` (nuevo, «Seguimiento») | Tutor y roles afines | Contadores por estado; tabla de alumnos (estado con color, motivos, asistencia efectiva, proyección, última actividad); cola de nivel 2 con formulario de contacto; detalle del alumno con su línea de tiempo (sesiones, quizzes, micro-preguntas, alertas); pestaña «Tramos clave» para validar los capítulos de cada sesión. Solo aparece en el sidebar para ese rol. |
| Campana de notificaciones | Alumno | Mensajes de nivel 1 desde `GET /api/me/notifications`. |
| `js/api.js`, `js/tracker.js`, `js/*.js` | — | Cliente de la API, heartbeat y lógica de cada página (ES modules). |

Colores de estado: activo = verde, en adaptación = azul (`--blue`), en riesgo = ámbar, inactivo = rojo. Diseño responsive: el alumno debe poder recuperar una clase desde el celular.

## 8. API REST (prefijo `/api`)

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/auth/demo` | `{role}` → usuario de demostración |
| GET | `/me/courses` | Cursos del usuario |
| GET | `/courses/{id}/progress` | Asistencia efectiva, proyección, faltantes, requisitos |
| GET | `/courses/{id}/sessions` | Sesiones con el estado de cumplimiento del alumno |
| GET | `/sessions/{id}` | Datos de la sesión: video, capítulos, resumen, minutos ausentes del alumno, banner |
| POST | `/sessions/{id}/chat` | `{message}` → `{answer, citations:[secs]}` |
| GET | `/sessions/{id}/quiz` | 5 preguntas (sin respuestas) |
| POST | `/sessions/{id}/quiz` | Respuestas → nota, explicaciones y nuevo estado de cumplimiento |
| POST | `/watch` | Rangos vistos |
| POST | `/heartbeat` | Tiempo activo |
| GET/POST | `/me/micro-question` | Micro-pregunta pendiente / respuesta |
| GET | `/me/notifications` | Mensajes de nivel 1 |
| POST | `/courses/{id}/withdrawal` | Baja asistida |
| POST | `/consent` | Consentimiento |
| GET | `/tutor/courses/{id}/students` | Tabla del tablero |
| GET | `/tutor/students/{uid}/timeline?course_id=` | Detalle del alumno |
| GET | `/tutor/alerts?level=2` | Cola de nivel 2 |
| POST | `/tutor/alerts/{id}/contact` | Registrar contacto |
| PATCH | `/tutor/sessions/{id}/chapters` | Validar o editar tramos clave |
| POST | `/admin/ingest/{session_id}` | Ingesta de exportaciones de Zoom |
| POST | `/admin/recompute` | Recalcular cumplimiento, variables, estados y alertas |

## 9. Modelo de datos (SQLite)

`user(id, name, email, role)` · `course(id, name, start_date, end_date, total_sessions, schedule, teacher_id, academic_assistant_id, attendance_threshold, live_min_ratio, recovery_days, key_coverage_min, quiz_pass_score, level2_role)` · `enrollment(user_id, course_id, status)` · `session(id, course_id, number, starts_at, ends_at, video_path, transcript_path, processed)` · `transcript_segment(session_id, start, end, speaker, text)` · `chapter(session_id, start, end, title, summary, is_key, validated)` · `session_summary(session_id, text)` · `zoom_interval(session_id, user_id, join_at, leave_at, match_confidence)` · `zoom_chat(session_id, user_id, at, text)` · `watch_range(user_id, session_id, start_s, end_s, at)` · `heartbeat(user_id, course_id, page, active_seconds, at)` · `chat_message(user_id, session_id, role, text, kind, at)` · `quiz_item(session_id, question, options_json, answer_idx, explanation)` · `quiz_attempt(user_id, session_id, score, answers_json, at)` · `micro_answer(user_id, course_id, question, answer, topic, sentiment, summary, at)` · `session_compliance(user_id, session_id, status, completed_at, deadline)` · `student_features(user_id, course_id, session_id, …variables de 6.5)` · `risk_snapshot(user_id, course_id, at, state, reasons_json, projection)` · `alert(id, user_id, course_id, level, status, created_at, escalated_at, assigned_role, contact_json)` · `outbox(user_id, channel, text, at, read)` · `withdrawal_request(user_id, course_id, reason, comment, offered_json, outcome, at)` · `consent(user_id, version, at)`.

**Indicadores a vigilar por no exigir un mínimo en vivo** (calcular en `GET /tutor/courses/{id}/stats` y mostrar en el tablero): proporción de sesiones en vivo frente a recuperadas por semana, nota media del quiz en cada modalidad y participación en vivo (`chat_msgs` + `voice_turns`) por semana.

## 10. Estructura de carpetas (rama `mateo`)

```
frontend/                  # existente + páginas nuevas
  recordings.html  recording.html  progress.html  tutor.html
  solution.css
  js/api.js  js/tracker.js  js/recording.js  js/progress.js  js/tutor.js  js/notifications.js
backend/
  app/main.py              # FastAPI + StaticFiles(frontend)
  app/config.py            # umbrales y parámetros
  app/db.py  app/models.py
  app/routers/             # auth, student, sessions, tutor, admin
  app/services/llm.py      # cliente compatible con OpenAI
  app/services/zoom_ingest.py
  app/services/attendance.py
  app/services/features.py
  app/services/risk.py     # estados + motivos
  app/services/alerts.py
  app/prompts/             # prompts en español (.md)
  scripts/generate_cohort.py  scripts/process_session.py  scripts/evaluate.py  scripts/seed_demo.py
  data/                    # en .gitignore (salvo data/sample/)
  tests/
  requirements.txt  .env.example  README.md
docs/DECISIONES.md
```

## 11. Orden de implementación

1. **Base:** crear la rama `mateo`; esqueleto de FastAPI que sirve `frontend/`; modelos y `seed_demo.py` (1 curso con 52 sesiones, 30 alumnos, 1 tutor, 1 asistente académico).
2. **Datos:** `zoom_ingest.py` (tolerante) + `generate_cohort.py` + `ground_truth.csv`.
3. **Asistencia efectiva:** `attendance.py` + pruebas (`en_vivo`, `recuperada` dentro y fuera de plazo, cálculo de la proyección).
4. **Diagnóstico:** `features.py` + `risk.py` + `evaluate.py`; ajustar umbrales hasta cumplir las metas de 6.6.
5. **LLM:** `llm.py`, `process_session.py` (capítulos, tramos clave, resumen, quiz) sobre la sesión simulada real.
6. **Vista de clase:** `recording.html` (video, capítulos, pestañas, chat, quiz, seguimiento del video, micro-pregunta, banner).
7. **Portal del alumno:** `recordings.html`, `progress.html`, cambios en `course-detail.html`, campana, baja asistida, consentimiento.
8. **Tablero del tutor:** `tutor.html` con estados, motivos, cola de nivel 2, detalle y validación de tramos clave.
9. **Alertas:** nivel 1, escalamiento a 48 h y recálculo programado.
10. **Cierre:** README con instrucciones de ejecución, `.env.example` y una demo guiada (ver 12).

## 12. Criterios de aceptación

- `uvicorn app.main:app --reload` (desde `backend/`) levanta todo en `http://localhost:8000`, y las páginas existentes se ven igual que en `main`.
- El chat responde correctamente y con el minuto citado en al menos 16 de 20 preguntas sobre la sesión simulada, y responde «Eso no se vio en esta clase…» cuando corresponde.
- «Lo que te perdiste» lista exactamente los capítulos que se superponen con la ausencia del alumno.
- La asistencia efectiva coincide con un cálculo manual para 5 alumnos de la cohorte, incluido un caso recuperado fuera de plazo.
- `evaluate.py` cumple: recall ≥ 80 % con ≥ 2 semanas de anticipación; falsas alarmas ≤ 20 %; `adaptado` no queda `en_riesgo`.
- Una alerta de nivel 1 se escala a nivel 2 a las 48 h sin actividad (probar con un reloj simulado), y el tutor puede registrar el contacto.
- Un usuario puede, desde el celular, ver cuánto le falta para certificarse y recuperar una clase en 20 minutos o menos.
- `pytest` pasa.

## 13. Demo guiada (para la presentación)

1. Entrar como alumno «cambio_horario» en el mes 3 → «Mi avance» muestra una proyección de 74 % < 80 % y 2 clases por recuperar; la campana tiene el aviso.
2. Abrir la clase → «Lo que te perdiste», preguntar algo en el chat, ver los tramos clave y aprobar el quiz → la sesión pasa a «Recuperada» y la proyección sube.
3. Entrar como tutor → tablero con estados y motivos; cola de nivel 2; registrar un contacto; validar los tramos clave de una sesión.
