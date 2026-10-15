# Demo del jueves 15 de octubre de 2026 — RescuePath AI con una clase real de n8n

> **Material privado.** El video y la transcripción están en `demo-assets/` (ignorada por Git). No los subas, no los compartas
> y no los pegues en servicios externos. Esta guía no contiene texto de la clase, solo minutos.

## 1. Qué se muestra (una sola pantalla, dos pestañas)
`frontend/demo-n8n.html` → **Introducción a n8n — Clase 1** (3:30:11). Pestaña **Clase y tutor**: reproductor del video original, un **resumen local por temas con
índice de minutos** y un **tutor conversacional** que responde con lo que dice la clase y cita el minuto (al pulsar una cita o un tema del índice, el video salta).
Pestaña **Validar aprendizaje**: un quiz de dos preguntas que **simula** la validación de participación efectiva. (No hay asistencia real, calificaciones ni análisis de abandono en esta pantalla.)

### Los tres agentes de la demostración
| Agente | Qué hace | Cómo está hecho | Dónde se ve |
|---|---|---|---|
| **1. Batch** — capítulos y resumen | Convierte la grabación en un índice navegable con minutos y un resumen por temas | Procesamiento local de la transcripción (léxico temático), guardado en SQLite; sin IA generativa | Pestaña *Clase y tutor* → tarjeta "Resumen de la clase" |
| **2. Tutor** — consulta generativa | Responde preguntas con lo que dice la clase y cita el minuto | Guardián → recuperador (RAG léxico) → **Kimi K3 vía NVIDIA Build** → verificador de citas; fallback DEMO visible | Pestaña *Clase y tutor* → chat |
| **3. Validador** — participación efectiva | Comprueba comprensión con dos preguntas de opción múltiple sobre la clase | **Simulación 100 % local** en JavaScript: sin backend, sin Kimi y sin registros | Pestaña **Validar aprendizaje** |

> **El quiz NO certifica asistencia oficialmente.** Es una **simulación académica**, una prueba de concepto de cómo un futuro flujo institucional podría pedir una validación de aprendizaje
> a quien faltó a la clase. No actualiza asistencia, participación, calificaciones ni datos de estudiantes; no guarda las respuestas; no llama al tutor ni a ningún modelo.

## 2. Requisitos
- Python 3.14 con el entorno `ai-service\.venv` (`pip install -r requirements.txt` si hiciera falta).
- Archivos locales: `demo-assets\clase-demo.mp4` y `demo-assets\transcripcion-original.txt` (no se versionan).
- Chrome o Edge actualizados (el MP4 es H.264 + AAC).
- La demo funciona **sin NVIDIA** (modo DEMO). Kimi K3 es opcional y se activa solo con tu `ai-service\.env` (§9).

## 3. Arranque (PowerShell, dos terminales)

**Terminal 1 — backend (puerto 8000)**
```powershell
cd C:\Users\Admin\Documents\DMC---AITHON\ai-service
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```
Debe mostrar `Application startup complete`. **Si ya había un backend abierto, ciérralo (Ctrl+C) y vuelve a lanzarlo**: así toma el código nuevo.
La primera vez tras esta actualización se regenera **una sola vez** el resumen (cambió la versión del algoritmo); después se reutiliza de SQLite.

**Terminal 2 — frontend (puerto 5500)**
```powershell
cd C:\Users\Admin\Documents\DMC---AITHON\frontend
..\ai-service\.venv\Scripts\python.exe -m http.server 5500 --bind 127.0.0.1
```
Si el puerto 5500 ya está en uso por esa misma orden, no hace falta repetirla.

**URL de la demo:** <http://localhost:5500/demo-n8n.html> (CORS solo admite `localhost:5500` y `127.0.0.1:5500`). Pulsa **Ctrl+F5** para
cargar el JavaScript nuevo.

## 4. Verificación inicial (1 minuto, antes de presentar)
Abre <http://127.0.0.1:8000/health> y comprueba:

