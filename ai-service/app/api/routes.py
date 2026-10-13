from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.idempotency import IdempotencyKey, run_idempotent
from app.models.api import (
    ErrorResponse, HealthResponse, KnowledgeHealth, LessonInfo, LLMHealth, StorageHealth,
)
from app.models.chat import ChatRequest, ChatResponse, CourseInfo
from app.services.tutor_service import TutorService

router = APIRouter()

_ERRORS = {
    404: {"model": ErrorResponse, "description": "Curso, clase o sesión inexistente"},
    403: {"model": ErrorResponse, "description": "La sesión no pertenece al estudiante, curso o clase indicados"},
    422: {"model": ErrorResponse, "description": "Solicitud inválida"},
    500: {"model": ErrorResponse, "description": "Error interno"},
    503: {"model": ErrorResponse, "description": "Almacenamiento no disponible"},
}


def _service(request: Request) -> TutorService:
    return request.app.state.service


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["Estado"],
    summary="Estado del backend",
    responses={503: {"model": HealthResponse, "description": "Almacenamiento con fallos (degraded)"}},
)
def health(request: Request):
    service = _service(request)
    store = service.store

    try:
        stats = store.stats()
        storage = StorageHealth(status="ok", database_file=store.db_path.name, **stats)
    except Exception as exc:  # el estado debe poder informarse aunque la base falle
        storage = StorageHealth(status="error", database_file=store.db_path.name, error=type(exc).__name__)

    retriever = service.retriever
    knowledge = KnowledgeHealth(
        courses=len(retriever.list_courses()),
        lessons=sum(len(retriever.list_lessons(c.course_id)) for c in retriever.list_courses()),
        load_errors=len(retriever.load_errors),
    )
    selection = request.app.state.llm_selection
    config = request.app.state.llm_config
    budget = getattr(service.provider, "_budget", None)
    llm = LLMHealth(
        active_provider=service.provider.name,
        configured_provider=selection.configured,
        mode=service.provider.mode,
        selection_reason=selection.reason,
        model=config.model if service.provider.mode == "LIVE" else None,
        live_authorized=config.live_authorized,
        api_key_configured=bool(config.api_key),
        fallback_to_demo=service.fallback_provider is not None,
        live_calls_used=budget.used if budget else None,
        live_calls_limit=budget.limit if budget else None,
        provider_status=getattr(service.provider, "provider_status", lambda: None)(),
        class_content_authorized=config.allow_class_content if getattr(service.provider, "name", "") == "nvidia-kimi" else None,
        egress_guard=getattr(service.provider, "egress_guard_status", lambda: None)(),
    )
    ok = storage.status == "ok"
    body = HealthResponse(
        status="ok" if ok else "degraded",
        version=__version__,
        mode=service.provider.mode,
        llm_provider=service.provider.name,
        llm_provider_configured=selection.configured,
        llm=llm,
        real_lesson=(lambda r: None if r is None else {
            "loaded": True, "lesson_id": r.lesson.lesson_id, "segments": len(r.lesson.segments),
            "video_available": r.video_path is not None, "transcript": r.report.public()})(request.app.state.real_lesson),
        storage=storage,
        knowledge=knowledge,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    if ok:
        return body
    return JSONResponse(status_code=503, content=body.model_dump())


@router.get("/api/courses", response_model=List[CourseInfo], tags=["Catálogo"], summary="Cursos disponibles")
def list_courses(request: Request):
    return _service(request).list_courses()


@router.get(
    "/api/courses/{course_id}/lessons",
    response_model=List[LessonInfo],
    tags=["Catálogo"],
    summary="Clases con transcripción de un curso",
    responses={404: _ERRORS[404]},
)
def list_lessons(course_id: str, request: Request):
    return _service(request).list_lessons(course_id)


@router.post(
    "/api/chat",
    response_model=ChatResponse,
    tags=["Asistente"],
    summary="Enviar un mensaje al tutor",
    description=(
        "Responde SOLO con lo que dice la transcripción recuperada y cita los minutos usados. "
        "Si `session_id` es null se inicia una conversación; para continuarla, reenvíe el `session_id` "
        "devuelto con el mismo estudiante, curso y clase.\n\n"
        "**Importante:** `student_id` es un identificador sintético de demostración, NO una credencial; "
        "el backend aún no autentica usuarios. Use únicamente datos ficticios."
    ),
    responses={k: _ERRORS[k] for k in (403, 404, 422, 500, 503)},
)
def chat(payload: ChatRequest, request: Request, idempotency_key: IdempotencyKey = None):
    return run_idempotent(request, "chat", payload.student_id, idempotency_key, payload.model_dump(),
                          lambda scope: _service(request).chat(payload, idem_scope=scope))
