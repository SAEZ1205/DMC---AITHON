# Demo del jueves 15 de octubre de 2026 — RescuePath AI con una clase real de n8n

> **Material privado.** El video y la transcripción están en `demo-assets/` (ignorada por Git). No los subas, no los compartas
> y no los pegues en servicios externos. Esta guía no contiene texto de la clase, solo minutos.

## 1. Qué se muestra (una sola pantalla)
`frontend/demo-n8n.html` → **Introducción a n8n — Clase 1** (3:30:11): reproductor del video original, un **resumen local por temas con
índice de minutos** y un **tutor conversacional** que responde con lo que dice la clase y cita el minuto. Al pulsar una cita o un tema
del índice, el video salta a ese momento. (No hay quizzes, asistencia ni análisis de abandono en esta pantalla: no forman parte del MVP mostrado.)

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

## 5. Secuencia de presentación (≈ 4-5 minutos)
1. **(0:30) Pantalla.** Título, video de 3:30:11 y el estado "Transcripción ingerida: 282 segmentos". Pulsa ▶ un instante: es el video original.
2. **(1:00) Resumen e índice.** Señala la etiqueta *"Resumen local por temas · sin IA generativa"* y *"Reutilizado desde la base de datos"*: se calculó una sola vez al
   procesar la clase. El texto de arriba es un **índice con minutos** (no frases del docente). Pulsa un tema (p. ej. **10:30 No-code y low-code**): el video salta.
3. **(1:30) Tutor con citas.** Pregunta **"¿Qué es n8n y para qué sirve?"** (o usa la versión larga *"…Explícamelo de forma sencilla según lo enseñado por el profesor"*: da la misma cita).
   Pulsa la cita **▶ 17:04** y el video salta. Repite con **"¿Para qué sirve Docker en esta clase?"** → **▶ 39:03**.
4. **(0:45) Honestidad del tutor.** "¿Cómo se prepara una pizza napolitana?" → fuera de alcance, sin citas inventadas. "¿Qué es Kubernetes?" → "No encontré ese tema en la transcripción".
5. **(0:45) Estado visible.** Muestra la etiqueta de cada respuesta (`Respuesta: DEMO` / `LIVE · nvidia-kimi · moonshotai/kimi-k3`) y el indicador "Kimi está preparando tu respuesta…".

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

## 7. Cómo explicarlo técnicamente (30 segundos por pieza)
- **Guardián de pertinencia:** reglas locales deciden si la pregunta trata de la clase (usa el vocabulario real de la transcripción). Solo si duda y Kimi está activo, una llamada pequeña
  (≤ 25 s) consulta al modelo con la pregunta y el título; si falla, se queda con la heurística.
- **Recuperador (RAG léxico):** busca en los 282 segmentos con BM25 + frases literales; en preguntas de definición ("¿qué es X?", "¿para qué sirve X?") prioriza los segmentos donde X aparece
  junto a "permite / sirve / es un…" o donde alguien pregunta lo mismo en la clase, y descarta las frases de estilo ("de forma sencilla"). Elige **una cita principal** y añade otra solo si es casi igual de fuerte.
- **Kimi K3 (NVIDIA Build):** recibe como máximo 3 fragmentos de ~40 s, la pregunta enmascarada y un historial corto, con un prompt que le pide responder directo, parafrasear, no atribuir al docente
  lo que no dice y separar cualquier "complemento" de lo que sí dice la clase. Nunca recibe el video ni la transcripción completa.
- **Verificador de citas:** el modelo solo nombra etiquetas (S1, S2); **el backend** pone los minutos reales, rechaza minutos escritos a mano, enlaces o promesas sobre certificados sin respaldo,
  y verifica que cada frase entre «comillas» exista en los segmentos. Si falla algo, responde el modo DEMO con un aviso explícito ("Kimi no participó").
- **Privacidad:** correos, teléfonos y nombres se enmascaran en la transcripción, en la pregunta y en el historial, y una barrera final los vuelve a revisar antes de cada envío (si queda un nombre conocido, no se envía).

## 8. Cómo explicar el resumen persistente
Al procesar la clase se calcula **una vez** y se guarda en SQLite (clave: transcripción + versión del algoritmo). Se regenera solo si cambia la transcripción, la versión del algoritmo o por una acción
administrativa explícita. Los títulos son **etiquetas temáticas** asignadas porque sus palabras clave aparecen en esa parte de la transcripción (17 capítulos, de "Introducción a la automatización
de procesos" a "Cierre y próximos pasos"); la frase de cada tema es una cita textual con los errores del reconocimiento automático. **No es un resumen redactado por IA**: no digas que lo es.
El resumen con Kimi existe como acción administrativa separada (§10) y, si se generara, se mostraría con otra etiqueta ("Resumen generado con Kimi").

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
| El botón Enviar quedó bloqueado | No debería: se libera tras cada respuesta o error. Recarga la página (la conversación se conserva en el backend). |

## 12. Pruebas y evaluación (sin red)
```powershell
cd C:\Users\Admin\Documents\DMC---AITHON\ai-service
.\.venv\Scripts\python.exe -m pytest -q                              # suite completa (898 pruebas, todas con transporte simulado)
.\.venv\Scripts\python.exe -X utf8 scripts\eval_tutor.py             # 10 preguntas sobre la clase real: DEMO y "LIVE simulado"
.\.venv\Scripts\python.exe -X utf8 scripts\plan_kimi_summary.py      # plan (solo lectura) del resumen con Kimi
```
`eval_tutor.py` escribe `data\real\eval_tutor_demo.md` y `eval_tutor_simulated.md` (ignorados por Git). El modo "LIVE simulado" usa un transporte falso: valida el circuito, **no** mide a Kimi.

## 13. (Opcional) Resumen con Kimi — **no es necesario para la demo y no se ha ejecutado**
Requiere `LLM_PROVIDER`, clave, `LLM_LIVE_AUTHORIZED=true`, `NVIDIA_ALLOW_CLASS_CONTENT=true` y `ADMIN_API_TOKEN`. Es una acción administrativa **síncrona** de ~21 solicitudes (una por trozo de capítulo + una de reducción)
que, en conjunto, envía todo el texto de la clase (enmascarado) a NVIDIA en trozos. Mira el plan con `plan_kimi_summary.py` y decide con tiempo; **nunca durante la demo**.
```powershell
Invoke-RestMethod -Method Post -Headers @{ "X-Admin-Token" = "<ADMIN_API_TOKEN>" } `
  "http://127.0.0.1:8000/api/admin/lessons/n8n-intro/clase-01-introduccion-n8n/summary?engine=kimi"
```
Esta función **no se ha probado contra la API real** (solo con simulaciones).
