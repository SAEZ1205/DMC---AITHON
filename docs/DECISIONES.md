# Decisiones del MVP (rama `mateo`)

Referencia: [`SOLUCION_DMC_CURSOR.md`](SOLUCION_DMC_CURSOR.md) (v1) y [`SOLUCION_DMC_CURSOR_V2.md`](SOLUCION_DMC_CURSOR_V2.md) (v2). Este documento registra las elecciones que no salen literalmente de la especificación o que conviene dejar explícitas para la demo y el mantenimiento.

# Versión 2

## Sesión real de prueba

- Los archivos de la sesión real (video, `TRANSCRIPCION.txt` y materiales del docente) viven fuera del repositorio, en `propuesta/Sesion 01`. `scripts/ingest_real_session.py "<carpeta>" --session 23` los normaliza en `backend/data/zoom/23/` (ignorado por Git).
- **Ubicación en la demo:** el contenido real se carga en la **sesión 23** del curso de demo, la que el alumno «cambio_horario» tiene pendiente de recuperar en el mes 3 (vence el martes 13 de octubre), con su título real. Las diapositivas dicen «Sesión 01» porque es la primera clase de la edición real; se aceptó esa diferencia para que todos los casos de la demo (recuperación, chat, quiz, tablero) usen la misma sesión real. Las demás sesiones conservan el contenido de muestra con el título «Sesión N – contenido de muestra».
- **Duración:** la sesión 23 dura lo que dura la grabación real (~3 h 30 min) en lugar de las 3 h del horario.
- **Video:** no se copia (632 MB): `metadata.json` guarda la ruta local y el backend lo sirve en `/media/23.mp4` con rangos HTTP. Sin Vercel Blob, la demo publicada no tiene video y usa el reproductor de transcripción con el texto real (con capítulos, resumen, chat y quiz reales).
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
- En modo simulado, «Proponer preguntas» arma borradores con las viñetas de las diapositivas de cada tramo clave sin preguntas; «Sugerir alternativas» toma viñetas de otros capítulos y «Revisar redacción» aplica reglas fijas (alternativas repetidas, correcta mucho más larga, negaciones, palabras de la respuesta en el enunciado). Con el LLM (Groq), las tres usan `prompts/quiz_assist.md` y, si el LLM no responde o no hay cupo, caen a esa misma versión simulada.

## Chat: ruteo sin LLM

- `app/services/chat_router.py` clasifica cada mensaje con reglas de palabras (texto sin tildes, límites de palabra): **plataforma** (mi avance, plazos, quiz, asistencia, certificado), **logística** (horario, link de Zoom, fechas) o **contenido**. Solo el contenido pasa al tutor.
- Plataforma responde con los datos reales del alumno calculados por el motor de asistencia (plazo de la sesión, % de tramos clave vistos, quiz, otras sesiones pendientes, asistencia efectiva contra el umbral del certificado) y enlaza a «Mi avance». Logística responde con el horario del curso y enlaza al detalle del curso. Ninguna de las dos llama al LLM.
- El contador `questions_logistics` del diagnóstico suma logística y plataforma (las dos señalan dudas de organización, no de contenido).

## Chat tutor

