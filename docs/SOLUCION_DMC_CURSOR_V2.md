# Especificación v2 – Mejoras antes de la presentación (Reto DMC)

> **Para Cursor.** Continúa sobre lo ya construido en la rama `mateo` (ver `docs/SOLUCION_DMC_CURSOR.md` y `docs/DECISIONES.md`). Este documento define **qué cambia** en la versión 2. Lo que no se menciona aquí se mantiene igual. Guarda este archivo como `docs/SOLUCION_DMC_CURSOR_V2.md` y registra en `docs/DECISIONES.md` cada decisión que tomes.

---

## 0. Reglas de trabajo (sin cambios, obligatorias)

- Todo el trabajo se hace **solo en la rama `mateo`**. Antes de cada cambio, confirma con `git branch --show-current`.
- **Prohibido** hacer commits, push, merge, rebase o pull requests hacia `main` u otra rama; borrar o renombrar ramas; o usar `git push --force`. Si algo parece requerir cambios fuera de `mateo`, detente y pregunta.
- No modifiques `dmc.css`, `styles.css`, `enhancements.css` ni `fine-tuning.css`. Los estilos nuevos van en `frontend/solution.css`.
- No subas al repositorio videos, transcripciones reales, materiales del docente, la base de datos ni `.env` (ya están en `.gitignore`; mantenlo así).
- Commits pequeños y en español. Al final de cada bloque, `pytest` debe pasar.

## 1. Objetivos de la versión 2

1. **Usar la IA solo donde aporta.** El modelo de lenguaje (LLM) queda en el **chat de grabaciones** y en la clasificación de respuestas abiertas. Capítulos, resumen, «Lo que te perdiste» y quiz pasan a métodos deterministas, basados en el material del docente.
2. **El chat como tutor, no como solucionador.** Guía con active recall, pistas graduales y el minuto exacto del video; no entrega la respuesta completa de entrada.
3. **Una sesión real completa.** Video, transcripción y materiales del docente de la sesión de prueba alimentan la demo.
4. **Corregir lo detectado en la revisión del MVP:** tiempo de recuperación honesto, fechas del curso, consentimiento, persistencia de la demo publicada y detalles de texto.

## 2. Dónde se usa la IA (antes y después)

| Función | v1 (hoy) | v2 | Método v2 |
|---|---|---|---|
| Capítulos | LLM, una vez por sesión | **Sin LLM** | Alineación de las diapositivas del docente con la transcripción + segmentación por temas (3.2) |
| Tramos clave | LLM propone, docente valida | **Sin LLM** | Capítulos que corresponden a diapositivas marcadas como clave por el docente; si no hay marca, los de mayor densidad de términos del material. El docente valida (igual que hoy) |
| Resumen | LLM, una vez por sesión | **Sin LLM** | Resumen estructurado: por capítulo, título + puntos de sus diapositivas + 1–2 frases clave extraídas de la transcripción con su minuto (3.3) |
| Lo que te perdiste | Determinista | Determinista (sin cambios) | Cruce de los minutos ausentes de Zoom con los capítulos |
| Quiz: banco de preguntas | LLM, una vez por sesión | **Docente, con la IA como asistente a pedido** | El docente escribe, pide propuestas o mejoras a la IA, y edita y aprueba cada pregunta. El alumno solo ve preguntas aprobadas por el docente (3.4) |
| Quiz: corrección | Determinista | Determinista (sin cambios) | Opción múltiple |
| Tipo de pregunta en el chat | LLM rápido | **Sin LLM** | Reglas por palabras clave (3.5.1) |
| Chat de contenido | LLM | **LLM** (tutor socrático, 3.5) | Kimi por defecto |
| Micro-preguntas abiertas | LLM rápido | **LLM rápido** (sin cambios) | Texto libre: es donde las reglas fallan. Volumen bajo: 1 cada 2 semanas por alumno |

Resultado: el LLM queda en **2 lugares** (chat de contenido y micro-preguntas). Todo lo demás es reproducible, más barato y auditable por el docente.

## 3. Funcionamiento detallado

### 3.1 Sesión real de prueba

El equipo tiene el video, la transcripción y los materiales de una sesión. Estructura:

