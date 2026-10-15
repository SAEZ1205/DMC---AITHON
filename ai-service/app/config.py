"""
Configuración centralizada de DMC RescuePath AI.
Maneja variables de entorno, rutas seguras y modos de ejecución.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de .env si existe
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Número decimal acotado: un valor ausente, ilegible o absurdo nunca produce una espera infinita ni nula."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    if value != value:          # NaN
        return default
    return min(max(value, minimum), maximum)


@dataclass(frozen=True)
class AcademicConfig:
    """Parámetros de quizzes, micro-pregunta y telemetría (Etapa 4)."""
    quiz_pass_threshold_percent: float = 60.0   # umbral DEMO configurable; no es una regla institucional
    quiz_max_attempts_per_lesson: int = 3
    quiz_attempt_ttl_minutes: int = 60
    micro_question_reference_date: str = ""     # YYYY-MM-DD (p. ej. inicio del curso); vacío = no se muestra
    micro_question_interval_days: int = 14
    micro_question_require_quiz: bool = True
    telemetry_api_token: str = ""               # vacío = /api/telemetry/events deshabilitado
    admin_api_token: str = ""                   # vacío = acciones administrativas deshabilitadas (p. ej. regenerar resúmenes)
    telemetry_pseudonym_salt: str = "demo-salt-cambiar-en-produccion"

    def __repr__(self) -> str:
        return (f"AcademicConfig(pass={self.quiz_pass_threshold_percent}, attempts={self.quiz_max_attempts_per_lesson}, "
                f"ttl_min={self.quiz_attempt_ttl_minutes}, ref_date={self.micro_question_reference_date!r}, "
                f"interval={self.micro_question_interval_days}, token={'<definido>' if self.telemetry_api_token else '<vacío>'})")


@dataclass(frozen=True)
class LLMConfig:
    """Configuración del proveedor de lenguaje (sin efectos secundarios; fácil de construir en pruebas)."""
    provider: str = "demo"
    api_key: str = ""
    base_url: str = "https://api.moonshot.ai/v1"
    model: str = "kimi-k2.6"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 700
    thinking: str = "disabled"          # solo kimi-k2.6: enabled | disabled
    reasoning_effort: str = "low"       # solo kimi-k3: low | high | max
    fallback_to_demo: bool = True
    live_authorized: bool = False       # puerta explícita: sin esto NUNCA se hacen llamadas facturables
    max_live_calls: int = 100           # tope de llamadas LIVE por proceso (0 = sin tope)
    max_history_messages: int = 6
    max_evidence: int = 3
    # --- solo NVIDIA Build (ignorados por el proveedor Moonshot) ---
    max_tokens_param: str = "max_completion_tokens"   # o "max_tokens", según lo que acepte el endpoint
    json_mode: bool = False                           # enviar response_format=json_object (no documentado para NVIDIA: apagado)
    allow_class_content: bool = False                 # ¿se puede enviar la transcripción de la clase REAL a este proveedor?
    total_timeout_seconds: float = 150.0              # tope TOTAL por pregunta del estudiante (guardián + tutor); 0 = solo el de cada llamada
    guardian_timeout_seconds: float = 25.0            # el guardián es una llamada pequeña: si tarda, se usa la heurística y no se bloquea al tutor

    def __repr__(self) -> str:          # nunca imprimir la clave
        return (f"LLMConfig(provider={self.provider!r}, model={self.model!r}, base_url={self.base_url!r}, "
                f"api_key={'<definida>' if self.api_key else '<vacía>'}, live_authorized={self.live_authorized})")


def _resolve_sqlite_path(url: str) -> Path:
    """
    Convierte una DATABASE_URL `sqlite:///...` en una ruta absoluta.
    Las rutas relativas se resuelven contra `ai-service/` (no contra el directorio
    desde donde se lanza el servidor), de modo que siempre se usa la misma base.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix) or len(url) == len(prefix):
        return DATA_DIR / "rescuepath.db"
    path = Path(url[len(prefix):])
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