- **Escalera de ayuda** guardada en `ChatThread`: 1 Ubicar (minuto y diapositiva), 2 Pista, 3 Paso guiado, 4 Explicación. Cada turno termina con una pregunta al alumno y cita al menos un minuto. Un intento fallido sube de nivel con `min(tope, 4, max(nivel + 1, intentos + 1))`; el botón «Ver explicación» aparece desde el nivel 3 o tras 2 intentos. Un intento correcto cierra el hilo y sugiere el siguiente tramo clave.
- **Evidencia determinista** (sin LLM y sin embeddings): BM25 sobre segmentos de la transcripción y unidades del material (diapositivas). Raíces de 6 letras para palabras de 7 o más letras (5 si no), sin palabras interrogativas; variantes por prefijo solo si su IDF está a menos de 2.5 del término. La cobertura de la pregunta se pondera por IDF.
- **Minuto citado:** ventanas de 20 s antes y 45 s después de cada candidato; puntaje = densidad BM25 × (0.5 + cobertura), ×1.5 si hay señal de definición («es un», «significa»…) y ×2 si repite la frase preguntada. Entre las ventanas con al menos el 75 % del mejor puntaje gana la más temprana (el docente suele definir antes de usar). Se prefiere el capítulo cercano a la diapositiva encontrada si su cobertura no es peor.
- **Fuera de la clase:** si la cobertura de la pregunta en la ventana más las 4 mejores diapositivas no llega a 0.6, se responde la frase fija «Eso no se vio en esta clase…» sin abrir hilo ni llamar al LLM.
- **Protección del quiz:** si la pregunta se parece (≥ 0.5, BM25 normalizado, con la transcripción como fondo) a una pregunta aprobada del quiz y el alumno aún no lo aprueba, el hilo queda topado en nivel 2. Además, si la respuesta contiene la alternativa correcta se reemplaza por una respuesta segura. Al aprobar el quiz el tope desaparece.
- **Límites:** 90 palabras (se recorta conservando la pregunta final); el LLM ve los últimos 4 mensajes del hilo.
- **Mock:** textos deterministas por nivel a partir de la evidencia (cita, viñetas de la diapositiva, frase de la transcripción). La evaluación simulada del intento compara términos del intento con la evidencia.
- **Uso del LLM:** solo tutor, micro-preguntas y asistencia del quiz. `llm.track()` cuenta llamadas (también en mock) y `eval_chat.py` las imprime.
- **Evaluación** (`scripts/eval_chat.py`, sesión 23, copia de la base): minuto a ±90 s **16/20**, primer turno con pregunta y ≤ 90 palabras **20/20**, quiz revelado **0/5**, fuera de la clase **5/5**. Las heurísticas se ajustaron mirando estas mismas 20 preguntas, así que 16/20 es optimista; hace falta un set nuevo de preguntas reales de alumnos para medirlo sin sesgo. El minuto esperado de «¿Qué es un RPA?» es 24:30, donde el docente lo define.
- **Se eliminó** `services/processing.py` y sus prompts (capítulos, resumen, quiz, clasificación): todo eso ahora es determinista o lo escribe el docente.
- **«Temas que más cuestan»** (`GET /api/tutor/sessions/{id}/hard-topics`): capítulos ordenados por hilos que llegaron a nivel 3 o 4. En la demo los hilos son **simulados**: el seed crea uno por cada pregunta de contenido de la cohorte (más 16 preguntas sobre la sesión real en la 23), con el capítulo según la evidencia y un nivel aleatorio con semilla fija.

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

## LLM (Groq · openai/gpt-oss-120b)

- Proveedor: **Groq**, plan gratuito, modelo `openai/gpt-oss-120b` (reemplaza a Kimi). Cliente OpenAI-compatible contra `LLM_BASE_URL` (`https://api.groq.com/openai/v1`); cambiar de proveedor sigue siendo solo configuración.
- La key va en `backend/.env` (local) y en la variable secreta `LLM_API_KEY` de Vercel (Producción). Nunca en Git.
- `LLM_MOCK=1`, o una API key vacía / `...`, activa respuestas simuladas (sin costo). Pytest fuerza mock.
- Prompts en español en `backend/app/prompts/`. Salida en modo JSON validada con Pydantic y un reintento. `reasoning_effort=low` y `include_reasoning=false`: gpt-oss razona antes de responder y esos tokens cuentan en el cupo.
- **Cupo del plan gratuito:** 30 solicitudes/min, 1.000/día, 8.000 tokens/min y 200.000/día. El límite que manda es el de tokens por minuto: un turno del tutor gasta ~1.500–2.000 tokens.
- **Para que la demo no se caiga** (`services/llm.py`):
  - Control de cupo propio al 85 % de cada límite. Los tokens por minuto se modelan como un balde que se recarga de forma continua (como Groq); se reserva prompt estimado + `max_tokens` y se ajusta con los tokens reales y con `x-ratelimit-remaining-tokens`.
  - `max_tokens` por tipo de salida (tutor 1024, micro 400, propuesta de quiz 3000, alternativas 800, revisión 1000), timeout de 25 s y sin reintentos automáticos del SDK.
  - Sin cupo en 4 s, ante un 429 (pausa lo que diga `retry-after`) o ante error/timeout, el servicio responde con su simulación. El tutor, las micro-preguntas y las tres acciones del quiz ya no devuelven 502.
  - Responder un intento hace **una** llamada (evaluación + ayuda del nivel siguiente); antes eran dos.
  - Contexto del tutor recortado a 6 fragmentos (los más cercanos al minuto citado), 3 diapositivas de 50 palabras y 4 mensajes de historial: ~1K tokens.
  - Caché de respuestas validadas por prompt exacto (6 h, 256 entradas).
  - `GET /api/admin/llm` (staff) muestra modo, cupo, uso del día y respuestas que salieron de la simulación.
