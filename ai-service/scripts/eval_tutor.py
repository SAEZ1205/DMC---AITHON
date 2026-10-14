"""
Evaluación LOCAL y reproducible del tutor con la clase real (sin red, sin enviar nada).

    cd ai-service
    .\.venv\Scripts\python.exe -X utf8 scripts\eval_tutor.py            # DEMO y LIVE simulado
    .\.venv\Scripts\python.exe -X utf8 scripts\eval_tutor.py --mode demo

Modos: «demo» = proveedor DEMO real; «simulated» = circuito LIVE completo con un transporte FALSO (respuestas «SIMULADO»).
El modo simulado valida prompt, privacidad, verificación y citas; NO mide la calidad de Kimi y nunca se presenta como LIVE.
Informe (ignorado por Git): ai-service/data/real/eval_tutor_<modo>.md
"""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BASE_DIR, DATA_DIR  # noqa: E402
from app.knowledge.real_lesson import load_real_lesson, register_real_lesson  # noqa: E402
from app.knowledge.retriever import KnowledgeRetriever  # noqa: E402
from app.services.tutor_eval import blocked_network, build_service, load_cases, render_report, run_suite  # noqa: E402

CASES = BASE_DIR / "eval" / "tutor_eval_cases.json"
ASSETS = BASE_DIR.parent / "demo-assets"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluación local del tutor (sin red).")
    parser.add_argument("--mode", choices=["demo", "simulated", "both"], default="both")
    parser.add_argument("--assets-dir", type=Path, default=ASSETS)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR / "real")
    args = parser.parse_args(argv)

    real = load_real_lesson(args.assets_dir)
    if real is None:
        print("No se encontró la clase real en la carpeta indicada. Nada que evaluar.")
        return 1
    cases = load_cases(args.cases)["cases"]
    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    failed = 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with blocked_network(), tempfile.TemporaryDirectory() as tmp:
        for mode in (["demo", "simulated"] if args.mode == "both" else [args.mode]):
            rig = build_service(kb, real.lesson, Path(tmp) / f"{mode}.db", mode, privacy=real.privacy)
            results = run_suite(rig, cases)
            report = render_report(results, mode, real.lesson.title)
            out = args.out_dir / f"eval_tutor_{mode}.md"
            out.write_text(report, encoding="utf-8", newline="\n")
            ok = sum(r.passed for r in results)
            failed += len(results) - ok
            label = "DEMO" if mode == "demo" else "LIVE SIMULADO (no es Kimi)"
            print(f"[{label}] casos aprobados {ok}/{len(results)} · informe: {out}")
            for r in results:
                bad = [c for c in r.checks if not c.ok]
                print(f"   {'OK ' if r.passed else 'FALLA'} {r.case_id}" + ("" if r.passed else " -> " + "; ".join(f"{c.name} ({c.detail})" for c in bad)))
            if rig.transport:
                print(f"   solicitudes simuladas hechas: {rig.transport.calls} (ninguna salió del equipo)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