```
backend/data/zoom/<session_id>/
  recording.mp4            # o video_url en metadata.json (ver 3.6)
  transcript.vtt           # o transcript.txt; soporta ambos (ver abajo)
  chat.txt                 # opcional
  participants.csv         # opcional
  metadata.json            # {"number": 23, "title": "...", "date": "2026-10-06", "video_url": null}
  materials/               # lo que adjunta el docente: .pptx, .pdf, .txt, .docx
  quiz.json                # banco de preguntas del docente (ver 3.4)
  key_slides.json          # opcional: {"key_slides": [3, 4, 7, 12]}
```

- **Transcripción (en el MVP llega como `transcript.txt`).** El chat y los capítulos necesitan el minuto de cada frase, así que `app/services/transcript_txt.py` hace lo siguiente:
  1. **Detecta marcas de tiempo** en el `.txt` y las parsea si existen. Formatos a soportar: `[hh:mm:ss] Nombre: texto`, `hh:mm:ss Nombre: texto`, `Nombre hh:mm:ss` en una línea con el texto en la siguiente (formato de transcripción de Zoom), y bloques tipo VTT pegados como texto. Si el 80 % o más de los bloques tiene tiempo, se usa directamente.
  2. **Si no tiene marcas de tiempo**, las obtiene del video con `scripts/align_transcript.py`:
     - transcribe el audio del video con `faster-whisper` (modelo `small`, `language="es"`, `word_timestamps=True`), solo para obtener los tiempos;
     - alinea el texto del `.txt` con las palabras de Whisper mediante coincidencia de secuencias (`difflib.SequenceMatcher` sobre palabras normalizadas) y asigna a cada frase del `.txt` el tiempo de su primera palabra alineada; las frases sin coincidencia toman el tiempo interpolado entre sus vecinas;
     - **conserva el texto del `.txt`** (suele tener mejor ortografía y términos técnicos) y usa Whisper solo para los tiempos;
     - escribe `transcript.vtt` en la misma carpeta, y desde ahí sigue el flujo normal.
  3. Si no hay video ni marcas de tiempo, reparte los tiempos de forma proporcional a la cantidad de palabras sobre la duración de la clase. El panel muestra el aviso «minutos aproximados».
  - El alineamiento corre **una vez, en local** (`requirements-transcribe.txt`), no en Vercel; el resultado (`transcript.vtt`) se carga en la base.
  - A futuro, la integración con Zoom tomará directamente el `.vtt` de la grabación en la nube y este paso desaparecerá.
- **Materiales:** extrae texto con `python-pptx` (texto por diapositiva, incluidas las notas del orador), `pypdf` (por página), `python-docx` y lectura directa de `.txt`. Guarda cada unidad en una tabla nueva `material_unit(session_id, source_file, unit_no, title, text, is_key)`. En PowerPoint la unidad es la diapositiva; en PDF, la página; en `.txt` y `.docx`, el párrafo o bloque con título.
- **Título y número:** la sesión de demo toma título y número de `metadata.json`, para que **el título coincida con el contenido**. Las sesiones sin contenido real conservan el contenido de muestra, pero con un título genérico coherente («Sesión N – contenido de muestra»).
- `scripts/ingest_real_session.py <session_id>` ejecuta todo: ingesta, extracción de materiales, capítulos, tramos clave, resumen, quiz y recálculo.

### 3.2 Capítulos sin LLM

Algoritmo en `app/services/chaptering.py`:

1. **Ventanas de la transcripción:** agrupa los segmentos del VTT en ventanas de ~60 s.
2. **Anclaje a diapositivas (método principal, si hay materiales):**
   - Para cada unidad del material (diapositiva o página), calcula la similitud BM25 (`rank-bm25`, ya instalado) entre su texto y cada ventana. Normaliza el texto: minúsculas, sin tildes, lista corta de stopwords en español definida en el código.
   - Asigna a cada ventana la diapositiva más similar, si supera un umbral mínimo; si no, «sin diapositiva». Suaviza con una ventana deslizante de 3 para quitar saltos aislados.
   - Las diapositivas se presentan en orden creciente, así que fuerza monotonía: una diapositiva anterior solo puede reaparecer si dura 2 minutos o más (un repaso).
   - Cada tramo continuo con la misma diapositiva o grupo de diapositivas consecutivas es un **capítulo**. Su título es el título de la diapositiva (el primer texto o el placeholder de título).
