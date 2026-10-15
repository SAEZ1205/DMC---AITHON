"""
Comparación CONTROLADA de modelos de NVIDIA Build para el tutor (latencia, cumplimiento del formato, uso de la evidencia, citas, longitud).

POR DEFECTO NO ENVÍA NADA: solo muestra el plan (cuántas solicitudes haría, con qué parámetros y qué tamaño tendría cada una).
Para ejecutar solicitudes reales hay que dar DOS autorizaciones explícitas:

    --i-authorize-real-requests N     N = número máximo de solicitudes reales que permites (debe cubrir las planificadas)
    --include-class-content           (solo si usas --course real) autoriza enviar fragmentos enmascarados de la clase REAL

Sin --include-class-content el curso por defecto es el FICTICIO de SQL (`sql-demo`): ningún dato de DMC sale del equipo.

    cd ai-service
    .\\.venv\\Scripts\\python.exe -X utf8 scripts\\compare_models.py                              # plan, sin red
    # UNA sola pregunta real con el modelo actual (procedimiento de medición):
    .\\.venv\\Scripts\\python.exe -X utf8 scripts\\compare_models.py --models moonshotai/kimi-k3 --questions 1 --i-authorize-real-requests 1
    # comparación (3 modelos x 3 preguntas = 9 solicitudes):
    .\\.venv\\Scripts\\python.exe -X utf8 scripts\\compare_models.py --questions 3 --i-authorize-real-requests 9

Cada solicitud es UNA llamada del tutor (sin guardián LLM, sin reintentos, sin streaming, caché desactivada), con el mismo prompt, la misma evidencia
y la misma barrera de privacidad que el tutor real. La clave se lee del entorno solo después de la autorización y no se imprime nunca. El informe
privado (con las respuestas) va a ai-service/data/real/model_comparison.md (ignorado por Git).

Modelos candidatos (de la documentación pública de NVIDIA Build consultada el 2026-10-14; NO se comprobó que tu cuenta tenga acceso a ellos ni sus
límites o condiciones de prueba, que la documentación no indica por modelo):
  * moonshotai/kimi-k3                      el actual (razonamiento siempre activo)
  * meta/llama-3.1-8b-instruct              pequeño, sin razonamiento: probable candidato más rápido; riesgo de calidad en español y de seguir mal el formato
  * nvidia/nemotron-3.5-lightning-30b-a3b   anunciado por NVIDIA como «el 30B A3B más rápido»; no se verificó si razona por defecto
Para modelos que no son K3 no se envía `reasoning_effort` (parámetro propio de K3) y no se aplica el mínimo de 512 tokens.
"""
import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BASE_DIR, DATA_DIR, LLMConfig, settings  # noqa: E402
from app.knowledge.real_lesson import load_real_lesson, register_real_lesson  # noqa: E402
from app.knowledge.retriever import KnowledgeRetriever  # noqa: E402
from app.llm import DemoProvider, NvidiaKimiProvider  # noqa: E402
from app.models.chat import ChatRequest  # noqa: E402
from app.services import latency  # noqa: E402
from app.services.guardian import HeuristicGuardian  # noqa: E402
from app.services.tutor_service import TutorService  # noqa: E402
from app.storage.conversation_store import ConversationStore  # noqa: E402

DEFAULT_MODELS = ["moonshotai/kimi-k3", "meta/llama-3.1-8b-instruct", "nvidia/nemotron-3.5-lightning-30b-a3b"]
REASONING_MODELS = ("moonshotai/kimi-k3",)
SQL_QUESTIONS = ["Explícame ORDER BY", "¿Para qué sirve GROUP BY?", "¿Cuál es la diferencia entre INNER JOIN y LEFT JOIN?", "¿Qué hace WHERE?"]
REAL_QUESTIONS = ["¿Qué es n8n y para qué sirve?", "¿Para qué sirve Docker en esta clase?", "¿Cuál es la diferencia entre no-code y low-code?",
                  "¿Qué diferencia existe entre un workflow y un RPA?"]
OUT = DATA_DIR / "real" / "model_comparison.md"


def provider_config(model: str, base: LLMConfig) -> LLMConfig:
    """Misma configuración para todos; solo cambian el modelo y, si no es K3, el parámetro de razonamiento y el mínimo de tokens."""
    effort = base.reasoning_effort if model in REASONING_MODELS else "none"
    return replace(base, model=model, reasoning_effort=effort, max_live_calls=0)


def build_rig(course: str, assets: Path, workdir: Path, model: str, base: LLMConfig, client: Optional[httpx.Client]):
    kb = KnowledgeRetriever()
    privacy, lesson = None, None
    if course == "real":
        real = load_real_lesson(assets)
        if real is None:
            raise SystemExit("No se encontró la clase real en demo-assets/.")
        register_real_lesson(kb, real)
        privacy, lesson = real.privacy, real.lesson
    cfg = provider_config(model, base)
    provider = NvidiaKimiProvider(cfg, client=client)
    if lesson is not None:
        provider.restrict_lesson(lesson.course_id, lesson.lesson_id)
        provider.attach_privacy(privacy)
    store = ConversationStore(workdir / f"{model.replace('/', '_')}.db")
    store.initialize()
    service = TutorService(kb, store, provider, fallback_provider=None, guardian=HeuristicGuardian(kb), privacy=privacy)   # sin fallback: se mide a ESE modelo
    course_id, lesson_id = (lesson.course_id, lesson.lesson_id) if lesson else ("sql-demo", "clase-01-introduccion-sql")
    return service, course_id, lesson_id, cfg


