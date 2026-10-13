"""Endpoints de la Etapa 4: resúmenes, capítulos, 'lo que te perdiste', quiz, micro-pregunta y telemetría."""
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Header, Query, Request

from app.api.idempotency import IdempotencyKey, run_idempotent
from app.models.academic import (
    ChaptersResponse, LessonSummaryResponse, MicroAnswerRequest, MicroAnswerResponse, MicroQuestionState,
    MissedContentRequest, MissedContentResponse, QuizStartRequest, QuizStartResponse, QuizSubmitRequest,
    QuizSubmitResponse,
)
from app.models.api import ErrorResponse
from app.models.chat import ID_PATTERN
from app.models.events import EVENT_CATALOG, TELEMETRY_NOTICE, TelemetryEventsResponse, TelemetrySummaryResponse

router = APIRouter()

IdQuery = Annotated[str, Query(pattern=ID_PATTERN)]
OptIdQuery = Annotated[Optional[str], Query(pattern=ID_PATTERN)]

_E = {
    404: {"model": ErrorResponse, "description": "Curso, clase o recurso inexistente"},
    409: {"model": ErrorResponse, "description": "Conflicto (p. ej. intento ya enviado)"},
    422: {"model": ErrorResponse, "description": "Solicitud inválida"},
}


# =================================================================== resumen y capítulos
@router.get(
    "/api/courses/{course_id}/lessons/{lesson_id}/summary", response_model=LessonSummaryResponse,
    tags=["Clases"], summary="Resumen estructurado de una clase", responses={404: _E[404], 422: _E[422]},
    description=(
        "Resumen EXTRACTIVO y determinista: frases textuales de la transcripción con su minuto, conceptos clave y "
        "segmentos usados. `engine=llm` está preparado pero deshabilitado (no hay llamadas facturables). No se "
        "afirma ningún ahorro de tiempo."
    ),
)
def lesson_summary(course_id: str, lesson_id: str, request: Request, engine: str = "extractive",
                   student_id: OptIdQuery = None):
    return request.app.state.lessons.summary(course_id, lesson_id, engine=engine, student_id=student_id)


@router.get(
    "/api/courses/{course_id}/lessons/{lesson_id}/chapters", response_model=ChaptersResponse,
    tags=["Clases"], summary="Capítulos de una clase basados en la transcripción", responses={404: _E[404]},
)
def lesson_chapters(course_id: str, lesson_id: str, request: Request):
    return request.app.state.lessons.chapters(course_id, lesson_id)


@router.post(
    "/api/lessons/missed-content", response_model=MissedContentResponse, tags=["Clases"],
    summary="Lo que te perdiste", responses={404: _E[404], 422: _E[422]},
    description=(
        "Devuelve los segmentos de la transcripción que se solapan con los intervalos de ausencia SIMULADOS recibidos. "
        "Los intervalos duplicados, solapados o contiguos se normalizan (sin repetir contenido); los negativos, invertidos, "
        "vacíos o fuera de la duración de la clase se rechazan con 422. No consulta Zoom ni infiere ausencias."
    ),
)
def missed_content(payload: MissedContentRequest, request: Request, idempotency_key: IdempotencyKey = None):
    return run_idempotent(request, "missed-content", payload.student_id, idempotency_key, payload.model_dump(),
                          lambda scope: request.app.state.lessons.missed_content(payload, idem_scope=scope))


# ========================================================================== quiz
@router.post(
    "/api/quiz/start", response_model=QuizStartResponse, tags=["Quiz"], summary="Iniciar (o retomar) un quiz",
    responses={404: _E[404], 422: _E[422], 429: {"model": ErrorResponse, "description": "Límite de intentos"}},
    description=(
        "Entrega 3-5 preguntas con 4 alternativas SIN la respuesta correcta. Si hay un intento abierto del mismo "
        "estudiante y clase, lo retoma (`resumed=true`). Máximo de intentos por clase configurable."
    ),
)
def quiz_start(payload: QuizStartRequest, request: Request, idempotency_key: IdempotencyKey = None):
    return run_idempotent(request, "quiz-start", payload.student_id, idempotency_key, payload.model_dump(),
                          lambda scope: request.app.state.quiz.start(payload, idem_scope=scope))


