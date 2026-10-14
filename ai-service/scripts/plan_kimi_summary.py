"""
Plan (SOLO LECTURA, sin red) del resumen de la clase real con Kimi por fragmentos. No genera nada y no llama a ningún proveedor.

Muestra cuántas solicitudes haría la acción administrativa `POST /api/admin/lessons/<curso>/<clase>/summary?engine=kimi`, qué tamaño
tendría cada una y el límite de tiempo teórico. Sirve para decidir con datos si vale la pena autorizar ese resumen.

    cd ai-service
    .\\.venv\\Scripts\\python.exe -X utf8 scripts\\plan_kimi_summary.py

Qué implica ejecutarlo de verdad (requiere TU autorización expresa):
  * Cada capítulo se envía a NVIDIA en trozos de <= 20 segmentos: entre todas las solicitudes sale TODO el texto de la clase
    (enmascarado), aunque nunca de una sola vez ni el video. Hay que tener NVIDIA_ALLOW_CLASS_CONTENT=true.
  * Es una llamada HTTP sincrónica: con ~25 solicitudes lentas puede tardar decenas de minutos. Hazlo en una ventana tranquila,
    nunca durante la demostración. Si una solicitud falla no se guarda un resumen parcial (todo o nada).
  * El resumen guardado queda marcado como «generado con Kimi» y la pantalla lo distingue del resumen local por temas.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BASE_DIR, settings  # noqa: E402
from app.knowledge.real_lesson import load_real_lesson, register_real_lesson  # noqa: E402
from app.knowledge.retriever import KnowledgeRetriever  # noqa: E402
from app.services.lesson_service import LessonService  # noqa: E402
from app.services.live_summary import MAX_CHARS_PER_CALL, MAX_SEGMENTS_PER_CALL, _chunks  # noqa: E402

ASSETS = BASE_DIR.parent / "demo-assets"


def plan(lesson, chapters):
    """Lista de (capítulo, nº de segmentos, caracteres) por solicitud prevista, más la de reducción final."""
    calls = []
    for ch in chapters:
        for group in _chunks(ch.segments):
            calls.append((ch.title, len(group), sum(len(s.text) for s in group)))
    calls.append(("Resumen general (reduce)", len(chapters), sum(len(ch.description) for ch in chapters)))
    return calls


def main() -> int:
    real = load_real_lesson(ASSETS)
    if real is None:
        print("No se encontró la clase real en demo-assets/. Nada que planificar.")
        return 1
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    chapters, _, _ = LessonService(kb, None).extractive_skeleton(real.lesson)
    calls = plan(real.lesson, chapters)
    cfg = settings.nvidia_config()
    total_chars = sum(c for _, _, c in calls[:-1])
    print(f"Clase: {len(real.lesson.segments)} segmentos · {len(chapters)} capítulos locales")
    print(f"Solicitudes previstas: {len(calls)} (una por trozo de capítulo, <= {MAX_SEGMENTS_PER_CALL} segmentos / {MAX_CHARS_PER_CALL} caracteres, + 1 de reducción)")
    print(f"Texto de la clase que saldría en total (enmascarado): {total_chars:,} caracteres en {len(calls) - 1} solicitudes; "
          f"mayor solicitud: {max(c for _, _, c in calls[:-1]):,} caracteres")
    print(f"Límites activos: {cfg.timeout_seconds:g} s por llamada · tope de llamadas LIVE por proceso: {cfg.max_live_calls} · salida <= {cfg.max_output_tokens} tokens")
    print(f"Cota superior de espera (todas las llamadas agotando su timeout): {len(calls) * cfg.timeout_seconds / 60:.0f} min. "
          f"No hay una medición del tiempo real por llamada de este tipo: no se promete ninguna duración.")
    print(f"¿Cabe en el tope de llamadas?: {'sí' if not cfg.max_live_calls or len(calls) <= cfg.max_live_calls else 'NO: súbelo o no se generará'}")
    print("Para ejecutarlo (NO lo hagas durante la demo): POST /api/admin/lessons/n8n-intro/clase-01-introduccion-n8n/summary?engine=kimi con X-Admin-Token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