3. **Fallback sin materiales:** segmentación tipo TextTiling, implementada en el código sin descargar corpus. Calcula la similitud coseno de TF entre bloques adyacentes de 3 ventanas, obtiene la «profundidad» de los valles y corta en los valles más profundos. El título del capítulo son los 3–4 términos con mayor TF-IDF del capítulo, por ejemplo «Webhooks · URL de producción · listener».
4. **Reglas de tamaño:** une capítulos menores a 4 min con su vecino y divide los mayores a 35 min por su valle más profundo. Objetivo: 5 a 12 capítulos.
5. Los capítulos de logística (pausa, temas administrativos) se detectan por palabras clave (`pausa`, `receso`, `break`, `tarea`, `asistencia`, `certificado`) y nunca son tramo clave.

El docente puede renombrar, mover límites y marcar o desmarcar tramos clave desde la pestaña «Tramos clave» del tablero, que ya existe. Agrega la edición de título e inicio/fin.

### 3.3 Resumen sin LLM

La pestaña «Resumen» muestra, por cada capítulo:
- Título y botón `[mm:ss]` para ir al minuto.
- **Puntos de la diapositiva:** hasta 4 viñetas tomadas del texto de las diapositivas de ese capítulo.
- **Frase clave:** 1–2 frases de la transcripción del capítulo, elegidas por centralidad (la frase con mayor similitud BM25 promedio con el resto del capítulo y con la diapositiva). Se muestran entre comillas, con su minuto.

Arriba del resumen, una línea: «Esta clase tiene N capítulos; los tramos clave suman X min.»

### 3.4 Quiz del docente, con la IA como asistente

Principio: **el docente es el autor y quien decide.** La IA solo trabaja cuando el docente se lo pide, y todo lo que propone llega como borrador que él revisa.

- Formato de `quiz.json` (carga inicial opcional): lista de preguntas `{question, options:[4], answer_idx, explanation, minute: "mm:ss", slide: n}`. Se recomiendan 8 preguntas por sesión.
- **Pestaña «Quiz»** en el tablero, por sesión, con la lista de preguntas y su estado: `borrador` → `aprobada` (o `descartada`). Cada pregunta muestra su origen (`docente` o `propuesta IA`), su minuto y su diapositiva.
- **Acciones de la IA, siempre a pedido del docente** (cada una es 1 llamada al LLM con los tramos clave y las diapositivas de la sesión):
  1. **«Proponer preguntas»:** sugiere hasta 5 preguntas sobre los tramos clave que aún no tienen preguntas, cada una con su minuto y diapositiva de respaldo.
  2. **«Sugerir alternativas»:** sobre una pregunta escrita por el docente, propone 3 distractores plausibles (errores comunes reales del tema, no opciones absurdas).
  3. **«Revisar redacción»:** señala ambigüedades (dos alternativas correctas, una pista en el enunciado, negaciones confusas) y sugiere una versión, sin reemplazar el texto del docente.
- **Las propuestas nunca se aprueban solas.** Cada una queda en `borrador`, y el docente la edita, la aprueba o la descarta una por una (no hay «aprobar todas»). Se guarda quién aprobó y cuándo (`approved_by`, `approved_at`), y si la pregunta se editó antes de aprobarla.
- **Publicación:** el quiz de la sesión se activa para el alumno cuando hay al menos 5 preguntas aprobadas. Hasta entonces, la vista de clase muestra «Quiz en preparación» y la sesión no puede recuperarse; avísalo en el tablero.
- En el tablero, indicador por sesión: «8 preguntas: 5 del docente, 3 propuestas por IA y editadas».
- La corrección sigue siendo determinista: 5 preguntas al azar del banco aprobado, alternativas mezcladas, aprobación con 3 de 5.
- **Retroalimentación con active recall:** al fallar una pregunta, no se muestra la respuesta correcta. Se muestra «Revisa el minuto [mm:ss] (diapositiva n)» con botón para ir al video. La explicación completa aparece solo después de aprobar el quiz.
- Agrega `minute`, `slide` y `approved` a `QuizItem`.

### 3.5 Chat como tutor (active recall y pistas graduales)

Principio: el chat **acompaña** el aprendizaje. Ubica, da pistas y hace que el alumno explique con sus palabras. No entrega la solución de entrada.

#### 3.5.1 Ruteo de la pregunta (sin LLM)

`app/services/chat_router.py` clasifica cada mensaje con reglas:

| Tipo | Señales (palabras clave, sin tildes) | Quién responde |
|---|---|---|
| `plataforma` | plazo, vence, recuperar, asistencia, certificado, nota, quiz aprobado, cuantas me faltan | **Backend, con datos reales** del alumno, sin LLM. Ejemplo: «La sesión 23 vence el martes 13 de octubre. Te faltan los tramos clave (45 % visto) y el quiz.» |
| `logistica` | zoom, link, horario, grupo, whatsapp, material, descargar | Respuesta fija con plantilla y enlace a la sección del curso |
| `contenido` | todo lo demás | LLM en **modo tutor** |

#### 3.5.2 Escalera de ayuda (modo tutor)

Cada pregunta de contenido abre un **hilo** (`chat_thread`) con un nivel de ayuda que sube solo cuando el alumno lo pide o lo intenta:

| Nivel | Qué hace el asistente | Cómo se llega |
|---|---|---|
| 1. Ubicar | Indica el minuto exacto `[mm:ss]` y la diapositiva donde se explica, y hace una pregunta de recuerdo: «¿Qué crees que pasa con la URL de test cuando cierras el editor?» | Primera respuesta |
| 2. Pista | Da una pista conceptual (una idea, no la respuesta) y vuelve a preguntar | Botón «Dame una pista» o respuesta incorrecta del alumno |
| 3. Paso guiado | Explica el primer paso o la mitad del razonamiento y le pide completar el resto | Botón «Otra pista» o segundo intento fallido |
| 4. Explicación | Explicación completa y breve, con minuto y diapositiva, y cierra con una pregunta de comprobación | Botón «Ver explicación» (aparece desde el nivel 3) o tras 2 intentos del alumno |

- **Evaluación del intento:** cuando el alumno responde a la pregunta de recuerdo, el LLM compara su respuesta con la transcripción y el material y devuelve `correcta | parcial | incorrecta`, con una retroalimentación de una o dos frases que reconoce lo que acertó. Si es correcta, cierra el hilo y sugiere el siguiente tramo clave.
- **Protección del quiz:** si el mensaje se parece a una pregunta del quiz aprobado de la sesión (BM25 por encima del umbral) y el alumno aún no lo aprueba, el nivel máximo para ese hilo es 2: solo ubicación y pista.
- **Fuera de la clase:** si no hay evidencia en la transcripción ni en el material, responde: «Eso no se vio en esta clase. Puedes consultarlo con el docente o con tu Asistente Académico.»

#### 3.5.3 Contexto y salida del LLM

- **Contexto por turno** (no se envía la transcripción completa): los 10 fragmentos de transcripción más relevantes según BM25, cada uno con su minuto; las 4 unidades de material más relevantes, con número de diapositiva; la lista de capítulos; y los últimos 6 mensajes del hilo. Esto reduce tokens y costo por turno.
- **Salida JSON** validada con Pydantic, con reintento (igual que hoy):
  ```json
  {"reply": "...", "level": 1, "citations": [{"sec": 900, "label": "15:00"}],
   "slides": [{"file": "sesion_23.pptx", "n": 7}],
   "asks_student": true, "evaluation": null}
  ```
- **Prompt** `prompts/tutor_system.md`, en español. Incluye las reglas de la escalera, la prohibición de dar la respuesta completa antes del nivel 4, la obligación de citar al menos un minuto, un máximo de 90 palabras por respuesta, tono cercano y tuteo.
- **Frontend (`recording.js`):** los minutos citados son botones que llevan el video a ese segundo, y las diapositivas citadas abren el material. Debajo de cada respuesta van los botones «Dame una pista», «Otra pista», «Ver explicación» (según el nivel) y «Ya lo entendí» (cierra el hilo). Agrega un campo «Responder» cuando `asks_student=true`.
- **Datos para el diagnóstico y el docente:** guarda por hilo el nivel alcanzado, los intentos y la evaluación. En el tablero agrega «Temas que más cuestan» por sesión: capítulos con más hilos que llegaron al nivel 3 o 4. Esto también sirve como variable de aprendizaje del alumno.

### 3.6 Video

- Agrega a `ClassSession` el campo `video_url`. Si existe, el reproductor usa `<video src=video_url>` y el seguimiento de rangos vistos funciona igual que hoy. Si no, se mantiene el `TranscriptPlayer`.
- **Alojamiento:** no incluyas el mp4 en el despliegue. Súbelo a **Vercel Blob** (misma plataforma; da una URL directa compatible con `<video>`) y guarda la URL en `metadata.json`. Alternativa: YouTube en modo «no listado», con la IFrame API para leer `getCurrentTime()`; requiere adaptar el seguimiento.
- En local: si existe `recording.mp4`, sírvelo desde `/media/<session_id>.mp4` con soporte de rangos HTTP (FileResponse de Starlette).

