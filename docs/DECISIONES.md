# Decisiones del MVP (rama `mateo`)

Referencia: [`SOLUCION_DMC_CURSOR.md`](SOLUCION_DMC_CURSOR.md) (v1) y [`SOLUCION_DMC_CURSOR_V2.md`](SOLUCION_DMC_CURSOR_V2.md) (v2). Este documento registra las elecciones que no salen literalmente de la especificación o que conviene dejar explícitas para la demo y el mantenimiento.

# Versión 2

## Sesión real de prueba

- Los archivos de la sesión real (video, `TRANSCRIPCION.txt` y materiales del docente) viven fuera del repositorio, en `propuesta/Sesion 01`. `scripts/ingest_real_session.py "<carpeta>" --session 23` los normaliza en `backend/data/zoom/23/` (ignorado por Git).
- **Ubicación en la demo:** el contenido real se carga en la **sesión 23** del curso de demo, la que el alumno «cambio_horario» tiene pendiente de recuperar en el mes 3 (vence el martes 13 de octubre), con su título real. Las diapositivas dicen «Sesión 01» porque es la primera clase de la edición real; se aceptó esa diferencia para que todos los casos de la demo (recuperación, chat, quiz, tablero) usen la misma sesión real. Las demás sesiones conservan el contenido de muestra con el título «Sesión N – contenido de muestra».
- **Duración:** la sesión 23 dura lo que dura la grabación real (~3 h 30 min) en lugar de las 3 h del horario.
- **Video:** no se copia (632 MB): `metadata.json` guarda la ruta local y el backend lo sirve en `/media/23.mp4` con rangos HTTP. Sin Vercel Blob, la demo publicada no tiene video y usa el reproductor de transcripción con el texto real.
- **Transcripción:** el `.txt` real trae una línea de tiempo (`m:ss` o `h:mm:ss`) seguida del texto, sin nombre del hablante, en el 100 % de los bloques; por eso no hizo falta alinear con Whisper. `scripts/align_transcript.py` queda para sesiones futuras sin marcas de tiempo.
- **Diapositivas de logística:** docente, perfil, acuerdos, agenda y «gracias» nunca son tramo clave.
- **Historial de la cohorte en la sesión 23:** la cohorte sintética se generó con clases de 3 h. Al cargar la sesión real (3 h 30 min), el seed escala sus rangos vistos en la misma proporción, para que «vio la grabación completa» siga significando lo mismo. Las asistencias en vivo (Zoom) no cambian.

## Capítulos y tramos clave sin LLM

- `app/services/chaptering.py`: ventanas de 60 s; BM25 de cada ventana contra cada diapositiva, suavizado en 3 ventanas y **alineación monótona por programación dinámica** (la mejor secuencia de diapositivas que avanza en orden, con una penalización por cambio). Se eligió en lugar de «diapositiva más parecida por ventana» porque la transcripción automática es ruidosa («M8» por n8n) y una coincidencia aislada con una diapositiva lejana rompía el orden. Un repaso (volver a una diapositiva anterior) se acepta si dura 2 min o más.
- Al unir capítulos para llegar a 12 como máximo, se unen primero pares vecinos del mismo tipo (logística con logística, contenido con contenido), así la bienvenida no queda mezclada con contenido.
- Sin marca del docente en `key_slides.json`, los tramos clave son los capítulos con mayor densidad de términos propios del material (los que aparecen en pocas diapositivas), hasta sumar 30–45 % de la clase. En la sesión real: 12 capítulos, 37 %. El tablero avisa si superan el 40 %.
- **Sesiones de muestra:** conservan los capítulos de la referencia `data/sample/session_demo/processed.json` (revisados por el equipo, fuente «muestra»), porque el historial simulado de la cohorte se generó contra esos tramos clave; cambiarlos alteraría los cumplimientos ya calculados. El algoritmo se prueba también sobre la transcripción de muestra (`tests/test_chaptering.py`).
- `scripts/chapters_report.py 23` imprime la tabla de capítulos para revisar los límites contra el video.

## Quiz del docente

- **Banco de la sesión 23:** la sesión real no traía `quiz.json`. El equipo escribió 8 preguntas a partir de las diapositivas y la transcripción, cada una con su minuto real y su diapositiva, en `backend/data/zoom/23/quiz.json` (fuera de Git). 5 cargan aprobadas (fuente «docente») para que el alumno de la demo pueda aprobar el quiz, y 3 quedan en borrador como «propuesta IA» para mostrar en el tablero cómo el docente revisa y aprueba. **Está pendiente la revisión del docente real.**
- **Sesiones de muestra:** su banco de la referencia carga aprobado y con fuente «docente», para que el historial de la cohorte siga igual.
- La explicación completa se muestra cuando el alumno aprueba en ese intento o ya había aprobado antes; si no, cada pregunta fallada muestra solo «Revisa el minuto [mm:ss] (diapositiva n)».
- Editar una pregunta ya aprobada la mantiene aprobada y actualiza quién y cuándo la aprobó. No existe «aprobar todas».
- En modo simulado, «Proponer preguntas» arma borradores con las viñetas de las diapositivas de cada tramo clave sin preguntas; «Sugerir alternativas» toma viñetas de otros capítulos y «Revisar redacción» aplica reglas fijas (alternativas repetidas, correcta mucho más larga, negaciones, palabras de la respuesta en el enunciado). Con Kimi, las tres usan `prompts/quiz_assist.md`.

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

## Despliegue en Vercel

Se optó por un **snapshot SQLite** en lugar de una base gestionada: el build genera la base de demo y cada instancia la copia a `/tmp` al arrancar. Ventajas: cero servicios externos y costo nulo, y la demo se reinicia sola. Límites: los cambios no persisten entre instancias ni tras inactividad. Si el MVP pasa a piloto real, migrar a Postgres (Neon vía Vercel Marketplace) y reemplazar APScheduler por Vercel Cron sobre `/api/admin/recompute`. El proyecto se despliega con la CLI (sin integración Git) para no disparar despliegues desde otras ramas del repositorio compartido.

## Alcance fuera del MVP

Encuesta final y pagos aparecen en «Mi avance» como `pendiente_verificar`. El servicio `ai-service/` del repo original no se usa; toda la lógica de IA está en `backend/`.