| Campo | Qué debe decir | Si no |
|---|---|---|
| `status` | `ok` | revisa la terminal del backend |
| `real_lesson.loaded` / `video_available` | `true` / `true` | faltan archivos en `demo-assets\` |
| `mode` y `llm.active_provider` | `DEMO` / `demo` **o** `LIVE` / `nvidia-kimi` | LIVE solo si lo activaste en `.env` |
| `llm.class_content_authorized` (solo LIVE) | `true` si quieres que Kimi vea fragmentos | `false` ⇒ responderá en DEMO con aviso |
| `llm.limits` (solo LIVE) | `timeout_seconds` 120, `total_timeout_seconds` 150, `max_output_tokens` 700 | |
| `llm.egress_guard` (solo LIVE) | `active: true`, `known_names` > 0, `blocked: 0` | barrera de privacidad |
| `llm.provider_status.state` (solo LIVE) | `ok` | `paused` ⇒ NVIDIA devolvió 401/403/404/429; espera o usa DEMO |

En la pantalla debe verse `Modo DEMO` (o `Modo LIVE`) y el resumen con la etiqueta **"Resumen local por temas · sin IA generativa"**.

**Ensayo recomendado (si usarás Kimi):** haz 2 preguntas LIVE el día anterior. El prompt cambió (v1.1.0) y **no se ha probado contra Kimi real**; un modelo puede
devolver un formato que el verificador rechace (entonces verás el fallback DEMO con aviso, no un error). Todo lo medido en esta etapa fue local y simulado.

## 5. Secuencia de presentación (≈ 5 minutos)
Frase de apertura: *"Una persona que faltó a la clase necesita tres cosas: ponerse al día, resolver dudas y demostrar que comprendió. Cada una es un agente."*
1. **(0:30) Pantalla.** Título, video de 3:30:11 y el estado "Transcripción ingerida: 282 segmentos". Pulsa ▶ un instante: es el video original.
2. **(0:45) Agente Batch.** Señala *"Resumen local por temas · sin IA generativa"* y *"Reutilizado desde la base de datos"*: se calculó una vez al procesar la clase. Es un **índice con minutos**
   (no frases del docente). Pulsa un tema (p. ej. **10:30 No-code y low-code**): el video salta.
3. **(1:15) Agente Tutor.** Pregunta **"¿Qué es n8n y para qué sirve?"** → pulsa la cita **▶ 17:04** y el video salta. Repite con **"¿Para qué sirve Docker en esta clase?"** → **▶ 39:03**.
   Muestra la etiqueta de la respuesta (`LIVE · nvidia-kimi · moonshotai/kimi-k3` o `DEMO`) y, si quieres, "¿Cómo se prepara una pizza napolitana?" (fuera de alcance, sin citas inventadas).
4. **(1:30) Agente Validador.** Pulsa la pestaña **Validar aprendizaje** (el video se pausa y conserva su posición). Responde mal a propósito → *"0 de 2 respuestas correctas"* + pistas con el minuto;
   pulsa **▶ Ver en el video · 09:18–10:10** (vuelves a la clase y el video salta) y regresa a la pestaña. **Volver a intentar** → responde bien → ***"2 de 2 respuestas correctas · Comprensión validada (simulación académica)"***.
5. **(0:30) Cierre.** Repite lo que **no** es: simulación, no certifica asistencia ni toca registros; hoy es local y sin IA; el siguiente paso sería conectarlo al sistema institucional (con autorización).

### Quiz: respuestas y respaldo en la clase (para el mentor)
| # | Pregunta (resumen) | Correcta | Dónde lo explica la clase |
|---|---|---|---|
| 1 | Reporte diario repetitivo con reglas definidas: ¿qué conviene? | **b)** Automatizarlo | **09:18–10:10**: procesos repetitivos y con reglas ordenadas y bien definidas; sin reglas claras no se puede optimizar |
| 2 | Aplicación antigua sin API que exige clics en botones: ¿qué alternativa? | **c)** RPA (interactúa con la interfaz) | **25:44–26:15**: con API conviene un workflow; sin API y con clics en botones, un RPA |

Cada alternativa incorrecta contradice lo que dice la clase (p. ej. "dejarlo manual" o "workflow por API cuando no hay API"). Las respuestas se verificaron contra los renglones reales de la transcripción (la prueba
automática `test_the_answers_are_supported_by_the_real_transcript` lo repite). Si fallan, el estudiante recibe una pista (no la respuesta) y el minuto para repasar.

### Preguntas recomendadas y minutos esperados (verificados con la clase real, en modo DEMO)
| Pregunta | Segmento citado | Salto del video (▶) |
|---|---|---|
| ¿Qué es n8n y para qué sirve? | 16:39–17:25 | **17:04** |
| ¿Para qué sirve Docker en esta clase? | 38:33–39:13 | **39:03** |
| ¿Cuál es la diferencia entre no-code y low-code? | 15:59–16:39 | **16:16** |
| ¿Qué procesos conviene automatizar? | 09:07–09:49 | **09:18** |
| ¿Qué diferencia existe entre un workflow y un RPA? | 31:35–32:16 | **31:47** |

En modo LIVE Kimi redacta la respuesta pero **las citas siguen saliendo de estos mismos segmentos**; puede citar un segundo fragmento cercano (p. ej. ~18:51 en no-code/low-code).
**No recomendado en vivo:** el seguimiento "Dame otro ejemplo" justo después de la pregunta de n8n (el sistema solo recuerda los términos de la pregunta anterior y trae menciones
débiles). Sí funciona bien tras "¿Qué diferencia existe entre un workflow y un RPA?".

## 6. Cómo demostrar el salto del chat al video
Pulsa el botón **▶ mm:ss** bajo la respuesta: el reproductor se desplaza a la vista, salta a ese minuto y reproduce. El minuto exacto es el renglón de la transcripción donde empieza
la frase relevante; el rango del segmento (p. ej. 16:39–17:25) aparece al pasar el cursor. Haz lo mismo desde el índice del resumen.
**Nota:** al pulsar un capítulo el video salta a la frase representativa del capítulo, que puede estar unos minutos después de la hora que muestra el índice (p. ej. el capítulo de 31:35 salta a ~38:11).

## 7. Cómo explicarlo técnicamente (30 segundos por pieza)
- **Guardián de pertinencia:** reglas locales deciden si la pregunta trata de la clase (usa el vocabulario real de la transcripción). Solo si duda y Kimi está activo, una llamada pequeña
  (≤ 25 s) consulta al modelo con la pregunta y el título; si falla, se queda con la heurística.
- **Recuperador (RAG léxico):** busca en los 282 segmentos con BM25 + frases literales; en preguntas de definición ("¿qué es X?", "¿para qué sirve X?") prioriza los segmentos donde X aparece
  junto a "permite / sirve / es un…" o donde alguien pregunta lo mismo en la clase, y descarta las frases de estilo ("de forma sencilla"). Elige **una cita principal** y añade otra solo si es casi igual de fuerte.
- **Kimi K3 (NVIDIA Build):** recibe como máximo 3 fragmentos de ~40 s, la pregunta enmascarada y un historial corto, con un prompt que le pide responder directo, parafrasear, no atribuir al docente
  lo que no dice y separar cualquier "complemento" de lo que sí dice la clase. Nunca recibe el video ni la transcripción completa.
- **Verificador de citas:** el modelo solo nombra etiquetas (S1, S2); **el backend** pone los minutos reales, rechaza minutos escritos a mano, enlaces o promesas sobre certificados sin respaldo,
  y verifica que cada frase entre «comillas» exista en los segmentos. Si falla algo, responde el modo DEMO con un aviso explícito ("Kimi no participó").
- **Validador:** quiz local de 2 preguntas (`frontend/validator-n8n.js`): la calificación es una función pura (`gradeQuiz`) que exige las dos respuestas y devuelve 0/2, 1/2 o 2/2. No usa el backend de
  quizzes existente (que persiste intentos y telemetría en SQLite y solo tiene banco del curso SQL ficticio) para no escribir registros de estudiantes en una simulación. No hace ninguna solicitud de red.
- **Privacidad:** correos, teléfonos y nombres se enmascaran en la transcripción, en la pregunta y en el historial, y una barrera final los vuelve a revisar antes de cada envío (si queda un nombre conocido, no se envía).

## 8. Cómo explicar el resumen persistente
Al procesar la clase se calcula **una vez** y se guarda en SQLite (clave: transcripción + versión del algoritmo). Se regenera solo si cambia la transcripción, la versión del algoritmo o por una acción
administrativa explícita. Los títulos son **etiquetas temáticas** asignadas porque sus palabras clave aparecen en esa parte de la transcripción (17 capítulos, de "Introducción a la automatización
de procesos" a "Cierre y próximos pasos"); la frase de cada tema es una cita textual con los errores del reconocimiento automático. **No es un resumen redactado por IA**: no digas que lo es.
El resumen con Kimi existe como acción administrativa separada (§10) y, si se generara, se mostraría con otra etiqueta ("Resumen generado con Kimi").

## 8b. Latencia (Etapa 9): qué cambió y qué NO está medido
- Cada respuesta muestra una píldora **⏱** (servidor, Kimi y espera total). `/health` → `llm.latency` resume las últimas preguntas. El backend propio tarda ~25 ms; **la espera real es NVIDIA y no se ha medido con el prompt nuevo**.
- **Caché de respuestas verificadas** (`ANSWER_CACHE_ENABLED=true` por defecto, solo con Kimi): si en el ensayo hiciste una pregunta, repetirla (primera pregunta de una conversación nueva, misma clase, mismo prompt y modelo) responde al instante con la etiqueta
  **"Respuesta guardada · generada antes por Kimi (fecha)"**. Díselo a la audiencia: no es una generación nueva. Para precalentarla, haz en el ensayo las 3 preguntas recomendadas con Kimi y **no cambies código, prompt ni
  `mask-names.txt` después** (invalidan las entradas). Verifica en `/health` → `llm.answer_cache` (`entries`, `hits`).
- **Streaming:** existe pero está **apagado** y sin probar contra NVIDIA real. No lo actives el jueves sin un ensayo (ver el informe de la Etapa 9).

## 9. Si NVIDIA tarda, falla o llega al límite gratuito
- **Tarda:** Kimi razona antes de responder; el chat muestra "Kimi está preparando tu respuesta…" con un contador y, a los 15 s, un aviso. Espera hasta ~2,5 min como máximo. **No hay mediciones de la
  velocidad real de Kimi con este prompt**: no prometas un tiempo. Una prueba anterior con texto ficticio tardó 42 s.
- **Falla o no responde a tiempo:** la respuesta sale en **DEMO con las mismas citas** y la etiqueta *"Respuesta DEMO · NO la generó Kimi"*. Es el comportamiento esperado, no un error.
- **Límite gratuito (HTTP 429) o acceso (401/403/404):** el proveedor se pausa (60 s o 5 min) y todo sigue en DEMO; `/health` → `llm.provider_status` muestra el motivo.
- **Plan B sin red o sin Kimi:** quita `LLM_PROVIDER` y `LLM_LIVE_AUTHORIZED` de `ai-service\.env` y reinicia el backend: queda en DEMO puro (la demo completa sigue funcionando).
- **Antes de abrir contenido de la clase a NVIDIA** revisa `ai-service\data\real\revision_privacidad.md` y `payload_nvidia_preview.md` (scripts `review_privacy.py` e `inspect_nvidia_payload.py`; sin red).

## 10. Implementado vs. futuro
| Implementado en este MVP | Futuro (no se muestra ni se promete) |
|---|---|
| Video real con saltos a timestamps | Sincronía audio-texto verificada en TODA la clase (solo ~17:04-17:09 se comprobó escuchando) |
| Tutor con citas, guardián, verificador y fallback DEMO visible | Respuesta en *streaming* (se evaluó: ver abajo) |
| Resumen local por temas con índice, persistido en SQLite | Resumen redactado con Kimi (requiere tu autorización; `scripts\plan_kimi_summary.py` muestra el plan) |
| Kimi K3 vía NVIDIA con tope de tiempo y barrera de privacidad | Modelo de pago / producción (el tier gratuito tiene límites) |
| Evaluación reproducible (`scripts\eval_tutor.py`), 100 % local | Medir calidad de Kimi real en lote (consume llamadas) |
| Validador: quiz de 2 preguntas como **simulación** local | Validación **real** de participación: conectar con el sistema institucional, banco de preguntas por clase, intentos y registro (requiere decisiones y autorización de DMC) |

**Streaming — por qué no se implementó:** Kimi K3 emite primero su razonamiento y el verificador necesita la respuesta completa (citas, minutos, comillas) antes de mostrar nada; mostrar texto sin verificar rompería la garantía de citas.
Con el backend actual, tardar es solo el tiempo de NVIDIA (el backend local responde en ~10 ms). Es una decisión de producto que requeriría tu autorización.

## 11. Si algo falla
| Síntoma | Qué revisar |
|---|---|
| "No se pudo cargar la demostración" | ¿Backend en 8000? Abre `/health`. ¿Existen los dos archivos en `demo-assets\`? Reinicia el backend. |
| Video no carga | `/health` → `real_lesson.video_available`; prueba Chrome/Edge actualizados; el archivo original no se modifica. |
| Veo el resumen/estilo anterior | Reinicia el backend y recarga con Ctrl+F5. |
| Puerto ocupado | `Get-NetTCPConnection -LocalPort 8000,5500 -State Listen` y detén el proceso, o usa otro puerto (`?api=http://127.0.0.1:PUERTO` solo admite orígenes locales). |
| CORS | Abre la demo desde `http://localhost:5500` o `http://127.0.0.1:5500`. |
| No veo la pestaña "Validar aprendizaje" | Recarga con Ctrl+F5 (el navegador tenía el JavaScript anterior) y comprueba que el servidor estático sirve `frontend\`. También abre <http://localhost:5500/demo-n8n.html#validar>. |
| El quiz no responde | No depende del backend ni de NVIDIA: recarga la página. Si persiste, abre la consola del navegador (F12). |
| El botón Enviar quedó bloqueado | No debería: se libera tras cada respuesta o error. Recarga la página (la conversación se conserva en el backend). |

## 12. Pruebas y evaluación (sin red)
```powershell
cd C:\Users\Admin\Documents\DMC---AITHON\ai-service
.\.venv\Scripts\python.exe -m pytest -q                              # suite completa (916 pruebas; transporte simulado y conexiones externas bloqueadas)
.\.venv\Scripts\python.exe -X utf8 scripts\eval_tutor.py             # 10 preguntas sobre la clase real: DEMO y "LIVE simulado"
.\.venv\Scripts\python.exe -X utf8 scripts\plan_kimi_summary.py      # plan (solo lectura) del resumen con Kimi
```
Las pruebas del validador (`tests\test_validator.py`) abren la página en un Chrome real, solo contra `127.0.0.1` (sin fuentes ni iconos externos), y se omiten solas si no hay Chrome o Node.
`eval_tutor.py` escribe `data\real\eval_tutor_demo.md` y `eval_tutor_simulated.md` (ignorados por Git). El modo "LIVE simulado" usa un transporte falso: valida el circuito, **no** mide a Kimi.

## 13. (Opcional) Resumen con Kimi — **no es necesario para la demo y no se ha ejecutado**
Requiere `LLM_PROVIDER`, clave, `LLM_LIVE_AUTHORIZED=true`, `NVIDIA_ALLOW_CLASS_CONTENT=true` y `ADMIN_API_TOKEN`. Es una acción administrativa **síncrona** de ~21 solicitudes (una por trozo de capítulo + una de reducción)
que, en conjunto, envía todo el texto de la clase (enmascarado) a NVIDIA en trozos. Mira el plan con `plan_kimi_summary.py` y decide con tiempo; **nunca durante la demo**.
```powershell
Invoke-RestMethod -Method Post -Headers @{ "X-Admin-Token" = "<ADMIN_API_TOKEN>" } `
  "http://127.0.0.1:8000/api/admin/lessons/n8n-intro/clase-01-introduccion-n8n/summary?engine=kimi"
```
Esta función **no se ha probado contra la API real** (solo con simulaciones).