### 3.7 Correcciones de la revisión del MVP

1. **Tiempo de recuperación honesto.** Reemplaza todos los «unos 20 minutos» (`course.js`, `progress.js`, plantillas de `alerts.py`) por un cálculo real: `minutos_clave_pendientes = duración de los tramos clave − lo ya visto`. Texto: «Te faltan ~X min de tramos clave (~Y min a 1.5×) y el quiz.» En el tablero, avisa al docente si los tramos clave superan el 40 % de la clase.
2. **Fechas del curso desde la API.** `course-detail.html` debe leer inicio, fin, frecuencia y horario de `GET /api/courses/{id}`. Corrige el seed para que el texto y los datos coincidan.
3. **Consentimiento.** Si el alumno elige «Más tarde», no vuelvas a abrir el modal en esa sesión del navegador (`sessionStorage`). Muestra en su lugar una franja discreta en «Mi avance» hasta que acepte.
4. **Pluralización:** «queda 1 día» / «quedan N días». Revisa los demás textos con número.
5. **Escalamiento a 48 h.** Agrega una prueba con el caso «alerta de nivel 1 creada hace más de 48 h, sin actividad desde entonces → nivel 2». Verifica en la demo a los alumnos «en riesgo» sin actividad desde hace 3 días o más. Si el comportamiento es correcto (la alerta es reciente), muestra en el tablero «Nivel 1 · escala en Xh».
6. **Persistencia de la demo publicada.** Soporta `DATABASE_URL`: si existe, usa PostgreSQL (Neon desde Vercel Marketplace, plan gratuito); si no, SQLite como hoy. Agrega `psycopg[binary]` a las dependencias. Ajusta el build de Vercel para crear tablas y sembrar la base solo si está vacía. Agrega `POST /api/admin/reset-demo` (solo staff) para volver al estado inicial antes de presentar.
7. **Identidad de demo.** Mantén `X-User-Id`, pero agrega en `DECISIONES.md` que antes de un piloto real se reemplaza por autenticación real. No es parte de esta versión.

## 4. Dependencias nuevas

`python-pptx`, `pypdf`, `python-docx`, `psycopg[binary]`; `faster-whisper` es opcional y va solo en `requirements-transcribe.txt`, no en el despliegue. No agregues sklearn ni nltk: BM25, TF y TextTiling se implementan con `rank-bm25` y numpy.

## 5. Modelo de datos (cambios)

- `ClassSession`: + `video_url`.
- Nuevo `MaterialUnit(id, session_id, source_file, unit_no, title, text, is_key)`.
- `Chapter`: + `slide_from`, `slide_to`, `source` (`slides` | `texttiling` | `docente`), `key_sentences_json`.
- `QuizItem`: + `minute`, `slide`, `status` (`borrador` | `aprobada` | `descartada`), `source` (`docente` | `propuesta_ia`), `edited_by_teacher`, `approved_by`, `approved_at`.
- Nuevo `ChatThread(id, user_id, session_id, question, chapter_id, level, attempts, evaluation, closed, created_at)`; `ChatMessage`: + `thread_id`, `level`.
- `SessionSummary` deja de usarse (el resumen se arma al vuelo desde capítulos, diapositivas y frases clave). Mantén la tabla por compatibilidad o elimínala con migración del seed.

## 6. API (cambios)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/courses/{id}` | Datos del curso para la página (fechas, frecuencia, horario, docente, asistente) |
| GET | `/sessions/{id}/summary` | Resumen estructurado (3.3) |
| GET | `/sessions/{id}/materials` | Unidades del material con su número; descarga del archivo original |
| POST | `/sessions/{id}/chat` | `{message, thread_id?, action?: "hint"\|"explain"\|"answer"\|"close"}` → salida JSON de 3.5.3 |
| GET/POST | `/tutor/sessions/{id}/quiz` | Listar o crear preguntas del docente |
| PATCH | `/tutor/quiz/{item_id}` | Editar, aprobar o descartar una pregunta |
| POST | `/tutor/sessions/{id}/quiz/assist` | `{action: "proponer"\|"alternativas"\|"revisar", item_id?}` → propuestas en `borrador` (nunca aprobadas) |
| PATCH | `/tutor/sessions/{id}/chapters` | + editar título, inicio y fin |
| GET | `/tutor/sessions/{id}/hard-topics` | Temas que más cuestan |
| POST | `/admin/reset-demo` | Restaura la base de demo |

