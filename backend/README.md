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
| `LLM_API_KEY` | Key de Kimi. Déjala en `...` o vacía para modo mock. |
| `LLM_BASE_URL` | Por defecto `https://api.moonshot.ai/v1`. |
| `LLM_MODEL_CHAT` / `LLM_MODEL_FAST` | Por defecto `kimi-k2.6` (confirma el nombre en tu cuenta). |
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
| Pytest | `pytest` → 75 tests |
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
- **Sesión real en la demo publicada:** `.vercelignore` deja subir `backend/data/zoom/23/` (transcripción, materiales y quiz; nunca el video) para que el build la cargue. Esos archivos van al despliegue, no a Git, así que solo se puede redesplegar desde una máquina que los tenga. Sin video en Vercel: la sesión 23 usa el reproductor de transcripción.
- El recálculo horario (APScheduler) se desactiva en Vercel.
- Variables: `SIM_NOW` y `DATABASE_URL` (Neon) en Producción. Para usar Kimi, agrega `LLM_API_KEY` con `vercel env add LLM_API_KEY production` y vuelve a desplegar.

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
- Alternativas de LLM (Groq, Gemini, OpenRouter): cambiar solo `LLM_BASE_URL` + key + modelo en `.env`.
