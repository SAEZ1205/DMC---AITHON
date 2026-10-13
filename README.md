# DMC — IATHON

Réplica del portal de DMC Institute con el MVP de **asistencia efectiva**, diagnóstico y rescate (rama `mateo`).

## Cómo correr el MVP

El backend FastAPI sirve el frontend en el mismo puerto. Instrucciones completas, variables de entorno y guion de demo:

→ **[`backend/README.md`](backend/README.md)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python scripts/seed_demo.py
uvicorn app.main:app --reload --port 8000
```

Abre `http://localhost:8000` y usa **Entrar como alumno** / **Entrar como tutor**.

Documentación:

- [`docs/SOLUCION_DMC_CURSOR.md`](docs/SOLUCION_DMC_CURSOR.md) — especificación del reto
- [`docs/DECISIONES.md`](docs/DECISIONES.md) — decisiones de implementación

## Estructura

| Carpeta | Contenido |
|---|---|
| `frontend/` | Portal DMC (páginas existentes + vistas del MVP: grabación, avance, seguimiento) |
| `backend/` | API, motores de asistencia/diagnóstico/alertas, cohorte sintética, LLM |
| `docs/` | Especificación y decisiones |
| `ai-service/` | Reservado por el repo base (no usado por este MVP) |

## Solo frontend (sin API)

Si solo quieres ver el HTML estático:

```powershell
cd frontend
python -m http.server 5500
```

Sin el backend no hay login de demo, asistencia efectiva ni chat.