class Settings:
    # Proveedor LLM: "demo" (default) o "kimi" / "moonshot"
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "demo").lower()
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "").strip()
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "kimi-k2.6")
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30.0"))
    LLM_MAX_OUTPUT_TOKENS: int = _env_int("LLM_MAX_OUTPUT_TOKENS", 700, minimum=16)
    LLM_THINKING: str = os.getenv("LLM_THINKING", "disabled").strip().lower()
    LLM_REASONING_EFFORT: str = os.getenv("LLM_REASONING_EFFORT", "low").strip().lower()
    LLM_FALLBACK_TO_DEMO: bool = _env_bool("LLM_FALLBACK_TO_DEMO", True)
    LLM_LIVE_AUTHORIZED: bool = _env_bool("LLM_LIVE_AUTHORIZED", False)
    LLM_MAX_LIVE_CALLS: int = _env_int("LLM_MAX_LIVE_CALLS", 100)
    LLM_MAX_HISTORY_MESSAGES: int = _env_int("LLM_MAX_HISTORY_MESSAGES", 6)
    LLM_MAX_EVIDENCE: int = _env_int("LLM_MAX_EVIDENCE", 3, minimum=1)

    # NVIDIA Build (Kimi alojado). La clave vive SOLO en el entorno / ai-service/.env (ignorado por Git).
    NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "").strip()
    NVIDIA_MODEL: str = os.getenv("NVIDIA_MODEL", "moonshotai/kimi-k3").strip()
    NVIDIA_BASE_URL: str = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip().rstrip("/")
    # Kimi K3 siempre razona y esos tokens cuentan contra el límite de salida: a ~6 tokens/s (medido: 258 tokens en 42 s) un tope de 1200
    # tardaría ~200 s. 700 cabe en el timeout por llamada (120 s) y deja margen sobre el mínimo de 512 que aplica el proveedor.
    NVIDIA_MAX_OUTPUT_TOKENS: int = _env_int("NVIDIA_MAX_OUTPUT_TOKENS", 700, minimum=16)
    NVIDIA_TIMEOUT_SECONDS: float = _env_float("NVIDIA_TIMEOUT_SECONDS", 120.0, minimum=5.0, maximum=300.0)
    NVIDIA_TOTAL_TIMEOUT_SECONDS: float = _env_float("NVIDIA_TOTAL_TIMEOUT_SECONDS", 150.0, minimum=5.0, maximum=600.0)
    NVIDIA_GUARDIAN_TIMEOUT_SECONDS: float = _env_float("NVIDIA_GUARDIAN_TIMEOUT_SECONDS", 25.0, minimum=3.0, maximum=120.0)
    NVIDIA_MAX_TOKENS_PARAM: str = os.getenv("NVIDIA_MAX_TOKENS_PARAM", "max_completion_tokens").strip()
    NVIDIA_JSON_MODE: bool = _env_bool("NVIDIA_JSON_MODE", False)
    NVIDIA_ALLOW_CLASS_CONTENT: bool = _env_bool("NVIDIA_ALLOW_CLASS_CONTENT", False)
    # Caché de respuestas verificadas (solo aplica al proveedor LIVE): una pregunta repetida no vuelve a esperar a Kimi. Ver services/answer_cache.py.
    ANSWER_CACHE_ENABLED: bool = _env_bool("ANSWER_CACHE_ENABLED", True)
    # Chat con streaming SSE (POST /api/chat/stream): OPCIONAL y apagado. No se ha probado contra el endpoint real de NVIDIA (ver README, Etapa 9).
    CHAT_STREAMING_ENABLED: bool = _env_bool("CHAT_STREAMING_ENABLED", False)

    # Quizzes, micro-pregunta y telemetría (Etapa 4)
    QUIZ_PASS_THRESHOLD_PERCENT: float = float(os.getenv("QUIZ_PASS_THRESHOLD_PERCENT", "60"))
    QUIZ_MAX_ATTEMPTS_PER_LESSON: int = _env_int("QUIZ_MAX_ATTEMPTS_PER_LESSON", 3, minimum=1)
    QUIZ_ATTEMPT_TTL_MINUTES: int = _env_int("QUIZ_ATTEMPT_TTL_MINUTES", 60, minimum=1)
    MICRO_QUESTION_REFERENCE_DATE: str = os.getenv("MICRO_QUESTION_REFERENCE_DATE", "").strip()
    MICRO_QUESTION_INTERVAL_DAYS: int = _env_int("MICRO_QUESTION_INTERVAL_DAYS", 14, minimum=1)
    MICRO_QUESTION_REQUIRE_QUIZ: bool = _env_bool("MICRO_QUESTION_REQUIRE_QUIZ", True)
    TELEMETRY_API_TOKEN: str = os.getenv("TELEMETRY_API_TOKEN", "").strip()
    ADMIN_API_TOKEN: str = os.getenv("ADMIN_API_TOKEN", "").strip()
    # Clase real de demostración (material privado fuera de Git)
    DEMO_ASSETS_DIR: str = os.getenv("DEMO_ASSETS_DIR", "").strip()
    REAL_LESSON_ENABLED: bool = _env_bool("REAL_LESSON_ENABLED", True)
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500")
    TELEMETRY_PSEUDONYM_SALT: str = os.getenv("TELEMETRY_PSEUDONYM_SALT", "demo-salt-cambiar-en-produccion")

    # Almacenamiento local SQLite
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'rescuepath.db').as_posix()}")
    DATABASE_PATH: Path = _resolve_sqlite_path(DATABASE_URL)

    # Integraciones opcionales
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip()
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "").strip()
    N8N_WEBHOOK_URL: str = os.getenv("N8N_WEBHOOK_URL", "").strip()

    # Parámetros del servidor
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    @property
    def is_demo_mode(self) -> bool:
        """Modo DEMO salvo que haya proveedor real, clave Y autorización explícita de presupuesto."""
        if self.LLM_PROVIDER == "nvidia":
            return not self.NVIDIA_API_KEY or not self.LLM_LIVE_AUTHORIZED
        if self.LLM_PROVIDER not in ("kimi", "moonshot") or not self.LLM_API_KEY:
            return True
        return not self.LLM_LIVE_AUTHORIZED

    def academic_config(self) -> AcademicConfig:
        return AcademicConfig(
            quiz_pass_threshold_percent=self.QUIZ_PASS_THRESHOLD_PERCENT,
            quiz_max_attempts_per_lesson=self.QUIZ_MAX_ATTEMPTS_PER_LESSON,
            quiz_attempt_ttl_minutes=self.QUIZ_ATTEMPT_TTL_MINUTES,
            micro_question_reference_date=self.MICRO_QUESTION_REFERENCE_DATE,
            micro_question_interval_days=self.MICRO_QUESTION_INTERVAL_DAYS,
            micro_question_require_quiz=self.MICRO_QUESTION_REQUIRE_QUIZ,
            telemetry_api_token=self.TELEMETRY_API_TOKEN,
            admin_api_token=self.ADMIN_API_TOKEN,
            telemetry_pseudonym_salt=self.TELEMETRY_PSEUDONYM_SALT,
        )

    def nvidia_config(self) -> LLMConfig:
        """Configuración del proveedor NVIDIA (independiente de LLM_PROVIDER: la usan también los scripts de prueba)."""
        return LLMConfig(
            provider="nvidia",
            api_key=self.NVIDIA_API_KEY,
            base_url=self.NVIDIA_BASE_URL,
            model=self.NVIDIA_MODEL,
            timeout_seconds=self.NVIDIA_TIMEOUT_SECONDS,
            max_output_tokens=self.NVIDIA_MAX_OUTPUT_TOKENS,
            thinking=self.LLM_THINKING,
            reasoning_effort=self.LLM_REASONING_EFFORT,
            fallback_to_demo=self.LLM_FALLBACK_TO_DEMO,
            live_authorized=self.LLM_LIVE_AUTHORIZED,
            max_live_calls=self.LLM_MAX_LIVE_CALLS,
            max_history_messages=self.LLM_MAX_HISTORY_MESSAGES,
            max_evidence=self.LLM_MAX_EVIDENCE,
            max_tokens_param=self.NVIDIA_MAX_TOKENS_PARAM,
            json_mode=self.NVIDIA_JSON_MODE,
            allow_class_content=self.NVIDIA_ALLOW_CLASS_CONTENT,
            total_timeout_seconds=self.NVIDIA_TOTAL_TIMEOUT_SECONDS,
            guardian_timeout_seconds=self.NVIDIA_GUARDIAN_TIMEOUT_SECONDS,
        )

    def llm_config(self) -> LLMConfig:
        if self.LLM_PROVIDER == "nvidia":
            return self.nvidia_config()
        return LLMConfig(
            provider=self.LLM_PROVIDER,
            api_key=self.LLM_API_KEY,
            base_url=self.LLM_BASE_URL,
            model=self.LLM_MODEL,
            timeout_seconds=self.LLM_TIMEOUT_SECONDS,
            max_output_tokens=self.LLM_MAX_OUTPUT_TOKENS,
            thinking=self.LLM_THINKING,
            reasoning_effort=self.LLM_REASONING_EFFORT,
            fallback_to_demo=self.LLM_FALLBACK_TO_DEMO,
            live_authorized=self.LLM_LIVE_AUTHORIZED,
            max_live_calls=self.LLM_MAX_LIVE_CALLS,
            max_history_messages=self.LLM_MAX_HISTORY_MESSAGES,
            max_evidence=self.LLM_MAX_EVIDENCE,
        )

settings = Settings()
