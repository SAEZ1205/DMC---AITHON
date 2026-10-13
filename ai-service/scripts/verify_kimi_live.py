"""
Verificación REAL y MANUAL de la integración con Kimi. NO forma parte de pytest.

!! HACE UNA LLAMADA FACTURABLE a la API de Moonshot/Kimi (unos cientos de tokens). !!
Solo se ejecuta si TODO esto es cierto:
  * LLM_API_KEY definida en el entorno o en ai-service/.env
  * LLM_LIVE_AUTHORIZED=true
  * se pasa el flag --confirm-billable-call

Uso (desde ai-service/):
    .\\.venv\\Scripts\\python.exe scripts\\verify_kimi_live.py --confirm-billable-call

Comprueba que: la credencial es aceptada, el modelo responde JSON válido, las etiquetas de segmentos
son verificables y las citas resultantes coinciden con el segmento recuperado. Imprime uso de tokens.
No imprime la clave.
"""
import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.knowledge.retriever import retriever  # noqa: E402
from app.llm import KimiProvider  # noqa: E402
from app.llm.base import ResponseKind, TutorContext  # noqa: E402
from app.llm.errors import LLMError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verificación real (facturable) de Kimi")
    parser.add_argument("--confirm-billable-call", action="store_true", help="confirmo que esta llamada genera costo")
    args = parser.parse_args()

    config = settings.llm_config()
    if not args.confirm_billable_call:
        print("Abortado: falta --confirm-billable-call. No se hizo ninguna llamada.")
        return 2
    if not config.api_key:
        print("Abortado: LLM_API_KEY no está definida. No se hizo ninguna llamada.")
        return 2
    if not config.live_authorized:
        print("Abortado: LLM_LIVE_AUTHORIZED no es true. No se hizo ninguna llamada.")
        return 2

    evidence = retriever.retrieve_segments("sql-demo", "ORDER BY", lesson_id="clase-01-introduccion-sql", top_k=2)
    context = TutorContext(
        kind=ResponseKind.GROUNDED, student_message="Explícame ORDER BY", course_name="SQL Fundamentals — DEMO",
        lesson_title="Clase 1 — Introducción a SQL (DEMO)", topic_label="ORDER BY", evidence=evidence,
    )
    config = replace(config, max_live_calls=1)
    print(f"Llamando a {config.base_url} con el modelo {config.model} (1 llamada facturable)...")
    try:
        reply = KimiProvider(config).generate(context)
    except LLMError as exc:
        print(f"FALLÓ [{exc.code}] {exc.public_message} (estado HTTP del proveedor: {exc.upstream_status})")
        return 1

    cites = retriever.build_segment_citations([evidence[i] for i in reply.cited_indices])
    print("OK. Respuesta verificada:\n")
    print(reply.text)
    print("\nCitas construidas por el backend:", [c.timestamp for c in cites])
    print("Uso de tokens:", reply.usage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
