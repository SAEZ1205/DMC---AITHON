import logging
from dataclasses import dataclass
from typing import Optional

from app.config import LLMConfig, settings
from app.llm.base import (
    ChatTurn, FollowUpMode, LLMProvider, ResponseKind, TutorContext, TutorReply,
)
from app.llm.demo_provider import DemoProvider
from app.llm.errors import LLMError
from app.llm.kimi_provider import KimiProvider
from app.llm.nvidia_provider import NvidiaKimiProvider

logger = logging.getLogger(__name__)

LIVE_PROVIDERS = ("kimi", "moonshot", "nvidia")


@dataclass(frozen=True)
class ProviderSelection:
    """Proveedor elegido y el motivo; se expone en /health (sin credenciales)."""
    provider: LLMProvider
    configured: str
    reason: str

    @property
    def live(self) -> bool:
        return self.provider.mode == "LIVE"


def select_provider(config: Optional[LLMConfig] = None, client=None) -> ProviderSelection:
    """
    DEMO salvo que se cumplan TODAS estas condiciones: proveedor kimi/moonshot/nvidia + su clave (LLM_API_KEY o
    NVIDIA_API_KEY) + LLM_LIVE_AUTHORIZED=true. Así una clave presente por accidente no genera llamadas sin autorización.
    """
    config = config or settings.llm_config()
    requested = (config.provider or "demo").lower()

    if requested not in LIVE_PROVIDERS:
        if requested != "demo":
            logger.warning("Proveedor LLM %r desconocido; se usa el modo DEMO.", requested)
            return ProviderSelection(DemoProvider(), requested, f"proveedor '{requested}' no soportado")
        return ProviderSelection(DemoProvider(), requested, "LLM_PROVIDER=demo")
    key_name = "NVIDIA_API_KEY" if requested == "nvidia" else "LLM_API_KEY"
    if not config.api_key:
        logger.warning("LLM_PROVIDER=%s sin %s: se usa el modo DEMO.", requested, key_name)
        return ProviderSelection(DemoProvider(), requested, f"falta {key_name}")
    if not config.live_authorized:
        logger.warning("Hay clave pero LLM_LIVE_AUTHORIZED no es true: se usa el modo DEMO (sin llamadas externas).")
        return ProviderSelection(DemoProvider(), requested, "LLM_LIVE_AUTHORIZED no es true (uso externo sin autorizar)")
    if requested == "nvidia":
        try:
            return ProviderSelection(NvidiaKimiProvider(config, client=client), requested, "LIVE autorizado (NVIDIA Build)")
        except ValueError as exc:      # URL base insegura: se informa el motivo y se mantiene DEMO
            logger.error("Configuración NVIDIA inválida: %s", exc)
            return ProviderSelection(DemoProvider(), requested, f"configuración NVIDIA inválida: {exc}")
    return ProviderSelection(KimiProvider(config, client=client), requested, "LIVE autorizado")


def get_provider(name: str = None, config: Optional[LLMConfig] = None) -> LLMProvider:
    """Compatibilidad con la Etapa 2: devuelve solo el proveedor."""
    if config is None:
        base = settings.llm_config()
        if name is not None:
            from dataclasses import replace
            base = replace(base, provider=name.lower())
        config = base
    return select_provider(config).provider


__all__ = [
    "ChatTurn", "DemoProvider", "FollowUpMode", "KimiProvider", "LLMError", "LLMProvider", "NvidiaKimiProvider",
    "ProviderSelection", "ResponseKind", "TutorContext", "TutorReply", "get_provider", "select_provider",
]
