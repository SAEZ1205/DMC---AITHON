"""Recálculo periódico de estados y alertas (APScheduler, cada hora)."""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from .. import config
from ..db import engine
from ..models import Course
from .recompute import recompute_course

log = logging.getLogger("dmc.scheduler")


def recompute_all() -> None:
    with Session(engine) as db:
        for course in db.exec(select(Course)).all():
            result = recompute_course(db, course.id)
            log.info("Recálculo del curso %s: %d alumnos", course.id, len(result))


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="America/Lima")
    scheduler.add_job(recompute_all, "interval", minutes=config.RECOMPUTE_EVERY_MINUTES,
                      next_run_time=datetime.now(), id="recompute", max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