@router.post(
    "/api/quiz/submit", response_model=QuizSubmitResponse, tags=["Quiz"], summary="Enviar y calificar un quiz",
    responses={403: _E[404], 404: _E[404], 409: _E[409], 410: _E[404], 422: _E[422]},
    description=(
        "La nota se calcula SIEMPRE en el backend (cualquier campo extra como `score` se rechaza). Un intento solo "
        "puede enviarse una vez. Aprobar NO modifica asistencia ni implica certificación."
    ),
)
def quiz_submit(payload: QuizSubmitRequest, request: Request, idempotency_key: IdempotencyKey = None):
    return run_idempotent(request, "quiz-submit", payload.student_id, idempotency_key, payload.model_dump(),
                          lambda scope: request.app.state.quiz.submit(payload, idem_scope=scope))


# ============================================================== micro-pregunta
@router.get(
    "/api/micro-question", response_model=MicroQuestionState, tags=["Micro-pregunta"],
    summary="¿Corresponde mostrar la micro-pregunta?", responses={404: _E[404], 422: _E[422]},
    description=(
        "Devuelve como máximo UNA pregunta breve y opcional por periodo (por defecto ~14 días desde la fecha de "
        "referencia configurada), después de completar un quiz. `as_of` es solo para demostración/pruebas."
    ),
)
def micro_question(request: Request, student_id: IdQuery, course_id: IdQuery, as_of: Optional[date] = None):
    return request.app.state.micro.state(student_id, course_id, as_of)


@router.post(
    "/api/micro-question/answer", response_model=MicroAnswerResponse, tags=["Micro-pregunta"],
    summary="Responder u omitir la micro-pregunta", responses={404: _E[404], 409: _E[409], 422: _E[422]},
    description=(
        "Guarda por separado el texto expresado y la categoría interpretada por reglas conservadoras "
        "(no es un diagnóstico). No dispara notificaciones externas."
    ),
)
def micro_answer(payload: MicroAnswerRequest, request: Request, idempotency_key: IdempotencyKey = None):
    return run_idempotent(request, "micro-answer", payload.student_id, idempotency_key,
                          payload.model_dump(mode="json"),
                          lambda scope: request.app.state.micro.answer(payload, idem_scope=scope))


# ================================================================= telemetría
@router.get("/api/telemetry/catalog", tags=["Telemetría"], summary="Catálogo cerrado de eventos y aviso al estudiante")
def telemetry_catalog():
    return {"event_types": EVENT_CATALOG, "student_notice": TELEMETRY_NOTICE, "data_scope": "demo_synthetic"}


@router.get(
    "/api/telemetry/summary", response_model=TelemetrySummaryResponse, tags=["Telemetría"],
    summary="Resumen agregado de la actividad del asistente (para el dashboard)",
    description=(
        "SOLO agregados: no incluye identificadores de estudiantes. Datos sintéticos de demostración. No es una "
        "probabilidad de deserción ni mide asistencia institucional. En producción protéjase con autenticación."
    ),
)
def telemetry_summary(request: Request, course_id: OptIdQuery = None, lesson_id: OptIdQuery = None,
                      since: Optional[str] = None, until: Optional[str] = None):
    return request.app.state.telemetry.summary(course_id=course_id, lesson_id=lesson_id, since=since, until=until)


@router.get(
    "/api/telemetry/events", response_model=TelemetryEventsResponse, tags=["Telemetría"],
    summary="Eventos individuales (protegido por token)",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    description=(
        "Requiere la cabecera `X-Telemetry-Token`. Está DESHABILITADO si el servidor no define TELEMETRY_API_TOKEN. "
        "Los estudiantes aparecen como seudónimo (`student_ref`); nunca se devuelve el identificador original. "
        "Paginación con `before`."
    ),
)
def telemetry_events(
    request: Request,
    x_telemetry_token: Annotated[Optional[str], Header()] = None,
    course_id: OptIdQuery = None,
    lesson_id: OptIdQuery = None,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    before: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    svc = request.app.state.telemetry
    svc.check_token(x_telemetry_token)
    return svc.list_events(course_id=course_id, lesson_id=lesson_id, event_type=event_type, since=since,
                           until=until, before=before, limit=limit)
