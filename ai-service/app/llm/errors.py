"""
Errores del proveedor de lenguaje.

`fallback_allowed` indica si tiene sentido degradar a DEMO:
  * Fallos temporales (timeout, red, 5xx, 429, respuesta malformada, tope de llamadas) -> sí.
  * Problemas de configuración (credencial rechazada, petición rechazada) -> NO: se informa
    el error en lugar de fingir que todo funciona.
"""
from typing import Optional


class LLMError(Exception):
    code = "llm_error"
    fallback_allowed = True
    http_status = 502
    public_message = "El proveedor de IA no pudo generar la respuesta."

    def __init__(self, detail: str = "", upstream_status: Optional[int] = None, public_message: Optional[str] = None):
        if public_message is not None:                 # mensaje específico del proveedor (p. ej. NVIDIA), solo para esta instancia
            self.public_message = public_message
        super().__init__(detail or self.public_message)
        self.detail = detail          # solo para registros internos; nunca contiene la clave
        self.upstream_status = upstream_status


class LLMAuthError(LLMError):
    code = "llm_auth_error"
    fallback_allowed = False
    http_status = 502
    public_message = "El proveedor de IA rechazó la credencial configurada (LLM_API_KEY). Revisa la clave y los permisos."


class LLMRequestRejected(LLMError):
    code = "llm_request_rejected"
    fallback_allowed = False
    http_status = 502
    public_message = "El proveedor de IA rechazó la petición. Revisa LLM_MODEL, LLM_BASE_URL y los límites configurados."


class LLMTimeout(LLMError):
    code = "llm_timeout"
    http_status = 504
    public_message = "El proveedor de IA tardó demasiado en responder."


class LLMUnavailable(LLMError):
    code = "llm_unavailable"
    http_status = 503
    public_message = "El proveedor de IA no está disponible temporalmente."


class LLMRateLimited(LLMUnavailable):
    code = "llm_rate_limited"
    public_message = "El proveedor de IA limitó la frecuencia de peticiones o la cuota disponible."


class LLMBudgetExceeded(LLMUnavailable):
    code = "llm_budget_exceeded"
    public_message = "Se alcanzó el tope de llamadas LIVE configurado (LLM_MAX_LIVE_CALLS)."


class LLMContentNotAuthorized(LLMError):
    """El contenido a enviar (p. ej. la clase real de DMC) no está autorizado a salir hacia este proveedor."""
    code = "llm_content_not_authorized"
    http_status = 403
    fallback_allowed = True
    public_message = "El contenido de la clase no está autorizado para enviarse a este proveedor externo."


class StreamCancelled(Exception):
    """El cliente cerró la conexión (o canceló) durante un streaming: se corta la llamada al proveedor y no se guarda nada. No es un LLMError."""


class LLMDeadlineExceeded(LLMTimeout):
    """Se agotó el tiempo TOTAL de la pregunta (no el de una llamada): no se inicia otra llamada al proveedor."""
    code = "llm_timeout"


class LLMUnmaskedContent(LLMContentNotAuthorized):
    """La barrera final detectó un nombre conocido que el enmascarado no pudo ocultar: la petición NO se envía."""
    code = "llm_unmasked_content"
    public_message = "Se bloqueó el envío a NVIDIA porque quedaba un nombre de persona sin enmascarar; la respuesta se generó en modo DEMO."


class LLMMalformedResponse(LLMError):
    code = "llm_malformed_response"
    http_status = 502
    public_message = "La respuesta del proveedor de IA no pasó la verificación."
