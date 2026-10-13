"""
Rutas de la demostración con la clase real: metadatos, reproducción local del video y acciones administrativas.

Seguridad del video
  * El archivo NO se elige por parámetro: la ruta sale del registro interno (`app.state.real_lesson`), validada al cargar
    (dentro de `demo-assets/`). El `lesson_id` de la URL solo se compara con el identificador registrado.
  * Se sirve en streaming con soporte HTTP Range (`FileResponse` de Starlette): nunca se carga completo en memoria.
  * No se expone la ruta local en ninguna respuesta.
"""
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse

from app.knowledge.real_lesson import REAL_COURSE_ID, REAL_LESSON_ID
from app.llm.errors import LLMError
from app.models.chat import ID_PATTERN
from app.services.errors import AppError
from app.services.live_summary import LiveSummaryGenerator

logger = logging.getLogger(__name__)
router = APIRouter()

VIDEO_URL = "/api/demo/lessons/{lesson_id}/video"


def _real(request: Request):
    real = request.app.state.real_lesson
    if real is None:
        raise AppError(404, "real_lesson_not_available",
                       "La clase real no está cargada. Coloca clase-demo.mp4 y transcripcion-original.txt en demo-assets/ y reinicia.")
    return real


@router.get("/api/demo/lesson", tags=["Demo"], summary="Metadatos de la clase real de demostración (sin rutas locales)")
def demo_lesson(request: Request):
    real = _real(request)
    summaries = request.app.state.summaries
    stored = {engine: summaries.peek(real.lesson, engine) for engine in ("extractive", "kimi")}
    info = real.public_info()
    info["video_url"] = VIDEO_URL.format(lesson_id=REAL_LESSON_ID) if real.video_path else None
    info["summary"] = {
        "available": any(stored.values()),
        "engines": {e: (None if s is None else {"generated_at": s.generated_at}) for e, s in stored.items()},
        "endpoint": f"/api/courses/{REAL_COURSE_ID}/lessons/{REAL_LESSON_ID}/summary?engine=best",
    }
    info["chat"] = {"course_id": REAL_COURSE_ID, "lesson_id": REAL_LESSON_ID, "endpoint": "/api/chat"}
    info["mode"] = request.app.state.service.provider.mode
    return info


@router.head("/api/demo/lessons/{lesson_id}/video", include_in_schema=False)
@router.get(
    "/api/demo/lessons/{lesson_id}/video", tags=["Demo"],
    summary="Video de la clase real (streaming con HTTP Range)",
    responses={206: {"description": "Contenido parcial (Range)"}, 404: {"description": "Clase o video no disponible"}},
)
def demo_video(lesson_id: str, request: Request):
    real = request.app.state.real_lesson
    if real is None or real.video_path is None or lesson_id != REAL_LESSON_ID:
        raise AppError(404, "video_not_available", "No hay video disponible para esa clase.")
    path = real.video_path
    if not path.is_file() or not path.is_relative_to(real.assets_dir):
        raise AppError(404, "video_not_available", "No hay video disponible para esa clase.")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600", "Content-Disposition": "inline"})


@router.post(
    "/api/admin/lessons/{course_id}/{lesson_id}/summary", tags=["Administración"],
    summary="(Admin) Generar o regenerar el resumen persistido de una clase",
    responses={401: {"description": "Token ausente o inválido"}, 403: {"description": "Acciones administrativas deshabilitadas"}},
    description=(
        "Requiere `X-Admin-Token` (deshabilitado si el servidor no define ADMIN_API_TOKEN). `engine=extractive` recalcula el "
        "resumen heurístico (gratis). `engine=kimi` genera el resumen con Kimi POR FRAGMENTOS: **hace llamadas de pago** y solo "
        "funciona con LLM_LIVE_AUTHORIZED=true. Sin `force`, si ya existe uno vigente para esta versión de la transcripción no se "
        "genera nada."
    ),
)
def admin_summary(course_id: str, lesson_id: str, request: Request, engine: str = "extractive", force: bool = False,
                  x_admin_token: Optional[str] = Header(default=None)):
    _check_admin(request, x_admin_token)
    if engine not in ("extractive", "kimi"):
        raise AppError(422, "invalid_engine", "engine debe ser 'extractive' o 'kimi'.")
    lessons = request.app.state.lessons
    lesson = lessons._lesson(course_id, lesson_id)
    summaries = request.app.state.summaries
    if engine == "extractive":
        stored = summaries.get_or_create(lesson, "extractive", lambda: lessons._extractive_payload(lesson), force=force)
    else:
        generator: Optional[LiveSummaryGenerator] = request.app.state.live_summary
        if generator is None:
            raise AppError(409, "llm_not_available",
                           "El resumen con Kimi requiere LLM_PROVIDER=kimi, LLM_API_KEY y LLM_LIVE_AUTHORIZED=true. No se hizo ninguna llamada.")
        try:
            stored = summaries.get_or_create(lesson, "kimi", lambda: generator.generate(lesson), force=force)
        except LLMError as exc:                  # todo o nada: no se guarda un resumen parcial
            logger.warning("Generación del resumen con Kimi abortada: code=%s upstream=%s", exc.code, exc.upstream_status)
            raise AppError(exc.http_status, exc.code, exc.public_message,
                           details={"provider": "kimi", "upstream_status": exc.upstream_status, "saved": False}) from exc
    return {
        "course_id": course_id, "lesson_id": lesson_id, "engine": stored.engine, "cached": stored.cached,
        "generated_at": stored.generated_at, "transcript_sha256_short": stored.fingerprint[:12],
        "pipeline_version": stored.pipeline_version, "generator": stored.generator_meta,
    }


def _check_admin(request: Request, presented: Optional[str]) -> None:
    expected = request.app.state.academic_config.admin_api_token
    if not expected:
        raise AppError(403, "admin_disabled", "Las acciones administrativas están deshabilitadas: define ADMIN_API_TOKEN.")
    if not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise AppError(401, "admin_unauthorized", "Token administrativo ausente o inválido.")
