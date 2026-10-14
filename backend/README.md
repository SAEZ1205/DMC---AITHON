# Backend del MVP — Asistencia efectiva DMC

FastAPI + SQLite que sirve el frontend estático en el mismo puerto (`http://localhost:8000`). Toda la lógica de asistencia efectiva, diagnóstico, rescate y chat sobre la grabación vive aquí.

Especificación: [`../docs/SOLUCION_DMC_CURSOR.md`](../docs/SOLUCION_DMC_CURSOR.md). Decisiones: [`../docs/DECISIONES.md`](../docs/DECISIONES.md).

## Requisitos

- Python 3.11+ (probado en Windows 10/11 con PowerShell).
- Opcional: API key de [Moonshot / Kimi](https://platform.moonshot.ai) para el LLM real. Sin key el sistema corre en modo simulado.

## Instalación

Desde la raíz del repositorio:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edita `backend/.env`:

| Variable | Uso |
|---|---|
| `LLM_API_KEY` | Key de Groq (`gsk_…`, en https://console.groq.com/keys). Déjala en `...` o vacía para modo mock. |
| `LLM_BASE_URL` | Por defecto `https://api.groq.com/openai/v1`. |
| `LLM_MODEL_CHAT` / `LLM_MODEL_FAST` | Por defecto `openai/gpt-oss-120b`. |
| `LLM_REASONING_EFFORT` | `low` (por defecto): el razonamiento de gpt-oss cuenta como tokens del cupo. |
| `LLM_RPM` / `LLM_RPD` / `LLM_TPM` / `LLM_TPD` | Cupo del proveedor. Por defecto el plan gratuito de Groq para gpt-oss-120b: 30 / 1000 / 8000 / 200000. |
| `LLM_SAFETY` | Fracción del cupo que usa la app (0.85). |
| `LLM_MAX_WAIT_S` | Segundos que una solicitud espera cupo antes de responder con la simulación (6). |
| `LLM_MOCK` | `1` fuerza mock aunque haya key. |
| `SIM_NOW` | Reloj de la demo (`2026-10-12T19:00:00`). Vacío = hora real. |
| `DISABLE_SCHEDULER` | `1` desactiva el recálculo horario (útil en desarrollo). |
| `DATABASE_URL` | Vacío = SQLite en `data/dmc.db`. Con `postgres://…` (Neon) usa Postgres; cárgale la demo con `python scripts/load_snapshot.py` después de correr `seed_demo.py` sin esta variable. |

## Base de datos de demo

```powershell
# Desde backend/, con el venv activo
python scripts/seed_demo.py
```

Crea `data/dmc.db` con:

- 1 curso (52 sesiones, martes y jueves 19:30–22:30).
- 30 alumnos de la cohorte sintética + docente, tutor, coordinador y **Michela López** (Asistente Académica).
- Contenido de clase tomado de `data/sample/session_demo/` (o de `data/zoom/<id>/` si existen exportaciones reales).
- Recálculo día a día de las 4 semanas previas a `SIM_NOW`.

Opciones: `--regen` regenera la cohorte; `--students 30 --weeks 26 --seed 42`.

Para generar solo la cohorte:

```powershell
python scripts/generate_cohort.py --students 30 --weeks 26 --seed 42
```

## Procesar una sesión (sin LLM)

```powershell
python scripts/process_session.py 23         # una sesión
python scripts/process_session.py --all      # todas
```

Si existe `data/zoom/<session_id>/` se ingesta primero (participantes, chat, transcripción, video y, con `transcript.txt` o `materials/`, el contenido real). Luego se recalculan capítulos (diapositivas + TextTiling), tramos clave y el borrador inicial del quiz; los capítulos quedan sin validar. El LLM solo se usa en línea: tutor del chat, micro-preguntas y «Sugerir preguntas» del quiz.

## LLM (Groq) y su cupo

El tutor del chat, las micro-preguntas y el asistente del quiz usan `openai/gpt-oss-120b` en Groq. El plan gratuito permite 30 solicitudes y 8.000 tokens por minuto, y 1.000 solicitudes y 200.000 tokens por día. Un turno del tutor gasta ~1.500–2.000 tokens: unos 4 turnos seguidos y luego uno cada ~15 s.

Para que la demo no se caiga:

- Cada llamada reserva cupo antes de salir (85 % de cada límite; los tokens se recargan de forma continua, como en Groq) y se ajusta con los encabezados `x-ratelimit-*` de la respuesta.
- Si no hay cupo en 6 s, o Groq responde 429 (la app se pausa lo que indique `retry-after`), o hay un error o timeout (25 s), el servicio responde al instante con su **simulación** (la misma lógica del modo mock). El alumno nunca ve un error.
- Responder un intento usa una sola llamada (evalúa y sigue con el nivel siguiente) y el prompt del tutor lleva ~1K tokens de contexto.
- Las respuestas validadas se guardan en caché 6 h por prompt exacto: repetir la pregunta de la demo no gasta cupo.
- Estado en vivo (staff): `GET /api/admin/llm` → modo, cupo disponible del minuto, uso del día y veces que se respondió con la simulación.

```powershell
curl.exe https://dmc-aithon.vercel.app/api/admin/llm -H "X-User-Id: 2"
```

El control de cupo vive en cada instancia; con varias instancias en Vercel los encabezados de Groq (que cuentan a toda la organización) lo corrigen y el 429 queda como red de seguridad. `eval_chat.py` con la key real gasta cupo: córrelo con `LLM_MOCK=1` salvo que quieras medir el modelo.

## Arrancar

```powershell
# Desde backend/
$env:DISABLE_SCHEDULER = "1"   # opcional en desarrollo
uvicorn app.main:app --reload --port 8000
```

Abre `http://localhost:8000`. Las rutas `/api/*` son la API; el resto sirve `../frontend`.

Identidad de demo: header `X-User-Id` (o `?uid=` en beacons). Los botones del login lo resuelven solos.

## Verificación

```powershell
pytest -q
python scripts/evaluate.py
python scripts/check_attendance.py
python scripts/eval_chat.py                  # sesión real (23); trabaja sobre una copia de dmc.db
```

Metas (sección 12 de la especificación):

| Criterio | Cómo se verifica |
|---|---|
| Pytest | `pytest` → 90 tests |
| Diagnóstico | `evaluate.py`: recall ≥ 80 % (≥ 14 días), falsas alarmas ≤ 20 %, adaptados no en riesgo |
| Asistencia efectiva | `check_attendance.py`: 5 perfiles + 1 recuperación fuera de plazo coinciden con el motor |
| Chat tutor | `eval_chat.py`: minuto citado a ±90 s ≥ 16/20, primer turno con pregunta y ≤ 90 palabras 20/20, quiz revelado 0/5, fuera de la clase 5/5 (mock: 16 · 20 · 0 · 5) |
| Alertas 48 h | `tests/test_alerts.py` con reloj simulado |
| Páginas existentes | sin cambios en `dmc.css` / `styles.css` / `enhancements.css` / `fine-tuning.css` |

## Guion de demo (sección 13)

Reloj fijo: `SIM_NOW=2026-10-12T19:00:00`. Si tocaste la base durante pruebas, vuelve a correr `python scripts/seed_demo.py` con el servidor detenido.

1. **Alumno con cambio de horario**  
   En `http://localhost:8000` → **Entrar como alumno**. Entra Plinio Núñez.  
   - Campana con 1 aviso de nivel 1.  
   - **Mi avance**: proyección ~67 % (&lt; 80 %), 2 clases por recuperar (sesiones 23 y 24) y los minutos reales de tramos clave que faltan (~160 min, ~107 min a 1.5×).  
   - Abrir la **sesión 23** (sesión real): capítulos con títulos de las diapositivas, pestaña **Resumen**, «Lo que te perdiste», interruptor de tramos clave.  
   - **Chat tutor:** «¿Qué hace Docker?» → nivel 1 (ubica el minuto y la diapositiva) → «Dame una pista» → nivel 2 → responder en el mismo hilo y ver la evaluación. «¿Cuándo vence esta sesión?» responde con tus datos (sin LLM). «¿Cuál es la capital de Francia?» → «Eso no se vio en esta clase…».  
   - Quiz ≥ 3/5. Tras ver tramos + aprobar, la sesión pasa a **Recuperada** y la proyección sube (persiste al recargar).

2. **Tutor / Asistente Académica**  
   Cerrar sesión → **Entrar como tutor** (Michela López).  
   - Contadores: ~18 activos, 6 en adaptación, 6 en riesgo, 1 alerta de nivel 2.  
   - Tabla con motivos en lenguaje claro; filtrar por estado.  
   - **Cola de nivel 2** (p. ej. Fabricio Garrido) → registrar contacto (canal, resultado, cerrar).  
   - Pestaña **Tramos clave** → sesión 23: editar título/inicio/fin y validar; debajo, «Temas que más cuestan» (hilos simulados del tutor).  
  - Pestaña **Quiz** → sesión 23: 5 preguntas aprobadas y 3 borradores «propuesta IA» para revisar y aprobar una por una.  
  - Al terminar: `POST /api/admin/reset-demo` para dejar la demo como al inicio.

3. **Vista móvil**  
   DevTools ≤ 390 px en `progress.html` / `recording.html` / `tutor.html`: menú hamburguesa, avance y recuperación usables.

## Despliegue en Vercel

Demo pública: **https://dmc-aithon.vercel.app** (proyecto `dmc-aithon`, desplegado con la CLI desde la rama `mateo`, sin integración Git).

- `pyproject.toml` (raíz) apunta a `backend.app.main:app` y declara las dependencias (incluye `psycopg[binary]`, `python-pptx`, `pypdf`, `python-docx`). El frontend se sirve con el `StaticFiles` de FastAPI.
- **Base de datos: Neon Postgres** (Vercel Marketplace, recurso `dmc-aithon-db`), conectada al proyecto; la integración define `DATABASE_URL`. Los cambios (quiz, avance, chat, contactos) **persisten** entre recargas e instancias.
- El build (`backend/scripts/vercel_build.py`) genera la cohorte y la base de demo `backend/data/dmc.db` (SQLite, con la sesión real en la 23) y, si Neon está vacío, la copia allí.
- **Volver al estado inicial:** `POST /api/admin/reset-demo` con un usuario staff (p. ej. `X-User-Id` de Michela). Recrea las tablas y copia la base de demo del despliegue en ~2 s. Hazlo **después de cada despliegue** con cambios de datos o de modelos, y antes de presentar.
- **Sesión real en la demo publicada:** `.vercelignore` deja subir `backend/data/zoom/23/` (transcripción, materiales, quiz y `metadata.json`; nunca el mp4) para que el build la cargue. Esos archivos van al despliegue, no a Git, así que solo se puede redesplegar desde una máquina que los tenga.
- **Video de la sesión 23:** está comprimido (1280×828, 181 MB) en **Vercel Blob** (almacén público `dmc-aithon-video`, conectado a Producción). `metadata.json` lo apunta con `video_url` y el reproductor lo carga directo desde Blob, con rangos HTTP. Para reemplazarlo: `vercel blob put <archivo.mp4> --access public --pathname sesion23/recording-720p.mp4 --allow-overwrite true --rw-token <BLOB_READ_WRITE_TOKEN de Producción>`, y luego redesplegar y hacer `reset-demo`.
- El recálculo horario (APScheduler) se desactiva en Vercel.
- Variables en Producción: `SIM_NOW`, `DATABASE_URL` (Neon) y `LLM_API_KEY` (Groq, tipo secreto; el resto de `LLM_*` usa los valores por defecto). Para cambiar la key: `vercel env rm LLM_API_KEY production`, `vercel env add LLM_API_KEY production` y volver a desplegar. El build siempre siembra con `LLM_MOCK=1`.

Redesplegar y reiniciar la demo (desde la raíz del repo, en la rama `mateo`):

```powershell
vercel deploy --prod
curl.exe -X POST https://dmc-aithon.vercel.app/api/admin/reset-demo -H "X-User-Id: 2"
```

## Estructura

```
backend/
  app/
    main.py          # FastAPI + StaticFiles(html=True)
    config.py        # umbrales, SIM_NOW, LLM
    db.py, models.py
    routers/         # auth, student, sessions, tutor, admin
    services/        # attendance, features, risk, alerts, llm, zoom_ingest…
    prompts/         # markdown en español
  scripts/           # seed_demo, generate_cohort, process_session, evaluate…
  tests/
  data/              # dmc.db, cohort/, zoom/, sample/ (no versionar salvo sample/)
  .env.example
```

## Notas

- Todo el trabajo de este MVP está en la rama `mateo`. No hacer push a `main`.
- No subir `.env`, `*.db` ni grabaciones/transcripciones fuera de `data/sample/`.
- Otros proveedores compatibles con OpenAI (Kimi, Gemini, OpenRouter): cambiar `LLM_BASE_URL`, key, modelo y el cupo `LLM_RPM/RPD/TPM/TPD` en `.env`.