## 7. Orden de implementación

1. Ingesta de la sesión real: materiales, `metadata.json`, transcripción `.txt` con detección o alineamiento de tiempos, video local y `video_url`.
2. Capítulos sin LLM (anclaje a diapositivas + TextTiling) y tramos clave, con pruebas sobre la sesión real y la muestra.
3. Resumen estructurado sin LLM.
4. Quiz del docente: `quiz.json`, editor en el tablero con estados y aprobación una por una, las 3 acciones de la IA a pedido y retroalimentación al alumno con el minuto.
5. Ruteo del chat y respuestas de plataforma con datos reales.
6. Chat tutor: escalera de ayuda, evaluación del intento, protección del quiz, interfaz de botones y «Temas que más cuestan».
7. Correcciones de 3.7 (tiempo honesto, fechas, consentimiento, textos, escalamiento, persistencia, reset).
8. Despliegue: variables en Vercel (`LLM_API_KEY`, `LLM_MOCK=0`, `DATABASE_URL`), video en Blob, `reset-demo` y prueba del recorrido completo.

## 8. Criterios de aceptación

- **Uso de IA:** con `LLM_MOCK=1`, capítulos, resumen, «Lo que te perdiste», quiz y respuestas de plataforma funcionan igual que con el LLM real, porque no dependen de él. El LLM solo se llama en el chat de contenido, en las micro-preguntas y en las acciones del asistente del quiz que pide el docente. Agrega un contador de llamadas por función en los logs.
- **Capítulos de la sesión real:** entre 5 y 12; sus títulos corresponden a diapositivas reales; el docente o el equipo confirma que al menos 80 % de los límites caen a menos de 2 min del cambio de tema real.
- **Chat tutor**, evaluado con `scripts/eval_chat.py` actualizado, 20 preguntas de la sesión real con su minuto esperado:
  - ≥ 16 de 20 citan un minuto a ±90 s del esperado.
  - En el primer turno, 20 de 20 respuestas terminan con una pregunta al alumno y no superan 90 palabras.
  - 0 de 5 preguntas copiadas del quiz reciben la respuesta correcta antes de aprobarlo.
  - 5 de 5 preguntas fuera de la clase reciben «Eso no se vio en esta clase…».
- **Transcripción `.txt`:** en la sesión real, el 90 % o más de las frases quedan con un tiempo a ±10 s del momento real (verificar 20 frases al azar contra el video).
- **Quiz:** ninguna pregunta llega al alumno sin aprobación del docente (prueba automática); el quiz no se activa con menos de 5 aprobadas; las tres acciones de la IA crean solo borradores.
- **Plataforma:** «¿Cuándo vence la sesión 23?» responde con la fecha real del alumno sin llamar al LLM.
- **Demo publicada:** aprobar un quiz y recargar la página conserva el resultado (base persistente). `reset-demo` restaura el estado inicial.
- Los textos de tiempo de recuperación muestran minutos calculados, no un valor fijo.
- `pytest` pasa, incluidas las pruebas nuevas de chaptering, ruteo del chat, escalera de ayuda (con LLM simulado) y escalamiento a 48 h.

## 9. Demo guiada v2

1. Alumno «cambio_horario» → «Mi avance»: proyección bajo el 80 %, la sesión real por recuperar con «Te faltan ~X min de tramos clave».
2. Vista de clase: video real, capítulos con títulos de las diapositivas, resumen estructurado y «Lo que te perdiste».
3. Chat: el alumno pregunta un concepto → el asistente lo ubica en el minuto y le hace una pregunta → el alumno responde → recibe retroalimentación. Luego pide una pista y ve la escalera de ayuda.
4. Quiz: una respuesta errada muestra el minuto para repasar; al aprobar, la sesión pasa a «Recuperada».
5. Docente/tutor: estados y motivos, cola de nivel 2, «Temas que más cuestan» y tramos clave. En «Quiz», el docente pide a la IA «Proponer preguntas», edita una y la aprueba; muestra que las propuestas sin revisar no llegan al alumno.