- **Límite conocido:** el control de cupo es por instancia; con varias instancias de Vercel los encabezados de Groq lo corrigen y el 429 queda como red de seguridad. Con muchas personas usando el chat al mismo tiempo, parte de las respuestas saldrá de la simulación (sin errores).
- Prueba con la key real (sesión 23, servidor local): pregunta → pista → intento incorrecto → intento correcto, 4 respuestas del modelo en ~3 s cada una, 0 respuestas simuladas, el intento correcto evaluado «correcta». `eval_chat.py` con el modelo real queda pendiente: gastaría ~30 llamadas del cupo diario.

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

- Nivel 1: al pasar a `en_riesgo` o `inactivo`, o al quedar una sesión `por_recuperar`, se escribe en `outbox` (portal + email + WhatsApp simulados) con plantilla que menciona la clase, su plazo y los minutos de tramos clave que le quedan por ver (v2; antes decía «unos 20 minutos»).
- Nivel 2: a las 48 h sin actividad (watch/heartbeat/quiz/chat) o al pasar a `inactivo`, o ante solicitud de retiro. Asignado a `asistente_academico`.
- Tablero (v2): la etiqueta de una alerta de nivel 1 dice «escala en X h» mientras el alumno no tenga actividad después del aviso, o «con actividad» si ya la tuvo (entonces no escala por tiempo, solo si pasa a `inactivo`). En la demo, los 5 alumnos con nivel 1 tuvieron actividad después del aviso, así que ninguno muestra cuenta regresiva; `tests/test_alerts.py` cubre la cuenta y el escalamiento a las 48 h.
- Cierre automático si el alumno recupera o deja de estar en riesgo; cierre manual al registrar contacto desde el tablero.

## Recálculo

`services/recompute.py` es el único camino que persiste cumplimientos, features, snapshots de riesgo y alertas. Corre desde `POST /api/admin/recompute`, tras eventos del alumno (watch, quiz, micro, retiro) y cada hora vía APScheduler (desactivable con `DISABLE_SCHEDULER=1`). El seed además recorre los 28 días previos a `SIM_NOW` para dejar historial de estados y alertas coherente con la demo.

## Autenticación de demo

Sin OAuth. Header `X-User-Id` (o `?uid=` para `sendBeacon`). El login ofrece «Entrar como alumno» (elige un caso `cambio_horario` con proyección bajo el umbral y 1–2 pendientes), «Entrar como tutor» (Michela) y un selector con todos los usuarios sintéticos.

**Antes de un piloto con alumnos reales**, `X-User-Id` se reemplaza por autenticación real (SSO del aula virtual de DMC o sesión firmada del LMS): hoy cualquiera que conozca un id puede leer los datos de ese alumno. El control de roles (`require_tutor`) ya está centralizado en `deps.py`, así que el cambio se limita a `current_user`.

## Cohorte sintética

30 alumnos × 52 sesiones, 5 perfiles (`estable`, `mala_conexion`, `cambio_horario`, `desinteres`, `adaptado`) en `ground_truth.csv`. Una fracción de `cambio_horario` recupera **después** del plazo de 7 días para ejercitar el caso «fuera de plazo» del criterio 12. Verificación: `scripts/check_attendance.py`.

## Tensión de los «20 minutos»

