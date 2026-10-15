"""
Tamaño del prompt que viajaría al modelo, por escenario, con la clase real y un transporte SIMULADO (sin red, nada sale del equipo).
Imprime solo números: caracteres del prompt de sistema, del historial, del bloque de contexto y de los segmentos, y cuántos hay.

    cd ai-service
    .\\.venv\\Scripts\\python.exe -X utf8 scripts\\measure_prompt_size.py

Los tokens se estiman como caracteres/3,5 (español); es una aproximación, no una medición del tokenizador de NVIDIA.
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BASE_DIR  # noqa: E402
from app.knowledge.real_lesson import load_real_lesson, register_real_lesson  # noqa: E402
from app.knowledge.retriever import KnowledgeRetriever  # noqa: E402
from app.models.chat import ChatRequest  # noqa: E402
from app.services.tutor_eval import blocked_network, build_service  # noqa: E402

ASSETS = BASE_DIR.parent / "demo-assets"
SCENARIOS = [
    ("n8n (definición)", ["¿Qué es n8n y para qué sirve?"]),
    ("Docker (definición)", ["¿Para qué sirve Docker en esta clase?"]),
    ("no-code vs low-code (comparación)", ["¿Cuál es la diferencia entre no-code y low-code?"]),
    ("workflow vs RPA (comparación)", ["¿Qué diferencia existe entre un workflow y un RPA?"]),
    ("conversación de 4 turnos", ["¿Qué diferencia existe entre un workflow y un RPA?", "Dame otro ejemplo", "No entendí, ¿puedes repetirlo?",
                                  "¿Qué procesos conviene automatizar?"]),
]
SEG = re.compile(r'<segmento id="S\d+" minutos="[^"]+">(.*?)</segmento>', re.S)


def main() -> int:
    real = load_real_lesson(ASSETS)
    if real is None:
        print("No se encontró la clase real en demo-assets/. Nada que medir.")
        return 1
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    print(f"{'escenario / turno':44s} {'total':>6s} {'sistema':>7s} {'histor.':>7s} {'segment.':>8s} {'nº seg':>6s} {'nº hist':>7s} {'~tokens':>7s}")
    totals = []
    with blocked_network(), tempfile.TemporaryDirectory() as tmp:
        for name, turns in SCENARIOS:
            rig = build_service(kb, real.lesson, Path(tmp) / f"{abs(hash(name))}.db", "simulated", privacy=real.privacy)
            session = None
            for i, question in enumerate(turns, 1):
                before = rig.transport.calls
                response = rig.service.chat(ChatRequest(student_id="medicion", course_id=real.lesson.course_id, lesson_id=real.lesson.lesson_id,
                                                        session_id=session, message=question))
                session = response.session_id
                tutor = [p for p in rig.transport.payloads[before:] if "<mensaje_estudiante>" in p["messages"][-1]["content"]]
                if not tutor:
                    print(f"{name + ' · turno ' + str(i):44s} (sin llamada al modelo: respondió la plantilla DEMO)")
                    continue
                msgs = tutor[-1]["messages"]
                system, user, history = len(msgs[0]["content"]), msgs[-1]["content"], msgs[1:-1]
                segs = SEG.findall(user)
                total = sum(len(m["content"]) for m in msgs)
                totals.append(total)
                label = name if len(turns) == 1 else f"{name} · turno {i}"
                print(f"{label:44s} {total:6d} {system:7d} {sum(len(m['content']) for m in history):7d} {sum(len(s) for s in segs):8d} "
                      f"{len(segs):6d} {len(history):7d} {int(total / 3.5):7d}")
    if totals:
        print(f"\nsolicitudes: {len(totals)} · caracteres: mediana {sorted(totals)[len(totals) // 2]} · máximo {max(totals)} · promedio {sum(totals) // len(totals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
