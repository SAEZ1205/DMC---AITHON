"""
Idempotencia de las peticiones POST mediante la cabecera opcional `Idempotency-Key`.

Si el cliente reintenta una petición con la misma clave (y el mismo cuerpo) se devuelve la respuesta ORIGINAL
guardada, sin repetir efectos (mensajes, intentos, eventos de telemetría). Reutilizar la clave con un cuerpo
distinto es un error. Sin cabecera, la petición se procesa normalmente.
"""
import hashlib
import json
from typing import Annotated, Callable, Optional

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from app.services.errors import AppError

IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9_.:-]{8,64}$"
IdempotencyKey = Annotated[
    Optional[str],
    Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_PATTERN,
           description="Opcional. Clave única (8-64 caracteres) para reintentar la petición sin duplicar efectos."),
]


def run_idempotent(request: Request, endpoint: str, student_id: str, key: Optional[str], payload: dict,
                   handler: Callable[[Optional[str]], object]):
    """Ejecuta `handler(scope)` (scope=None si no hay clave) o reproduce la respuesta guardada."""
    if not key:
        return handler(None)
    store = request.app.state.academic_store
    scope = f"{endpoint}:{student_id}:{key}"
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    stored = store.get_idempotent(scope)
    if stored is not None:
        if stored["request_hash"] != fingerprint:
            raise AppError(422, "idempotency_key_reuse",
                           "Esa Idempotency-Key ya se usó con una petición distinta. Usa una clave nueva.")
        return JSONResponse(json.loads(stored["body"]), status_code=stored["status_code"],
                            headers={"Idempotent-Replay": "true"})

    result = handler(scope)
    store.save_idempotent(scope, fingerprint, 200, json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    return result
