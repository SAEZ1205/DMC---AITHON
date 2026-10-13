"""Manejadores de excepciones: toda respuesta de error usa el mismo formato estructurado."""
import logging
import sqlite3
from typing import Any, List

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import AppError

logger = logging.getLogger(__name__)

_VALIDATION_MESSAGES = {
    "missing": "Campo obligatorio.",
    "string_too_short": "El texto no puede estar vacío.",
    "string_too_long": "El texto supera la longitud máxima permitida.",
    "string_pattern_mismatch": "Formato no válido: use letras, números, '-', '_', '.' o ':' (máx. 64 caracteres).",
    "string_type": "Debe ser un texto.",
    "json_invalid": "El cuerpo de la petición no es un JSON válido.",
}

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def _validation_details(errors: List[dict]) -> List[dict]:
    # Se omite `input` a propósito: no se devuelve de vuelta el contenido enviado por el cliente.
    return [
        {
            "field": ".".join(str(p) for p in err.get("loc", ())[1:]) or "body",
            "message": _VALIDATION_MESSAGES.get(err.get("type", ""), err.get("msg", "Valor no válido.")),
        }
        for err in errors
    ]


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "La solicitud no es válida.", _validation_details(exc.errors())),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=error_body(code, str(exc.detail)))

    @app.exception_handler(sqlite3.Error)
    async def _storage_error(_: Request, exc: sqlite3.Error):
        logger.error("Error de almacenamiento: %s", exc)
        return JSONResponse(
            status_code=503,
            content=error_body("storage_unavailable", "El almacenamiento no está disponible. Inténtalo de nuevo."),
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        logger.exception("Error no controlado", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "Ocurrió un error interno. Inténtalo de nuevo más tarde."),
        )
