# Demo del jueves 15 de octubre de 2026 — RescuePath AI con una clase real de n8n

> **Material privado.** El video y la transcripción están en `demo-assets/` (ignorada por Git). No los subas, no los compartas
> y no los pegues en servicios externos. Esta guía no contiene texto de la clase.

## 1. Qué se muestra (una sola pantalla)
`frontend/demo-n8n.html` → **Introducción a n8n — Clase 1**: reproductor del video original, resumen persistido de la clase y chat con el
tutor. Cada respuesta cita el minuto; al pulsar la cita, el video salta a ese momento.

## 2. Requisitos
- Python 3.14 con el entorno `ai-service\.venv` ya creado (`pip install -r requirements.txt` si hiciera falta).
- Archivos locales: `demo-assets\clase-demo.mp4` y `demo-assets\transcripcion-original.txt` (no se versionan).
- Chrome o Edge actualizados (el MP4 es H.264 + AAC).
- **No hace falta API key**: la demo funciona en **modo DEMO** (sin llamadas de pago).

## 3. Arranque (PowerShell, dos terminales)

**Terminal 1 — backend (puerto 8000)**
```powershell
cd C:\Users\Admin\Documents\DMC---AITHON\ai-service
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```
Debe mostrar `Application startup complete`. Comprobación rápida: <http://127.0.0.1:8000/health> →
`"mode": "DEMO"` y `"real_lesson": {"loaded": true, ...}`.

**Terminal 2 — frontend (puerto 5500)**
```powershell
cd C:\Users\Admin\Documents\DMC---AITHON\frontend
..\ai-service\.venv\Scripts\python.exe -m http.server 5500 --bind 127.0.0.1
```
Abre <http://localhost:5500/demo-n8n.html>. (CORS solo admite `localhost:5500` y `127.0.0.1:5500`.)

## 4. Secuencia corta (≈ 5 min)
1. **Pantalla:** título, estado ("Transcripción ingerida: 282 segmentos…"), video con duración **3:30:11**. Pulsa ▶ para mostrar que reproduce.
2. **Resumen persistido:** señala las etiquetas *"Resumen heurístico DEMO (sin modelo de lenguaje)"* y *"Reutilizado desde la base de datos"*:
   se generó una sola vez al procesar la clase, no al abrir la página. Pulsa un capítulo → el video salta a ese minuto.
3. **Tutor con citas:** pulsa las sugerencias, una a una:
   - ¿Qué es n8n? → cita ~17:04 (el profesor define n8n). Pulsa **▶ 17:04**: el video salta a ese punto.
   - ¿Cuál es la diferencia entre no-code y low-code?
   - ¿Qué procesos conviene automatizar?
   - ¿Qué diferencia existe entre un workflow y un RPA?
   - ¿Para qué sirve Docker en esta clase?
4. **Guardián:** pregunta *"¿Cómo se prepara una pizza napolitana?"* → respuesta de **fuera de alcance**, sin citas inventadas
   (etiqueta *"Guardián heurístico (reglas): no pertinente"*). Pregunta algo de otro curso (*"¿Cómo funciona un LEFT JOIN?"*) → *"No encontré ese tema…"*.
5. **Honestidad:** las respuestas son citas textuales de la transcripción (automática, con errores) + una pregunta de repaso; **no** es un LLM.
   El mensaje del estudiante y los datos personales se enmascaran; el material no sale del equipo.

## 5. Qué decir (y qué NO decir)
- Sí: modo **DEMO** determinista; guardián **heurístico**; resumen **heurístico**; citas verificables por minuto; datos personales enmascarados; todo local.
- No decir: que el resumen o el guardián los hace un LLM; que Kimi está activo; que la sincronía audio-texto está verificada en toda la clase
  (solo ~17:04–17:09 se comprobó escuchando; ver §7).

## 6. Si algo falla
| Síntoma | Qué revisar |
|---|---|
| "No se pudo cargar la demostración" | ¿Backend en 8000? Abre `/health`. ¿Existen los dos archivos en `demo-assets\`? Reinicia el backend. |
| Video no carga | `/health` → `real_lesson.video_available`. Prueba Chrome/Edge actualizados; el archivo original no se modifica. |
| Puerto ocupado | `Get-NetTCPConnection -LocalPort 8000,5500 -State Listen` y detén el proceso, o usa otro puerto (`?api=http://127.0.0.1:PUERTO` solo admite orígenes locales). |
| CORS | El frontend debe abrirse desde `http://localhost:5500` o `http://127.0.0.1:5500`. |

## 7. Verificar la sincronía a mano (recomendado antes de la demo)
Al arrancar, el backend escribe `ai-service\data\real\verificacion_manual.md` (ignorado por Git; contiene fragmentos de la clase) con
10 puntos distribuidos a lo largo de la clase. Las comprobaciones **automáticas** confirman la estructura de la transcripción; **escuchar el video** en
cada minuto confirma que el texto coincide con el audio. Marca cada casilla.

## 8. Pruebas
```powershell
cd C:\Users\Admin\Documents\DMC---AITHON\ai-service
.\.venv\Scripts\python.exe -m pytest -q
```
No consumen tokens ni usan la red (Kimi se simula). Las pruebas contra los archivos reales se omiten solas si `demo-assets\` no existe.

## 9. (Opcional) Resumen con Kimi — **de pago, no es necesario para la demo**
Requiere `LLM_PROVIDER=kimi`, `LLM_API_KEY`, `LLM_LIVE_AUTHORIZED=true` y `ADMIN_API_TOKEN` en `ai-service\.env`. Genera el resumen por fragmentos
(~21 llamadas para esta clase: una por capítulo + una de reducción; revisa el precio vigente de Kimi antes) con:
```powershell
Invoke-RestMethod -Method Post -Headers @{ "X-Admin-Token" = "<ADMIN_API_TOKEN>" } `
  "http://127.0.0.1:8000/api/admin/lessons/n8n-intro/clase-01-introduccion-n8n/summary?engine=kimi"
```
Esta función **no se ha probado contra la API real** (solo con simulaciones). Ver `ai-service/README.md`.
