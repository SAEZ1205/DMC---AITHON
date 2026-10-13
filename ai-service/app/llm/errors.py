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

    def __init__(self, detail: str = "", upstream_status: Optional[int] = None):
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


class LLMMalformedResponse(LLMError):
    code = "llm_malformed_response"
    http_status = 502
    public_message = "La respuesta del proveedor de IA no pasó la verificación."
