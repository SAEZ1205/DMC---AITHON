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
   - **Mi avance**: proyección ~67 % (&lt; 80 %), 2 clases por recuperar (sesiones 23 y 24).  
   - Abrir la sesión 24 → banner de plazo, «Lo que te perdiste», chat (pregunta algo de n8n), interruptor de tramos clave, quiz ≥ 3/5.  
   - Tras ver tramos + aprobar, la sesión pasa a **Recuperada** y la proyección sube.

2. **Tutor / Asistente Académica**  
   Cerrar sesión → **Entrar como tutor** (Michela López).  
   - Contadores: ~18 activos, 6 en adaptación, 6 en riesgo, 1 alerta de nivel 2.  
   - Tabla con motivos en lenguaje claro; filtrar por estado.  
   - **Cola de nivel 2** (p. ej. Fabricio Garrido) → registrar contacto (canal, resultado, cerrar).  
   - Pestaña **Tramos clave** → validar los de una sesión; debajo, «Temas que más cuestan» (hilos simulados del tutor).

3. **Vista móvil**  
   DevTools ≤ 390 px en `progress.html` / `recording.html` / `tutor.html`: menú hamburguesa, avance y recuperación usables.

## Despliegue en Vercel

Demo pública: **https://dmc-aithon.vercel.app** (proyecto `dmc-aithon`, desplegado con la CLI desde la rama `mateo`, sin integración Git).

- `pyproject.toml` (raíz) apunta a `backend.app.main:app` y declara las dependencias. El frontend se sirve desde el CDN gracias al `StaticFiles` de FastAPI.
- El build (`backend/scripts/vercel_build.py`) genera la cohorte y `backend/data/dmc.db` con el reloj de la demo.
- Al arrancar, la función copia esa base a `/tmp/dmc.db` (el bundle es de solo lectura). Los cambios (quiz, contactos, chat) duran mientras viva la instancia; tras unos minutos sin tráfico la demo vuelve al estado inicial.
- El recálculo horario (APScheduler) se desactiva en Vercel.
- Variables: `SIM_NOW` en Producción. Para usar Kimi, agrega `LLM_API_KEY` con `vercel env add LLM_API_KEY production` y vuelve a desplegar.
- `.vercelignore` excluye `.venv`, `.env`, tests, datos de Zoom/cohorte y la base local.

Redesplegar (desde la raíz del repo, en la rama `mateo`):

```powershell
vercel deploy --prod
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
