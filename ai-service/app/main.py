"""
Punto de entrada FastAPI de DMC RescuePath AI.

    cd ai-service
    python -m uvicorn app.main:app --reload

Documentación interactiva en http://127.0.0.1:8000/docs
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.academic_routes import router as academic_router
from app.api.demo_routes import router as demo_router
from app.api.errors import register_error_handlers
from app.api.routes import router
from app.config import BASE_DIR, DATA_DIR, AcademicConfig, LLMConfig, settings
from app.knowledge.quiz_bank import QuizBank
from app.knowledge.real_lesson import load_real_lesson, register_real_lesson, write_local_reports
from app.knowledge.retriever import KnowledgeRetriever, retriever as default_retriever
from app.llm import DemoProvider, KimiProvider, LLMProvider, NvidiaKimiProvider, ProviderSelection, select_provider
from app.services.answer_cache import AnswerCache
from app.services.guardian import HeuristicGuardian, LLMGuardian
from app.services.lesson_service import LessonService
from app.services.live_summary import LiveSummaryGenerator
from app.services.micro_question_service import MicroQuestionService
from app.services.quiz_service import QuizService
from app.services.summary_service import SummaryService
from app.services.telemetry_service import TelemetryService
from app.services.tutor_service import TutorService
from app.storage.academic_store import AcademicStore
from app.storage.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

DESCRIPTION = """
Asistente académico conversacional que responde **solo** con lo que dicen las transcripciones
de clase y cita los minutos exactos.

* **Modo DEMO**: respuestas deterministas con plantillas pedagógicas; no usa ningún modelo externo.
* **Datos ficticios**: el material (`sql-demo`) y los estudiantes son sintéticos. `student_id` NO es una
  credencial: el backend todavía no autentica usuarios.
"""


def create_app(
    db_path: Optional[Path] = None,
    retriever: Optional[KnowledgeRetriever] = None,
    provider: Optional[LLMProvider] = None,
    llm_config: Optional[LLMConfig] = None,
    llm_client=None,
    academic_config: Optional[AcademicConfig] = None,
    clock: Optional[Callable[[], datetime]] = None,
    quiz_bank: Optional[QuizBank] = None,
    real_lesson: Optional[bool] = None,
    demo_assets_dir: Optional[Path] = None,
    cors_origins: Optional[list] = None,
) -> FastAPI:
    """
    Fábrica de la aplicación. Los parámetros permiten aislar base de datos, material y proveedor en las pruebas
    (`llm_client` permite inyectar un transporte HTTP simulado: las pruebas nunca llaman a la API real).
    """
    config = llm_config or settings.llm_config()
    if provider is not None:
        selection = ProviderSelection(provider, config.provider, "proveedor inyectado")
    else:
        selection = select_provider(config, client=llm_client)
    store = ConversationStore(db_path or settings.DATABASE_PATH)
    fallback = DemoProvider() if selection.live and config.fallback_to_demo else None
    # La clase real solo se carga si se pide, o por defecto cuando no se inyecta un recuperador (arranque normal del servidor).
    load_real = real_lesson if real_lesson is not None else (retriever is None and settings.REAL_LESSON_ENABLED)
    kb = retriever or (KnowledgeRetriever() if load_real else default_retriever)   # copia propia: no se muta el singleton
    assets = Path(demo_assets_dir) if demo_assets_dir else (Path(settings.DEMO_ASSETS_DIR) if settings.DEMO_ASSETS_DIR else BASE_DIR.parent / "demo-assets")
    real = load_real_lesson(assets) if load_real else None
    if real is not None:
        register_real_lesson(kb, real)
    academic_cfg = academic_config or settings.academic_config()
    now = clock or (lambda: datetime.now(timezone.utc))
    academic_store = AcademicStore(store)
    telemetry = TelemetryService(academic_store, academic_cfg)
    heuristic = HeuristicGuardian(kb)
    live_provider = selection.provider if isinstance(selection.provider, KimiProvider) else None
    if isinstance(live_provider, NvidiaKimiProvider) and real is not None:
        # El servicio de prueba de NVIDIA puede registrar entradas y salidas: la clase REAL no sale sin autorización expresa.
        live_provider.restrict_lesson(real.lesson.course_id, real.lesson.lesson_id)
        live_provider.attach_privacy(real.privacy)      # barrera final: re-enmascara y verifica nombres antes de cada envío
    guardian = LLMGuardian(live_provider, kb, heuristic) if live_provider else heuristic
    service = TutorService(kb, store, selection.provider, fallback_provider=fallback, telemetry=telemetry, guardian=guardian,
                           privacy=real.privacy if real is not None else None,
                           answer_cache=AnswerCache(store, enabled=settings.ANSWER_CACHE_ENABLED))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            store.initialize()
            logger.info("Almacenamiento listo: %s", store.db_path)
        except Exception:
            logger.exception("No se pudo inicializar el almacenamiento; /health lo reportará")
        if real is not None:                    # procesamiento de la clase: el resumen heurístico se genera UNA vez por versión
            try:
                stored = app.state.lessons.ensure_extractive(real.lesson.course_id, real.lesson.lesson_id)
                logger.info("Resumen de la clase real listo (%s, %s)", "reutilizado" if stored.cached else "generado ahora", stored.engine)
                write_local_reports(real, DATA_DIR / "real")
            except Exception:
                logger.exception("No se pudo preparar el resumen de la clase real")
        yield

    app = FastAPI(
        title="DMC RescuePath AI",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.llm_selection = selection
    app.state.llm_config = config
    app.state.academic_config = academic_cfg
    app.state.academic_store = academic_store
    app.state.telemetry = telemetry
    summaries = SummaryService(academic_store)
    app.state.summaries = summaries
    app.state.lessons = LessonService(kb, telemetry, summaries)
    app.state.real_lesson = real
    app.state.live_summary = LiveSummaryGenerator(live_provider, app.state.lessons) if live_provider else None
    app.state.quiz = QuizService(kb, quiz_bank or QuizBank(kb), academic_store, telemetry, academic_cfg, now)
    app.state.micro = MicroQuestionService(kb, academic_store, telemetry, academic_cfg, now)
    origins = cors_origins if cors_origins is not None else [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Admin-Token", "X-Telemetry-Token", "Range"],
        expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "Idempotent-Replay"],
    )
    register_error_handlers(app)
    app.include_router(router)
    app.include_router(academic_router)
    app.include_router(demo_router)
    return app


app = create_app()
