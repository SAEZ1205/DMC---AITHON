"""Backend del MVP: API REST en /api y archivos de frontend/ como estáticos en el mismo puerto."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
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


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
