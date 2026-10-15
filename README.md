# DMC — IATHON

MVP del reto **DMC Institute × IATHON**: asistencia efectiva, diagnóstico temprano de riesgo de deserción y rescate académico, sobre una réplica del portal de DMC.

**Demo en vivo:** https://dmc-aithon.vercel.app

## Problemática

DMC Institute dicta cursos online en vivo (Zoom) para profesionales. La deserción ronda el **15 % mensual** en programas largos y se concentra entre el 4.º y el 5.º mes. La causa principal no es falta de interés, sino **falta de tiempo** (nuevo empleo, cambio de horario): con asistencia mínima obligatoria y horario fijo, quien no puede conectarse en vivo termina abandonando.

Hoy Moodle **no registra** quién ve las grabaciones, las encuestas no diagnostican (quien abandona no responde) y el certificado exige un % de asistencia en vivo sin una vía clara de recuperación equivalente.

## Objetivo

Demostrar un MVP que:

1. Mida **asistencia efectiva** (en vivo **o** recuperación con evidencia).
2. Detecte a tiempo a alumnos **en riesgo** con señales de conducta real (no solo encuestas).
3. Active un **rescate en 2 niveles** (aviso automático → contacto humano).
4. Use las **grabaciones** como espacio de aprendizaje (chat tutor, capítulos, quiz, video).

## Propuesta de solución

Cuatro pilares sobre el frontend existente de DMC y un backend FastAPI:

| Pilar | Qué hace |
|---|---|
| **Clase / grabación** | Video (o reproductor de transcripción), capítulos, resumen, «Lo que te perdiste», chat tutor con citas al minuto, quiz de la sesión y micro-preguntas. |
| **Asistencia efectiva** | Una sesión cuenta si el alumno asistió en vivo **o** la recuperó en 7 días: vio los **tramos clave** y aprobó el **quiz**. El umbral del certificado es configurable por curso (por defecto 80 %). |
| **Diagnóstico y rescate** | Variables de Zoom, plataforma y chat; 4 estados de riesgo; nivel 1 automático y nivel 2 con el asistente académico si no hay respuesta en 48 h. |
| **Plataforma** | «Mi avance», grabaciones y tablero del tutor (alertas, tramos clave, quiz, cola nivel 2). |

La demo incluye una **cohorte sintética** (~30 alumnos × 52 sesiones) y una **sesión real** (sesión 23) con transcripción, materiales y video en Vercel Blob. El LLM (Groq, `openai/gpt-oss-120b`) refuerza el chat tutor y la asistencia del quiz; sin key o sin cupo cae a un modo simulado estable para demos.

Documentación detallada:

- [`docs/SOLUCION_DMC_CURSOR.md`](docs/SOLUCION_DMC_CURSOR.md) — especificación v1
- [`docs/SOLUCION_DMC_CURSOR_V2.md`](docs/SOLUCION_DMC_CURSOR_V2.md) — especificación v2
- [`docs/DECISIONES.md`](docs/DECISIONES.md) — decisiones de implementación
- [`backend/README.md`](backend/README.md) — arranque local, variables, despliegue y guion de demo

## Demo publicada

| | |
|---|---|
| URL | https://dmc-aithon.vercel.app |
| Stack | FastAPI + frontend estático en Vercel, Postgres (Neon), video en Vercel Blob, LLM Groq |
| Entrada | **Entrar como alumno** / **Entrar como tutor** (usuarios de demo en la pantalla de login) |

Tras un despliegue con cambios de datos o de modelos, el staff puede reiniciar la demo con `POST /api/admin/reset-demo`.

## Cómo correr en local

Requisitos: **Python 3.12+**. Dependencias en [`requirements.txt`](requirements.txt) (raíz) y [`backend/requirements.txt`](backend/requirements.txt).

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Opcional: pega tu LLM_API_KEY de Groq. Sin key, el MVP usa modo simulado.
python scripts/seed_demo.py
uvicorn app.main:app --reload --port 8000
```

Abre http://localhost:8000.

Las variables sensibles (`LLM_API_KEY`, `DATABASE_URL`, tokens de Blob) van solo en `backend/.env` (local) o en las variables de entorno de Vercel. **Nunca las subas al repositorio.** Usa `backend/.env.example` como plantilla.

## Estructura

| Carpeta | Contenido |
|---|---|
| `frontend/` | Portal DMC + vistas del MVP (grabación, avance, tutor) |
| `backend/` | API, motores de asistencia/diagnóstico/alertas, cohorte, LLM |
| `docs/` | Especificación y decisiones |
| `ai-service/` | Reservado por el repo base (no usado por este MVP) |
| `requirements.txt` | Dependencias Python del MVP |

## Licencia

Este proyecto se publica bajo la licencia [MIT](LICENSE): apta para un hackathon y para que DMC o terceros puedan evaluar, adaptar o reutilizar el código con atribución.
