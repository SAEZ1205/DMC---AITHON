"""Backend del MVP: API REST en /api y archivos de frontend/ como estáticos en el mismo puerto."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from . import config
from .db import engine, init_db
from .models import ClassSession
from .routers import admin, auth, sessions, student, tutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dmc")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    scheduler = None
    if config.SCHEDULER_ENABLED:
        from .services.scheduler import start_scheduler

        scheduler = start_scheduler()
    log.info("LLM en modo %s", "simulado (sin API key)" if config.LLM_MOCK else f"real ({config.LLM_MODEL_CHAT})")
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="DMC – Asistencia efectiva y rescate", lifespan=lifespan)

for router in (auth.router, student.router, sessions.router, tutor.router, admin.router):
    app.include_router(router, prefix="/api")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/index.html")


@app.get("/media/{session_id}.mp4", include_in_schema=False)
def media(session_id: int):
    """Video local de la sesión, con soporte de rangos HTTP (necesario para saltar a un minuto)."""
    with Session(engine) as db:
        s = db.get(ClassSession, session_id)
    if not s or not s.video_path or not Path(s.video_path).exists():
        raise HTTPException(404, "La sesión no tiene video local")
    return FileResponse(s.video_path, media_type="video/mp4")


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