def plan(models: List[str], questions: List[str], course: str, base: LLMConfig) -> List[str]:
    lines = [f"Curso: {'REAL (n8n)' if course == 'real' else 'FICTICIO (sql-demo): ningún dato de DMC sale del equipo'}",
             f"Solicitudes planificadas: {len(models) * len(questions)} ({len(models)} modelo(s) x {len(questions)} pregunta(s)), una llamada del tutor cada una, sin reintentos"]
    for model in models:
        cfg = provider_config(model, base)
        lines.append(f"  · {model}: max_completion_tokens={cfg.max_output_tokens}, "
                     f"{'reasoning_effort=' + cfg.reasoning_effort if cfg.reasoning_effort != 'none' else 'sin reasoning_effort ni mínimo de 512 tokens'}, timeout {cfg.timeout_seconds:g} s")
    lines.append("Preguntas: " + " | ".join(questions))
    return lines


def run(argv: Optional[List[str]] = None, client_factory: Optional[Callable[[], httpx.Client]] = None, out: Path = OUT) -> int:
    parser = argparse.ArgumentParser(description="Comparación controlada de modelos (por defecto, solo muestra el plan).")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--questions", type=int, default=3, help="cuántas preguntas por modelo (1-4)")
    parser.add_argument("--course", choices=["sql", "real"], default="sql")
    parser.add_argument("--assets-dir", type=Path, default=BASE_DIR.parent / "demo-assets")
    parser.add_argument("--i-authorize-real-requests", type=int, default=0, metavar="N")
    parser.add_argument("--include-class-content", action="store_true")
    args = parser.parse_args(argv)

    course = "real" if args.course == "real" else "sql"
    questions = (REAL_QUESTIONS if course == "real" else SQL_QUESTIONS)[: max(1, min(args.questions, 4))]
    base = settings.nvidia_config()
    for line in plan(args.models, questions, course, base):
        print(line)
    planned = len(args.models) * len(questions)
    if args.i_authorize_real_requests <= 0:
        print("\nNo se envió nada (modo plan). Para ejecutar, añade --i-authorize-real-requests N.")
        return 0
    if args.i_authorize_real_requests < planned:
        print(f"\nRechazado: autorizaste {args.i_authorize_real_requests} solicitud(es) y el plan necesita {planned}. No se envió nada.")
        return 2
    if course == "real" and not args.include_class_content:
        print("\nRechazado: --course real envía fragmentos (enmascarados) de la clase real a NVIDIA; añade --include-class-content si lo autorizas. No se envió nada.")
        return 2
    key = settings.NVIDIA_API_KEY
    if not key:
        print("\nRechazado: no hay NVIDIA_API_KEY en el entorno. No se envió nada.")
        return 2
    base = replace(base, api_key=key, live_authorized=True, allow_class_content=(course == "real" and args.include_class_content),
                   max_output_tokens=settings.NVIDIA_MAX_OUTPUT_TOKENS)

    import tempfile
    results: List[Dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for model in args.models:
            client = client_factory() if client_factory else None
            service, course_id, lesson_id, cfg = build_rig(course, args.assets_dir, Path(tmp), model, base, client)
            for i, question in enumerate(questions):
                started = time.perf_counter()
                row: Dict = {"model": model, "question": question}
                try:
                    response = service.chat(ChatRequest(student_id=f"comparacion-{i}", course_id=course_id, lesson_id=lesson_id, message=question))
                    row.update(wall_s=round(time.perf_counter() - started, 2), status=(response.timing or {}).get("status"), provider_ms=(response.timing or {}).get("provider_ms"),
                               counts=(response.timing or {}).get("counts", {}), words=len(response.response.split()), citations=len(response.references),
                               verified=response.provider == "nvidia-kimi" and not response.fallback_used, text=response.response)
                except Exception as exc:                          # un modelo que falla no detiene la comparación: se registra el código
                    row.update(wall_s=round(time.perf_counter() - started, 2), status="error", error=type(exc).__name__, verified=False,
                               words=0, citations=0, counts={}, provider_ms=None, text="")
                results.append(row)
                print(f"  {model:42s} P{i + 1}: {row['wall_s']:6.1f} s · {row['status']} · {row['words']} palabras · {row['citations']} citas")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(results, course), encoding="utf-8", newline="\n")
    print(f"\nInforme privado: {out} (ignorado por Git). Solicitudes reales enviadas: {len(results)}.")
    return 0


def render(results: List[Dict], course: str) -> str:
    lines = ["# Comparación de modelos (PRIVADO: contiene respuestas; no subir)", "",
             f"Curso: {'real (fragmentos enmascarados)' if course == 'real' else 'ficticio sql-demo'} · solicitudes: {len(results)}", "",
             "| modelo | tiempo (s) | estado | verificada | palabras | citas | tokens prompt/salida/razonamiento |", "|---|---|---|---|---|---|---|"]
    for r in results:
        c = r.get("counts", {})
        lines.append(f"| {r['model']} | {r['wall_s']} | {r['status']} | {'sí' if r['verified'] else 'no'} | {r['words']} | {r['citations']} | "
                     f"{c.get('prompt_tokens', '-')}/{c.get('completion_tokens', '-')}/{c.get('reasoning_tokens', '-')} |")
    lines += ["", "## Respuestas", ""]
    for r in results:
        lines += [f"### {r['model']} · {r['question']}", r["text"] or f"(sin respuesta: {r.get('error', r['status'])})", ""]
    lines += ["## Cómo leer esto", "- «verificada» = el modelo devolvió el formato esperado y sus citas pasaron el verificador del backend (no hubo fallback).",
              "- Una muestra de pocas preguntas NO es una prueba estadística: sirve para descartar modelos y decidir si vale la pena seguir.",
              "- Calidad y alucinaciones se juzgan LEYENDO las respuestas contra los segmentos citados."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(run())
