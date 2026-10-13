"""
Revisión LOCAL de privacidad de la clase real: qué podría salir del equipo si se autorizara el envío a un servicio externo.

No usa la red. Carga la clase con el mismo enmascarado que usa el backend y busca lo que pudo quedar sin ocultar:
palabras capitalizadas que parecen nombres propios, URLs, correos o números largos. Escribe el detalle (con fragmentos de la
clase) en  ai-service/data/real/revision_privacidad.md  — carpeta IGNORADA por Git. No subir ni compartir ese archivo.

    cd ai-service
    .\\.venv\\Scripts\\python.exe scripts\\review_privacy.py
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BASE_DIR, DATA_DIR  # noqa: E402
from app.knowledge.real_lesson import load_real_lesson  # noqa: E402
from app.knowledge.text_utils import STOPWORDS, normalize_text  # noqa: E402

ASSETS = BASE_DIR.parent / "demo-assets"
WORD = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3,}")
URL = re.compile(r"https?://\S+|www\.\S+|\b\w+\.(?:com|net|org|io|pe)\b", re.IGNORECASE)
DIGITS = re.compile(r"\b\d[\d .-]{6,}\d\b")
SPOKEN_AT = re.compile(r"\b(arroba|gmail|hotmail|outlook|punto com)\b", re.IGNORECASE)
CONTEXT = 70


def main() -> int:
    real = load_real_lesson(ASSETS)
    if real is None:
        print("No se encontró la clase real en demo-assets/. Nada que revisar.")
        return 1
    segments = real.lesson.segments
    total_chars = sum(len(s.text) for s in segments)
    texts = [s.text for s in segments]

    capital, lower = Counter(), Counter()
    context = defaultdict(str)
    for text in texts:
        for m in WORD.finditer(text):
            w = m.group(0)
            key = normalize_text(w)
            if key in STOPWORDS:
                continue
            (capital if w[0].isupper() else lower)[key] += 1
            if w[0].isupper() and key not in context:
                a, b = max(0, m.start() - CONTEXT // 2), min(len(text), m.end() + CONTEXT // 2)
                context[key] = text[a:b].replace("\n", " ")
    # candidatos: aparecen mayormente capitalizados (los nombres propios) y no son una sigla o una palabra común en minúsculas
    candidates = sorted(
        ((capital[k], k) for k in capital if capital[k] >= 2 and capital[k] > lower[k] and not context[k].isupper()),
        reverse=True,
    )[:60]
    urls = sorted({m.group(0) for t in texts for m in URL.finditer(t)})
    numbers = sorted({m.group(0) for t in texts for m in DIGITS.finditer(t)})
    spoken = sorted({m.group(0).lower() for t in texts for m in SPOKEN_AT.finditer(t)})

    r = real.report
    lines = [
        "# Revisión de privacidad de la clase real (LOCAL — contiene fragmentos de la clase; NO subir ni compartir)", "",
        "## Resumen",
        f"- Segmentos: {len(segments)} · caracteres totales de texto: {total_chars:,}",
        f"- Enmascarado aplicado al ingerir: {r.pii_counts}  (nombres detectados por presentación: {r.name_stats})",
        f"- Palabras capitalizadas que podrían ser nombres propios sin enmascarar: {len(candidates)} (ver abajo)",
        f"- URLs/dominios: {len(urls)} · números largos: {len(numbers)} · menciones habladas de correo: {len(spoken)}", "",
        "## Qué se enviaría a un servicio externo si se autorizara (NVIDIA_ALLOW_CLASS_CONTENT=true)",
        "- Por pregunta: la pregunta del estudiante (enmascarada), hasta 3 segmentos recuperados (~40 s de clase cada uno) y el historial reciente.",
        "- NUNCA: el video, la transcripción completa, identificadores de estudiantes ni los nombres que el enmascarado detectó.",
        "- Aun así, el texto de esos segmentos sale tal cual (con los errores del reconocimiento automático) y el servicio de prueba puede registrarlo.", "",
        "## Candidatos a nombre propio (decide cuáles añadir a demo-assets/mask-names.txt, uno por línea)",
        "| veces | palabra | contexto |", "|---|---|---|",
    ]
    lines += [f"| {n} | {k} | …{context[k]}… |" for n, k in candidates]
    def bullets(items, empty):
        return [f"- {i}" for i in items] or [f"- {empty}"]

    lines += ["", "## URLs / dominios", *bullets(urls, "(ninguno)"),
              "", "## Números largos (posibles teléfonos o documentos)", *bullets(numbers, "(ninguno)"),
              "", "## Menciones habladas de correo", *bullets(spoken, "(ninguna)"),
              "", "## Límites de esta revisión",
              "- Es heurística: no detecta nombres escritos de forma inusual, datos dichos en voz alta como palabras (p. ej. un teléfono dictado) ni información sensible sin patrón.",
              "- El audio y el video no se analizan: la grabación contiene voces y rostros y NO debe subirse a ningún servicio."]
    out = DATA_DIR / "real" / "revision_privacidad.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Revisión escrita en: {out.relative_to(BASE_DIR)} (ignorado por Git)")
    print(f"Segmentos: {len(segments)} · enmascarado aplicado: {r.pii_counts}")
    print(f"Pendiente de revisar: {len(candidates)} palabras que podrían ser nombres · {len(urls)} URLs · {len(numbers)} números largos · {len(spoken)} menciones de correo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