Los tramos clave de la sesión de muestra suman ~83 min (46 % de 3 h) y los de la sesión real, ~77 min (37 % de 3 h 30 min). Recuperar una clase en «unos 20 minutos» no era cierto a velocidad normal, así que en v2 se eliminó esa promesa (`RECOVERY_MINUTES_HINT`): avisos, «Mi avance», el chat de plataforma y la oferta de la baja asistida muestran los **minutos de tramos clave que le quedan** al alumno (duración de los tramos menos lo ya visto) y su equivalente a 1.5×, más el quiz. Por ejemplo, Plinio ve «te faltan ~160 min de tramos clave (~107 min a 1.5×)» para sus 2 clases pendientes. El tablero sigue avisando si los tramos clave superan el 40 % de la clase.

## Datos y secretos

- Grabaciones, exportaciones Zoom, cohorte generada y `*.db` viven bajo `backend/data/` y están en `.gitignore` (excepto `data/sample/`).
- `backend/.env` no se versiona; plantilla en `.env.example`.
- Consentimiento (Ley 29733) versionado (`CONSENT_VERSION`); el modal aparece a los alumnos hasta que aceptan. «Más tarde» se guarda en `sessionStorage` (por usuario), así el modal no reaparece en cada página de esa pestaña; mientras no acepte, «Mi avance» muestra un aviso discreto con «Revisar y aceptar».

## Despliegue en Vercel

**v1:** snapshot SQLite copiado a `/tmp` en cada instancia (cero servicios externos, pero los cambios no persistían entre instancias ni tras inactividad). El proyecto se despliega con la CLI (sin integración Git) para no disparar despliegues desde otras ramas del repositorio compartido.

**v2 – Persistencia con Postgres (Neon):**

- Si `DATABASE_URL` es Postgres (`postgres://` o `postgresql://`, la que deja la integración de Neon), el backend usa psycopg 3 con `pool_pre_ping`, un pool chico por instancia y reciclaje a los 5 min (Neon corta conexiones inactivas). Sin `DATABASE_URL`, todo sigue en SQLite como antes; los tests siempre usan SQLite.
- El build sigue generando la base de demo en SQLite con `seed_demo.py` (es el «snapshot») y, si Postgres está vacío, la copia con `scripts/load_snapshot.py`: una sola transacción con candado de Postgres (`pg_advisory_xact_lock`), inserción por lotes y ajuste de las secuencias de `id`. El arranque de la función repite la verificación «si está vacía, copiar», por si el build no tuvo acceso a la base.
- `POST /api/admin/reset-demo` (solo staff) borra y recrea las tablas y vuelve a copiar el snapshot del despliegue, todo en la misma transacción (en Postgres el DDL es transaccional): deja la demo en el estado inicial, incluida la sesión real, y de paso aplica cambios de esquema entre versiones. Por eso, después de cada despliegue con cambios de modelo se llama a `reset-demo` (o `load_snapshot.py --force`); la copia «si está vacía» no altera tablas existentes.
- APScheduler sigue desactivado en Vercel; para un piloto, reemplazarlo por Vercel Cron sobre `/api/admin/recompute`.
- El pooler de Neon es PgBouncer en modo transacción: psycopg corre sin sentencias preparadas del lado del servidor (`prepare_threshold=None`) y el candado de la copia es por transacción (`pg_advisory_xact_lock`), que sí funciona detrás del pooler.
- **La demo publicada depende de archivos locales:** `.vercelignore` sube `backend/data/zoom/23/` (transcripción, materiales y `quiz.json`, sin el video) para que el build cargue la sesión real. Esos archivos nunca entran a Git; un despliegue desde un clon sin ellos publicaría la sesión 23 con contenido de muestra.
- Verificado en producción (12/10 de la demo): `reset-demo` en ~2 s (16 987 filas), todas las rutas GET responden 200, y al recuperar la sesión 23 (tramos clave + quiz 5/5) el estado «recuperada» se mantiene en tres recargas seguidas.

## Alcance fuera del MVP

Encuesta final y pagos aparecen en «Mi avance» como `pendiente_verificar`. El servicio `ai-service/` del repo original no se usa; toda la lógica de IA está en `backend/`.
