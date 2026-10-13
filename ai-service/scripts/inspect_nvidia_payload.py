"""
Inspección LOCAL del payload que RescuePath enviaría a NVIDIA con la clase real — SIN enviar nada.

Qué hace
  * Carga la clase real con el MISMO enmascarado que usa el backend y arma el tutor con el proveedor NVIDIA real
    (`NvidiaKimiProvider`), pero con un transporte HTTP SIMULADO en memoria: la petición se captura y se descarta.
  * Bloquea los sockets mientras corre: si algo intentara abrir una conexión, el script falla en lugar de enviar.
  * Usa una clave de mentira. No lee `ai-service/.env`, no lee `NVIDIA_API_KEY` y no activa `NVIDIA_ALLOW_CLASS_CONTENT`:
    la autorización de contenido se concede SOLO dentro de este proceso, sobre el proveedor simulado.
  * Ejecuta preguntas representativas (incluidas una con un nombre de `demo-assets/mask-names.txt`, una con correo y
    teléfono, y una conversación cuyo historial guardado contiene un nombre sin enmascarar) y escribe un informe
    PRIVADO con la versión sanitizada de cada solicitud.

Informe: ai-service/data/real/payload_nvidia_preview.md  (carpeta IGNORADA por Git). Contiene fragmentos de la clase
(ya enmascarados): no subirlo ni compartirlo. Antes de escribirlo se comprueba que no contiene nombres conocidos, claves,
correos ni teléfonos; si los contuviera, NO se escribe.

    cd ai-service
    .\\.venv\\Scripts\\python.exe -X utf8 scripts\\inspect_nvidia_payload.py

Límite: es heurístico. Prueba que los nombres CONOCIDOS no salen; no puede probar que no exista algún nombre que ni la
lista ni las reglas conocen.
"""
import argparse
import contextlib
import hashlib
import json
import re
import socket
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BASE_DIR, DATA_DIR, LLMConfig  # noqa: E402
from app.knowledge.real_lesson import MASK_NAMES_FILE, _read_extra_names, load_real_lesson, register_real_lesson  # noqa: E402
from app.knowledge.retriever import KnowledgeRetriever  # noqa: E402
from app.llm import DemoProvider, NvidiaKimiProvider  # noqa: E402
from app.models.chat import ChatRequest  # noqa: E402
from app.privacy import EMAIL_RE, PHONE_RE  # noqa: E402
from app.services.guardian import HeuristicGuardian, LLMGuardian  # noqa: E402
from app.services.tutor_service import TutorService  # noqa: E402
from app.storage.conversation_store import ConversationStore  # noqa: E402

DEFAULT_ASSETS = BASE_DIR.parent / "demo-assets"
DEFAULT_OUT = DATA_DIR / "real" / "payload_nvidia_preview.md"
FAKE_KEY = "INSPECCION-LOCAL-CLAVE-DE-MENTIRA-0000"                   # jamás una credencial real (ni con forma de una)
KEY_RE = re.compile(r"nvapi-[A-Za-z0-9_\-]{10,}|\bsk-[A-Za-z0-9\-]{16,}|Bearer\s+[A-Za-z0-9._\-]{16,}")
STUDENT = "inspection-student"
FALLBACK_NAME = "Zulemita"          # nombre ficticio si no existe mask-names.txt (y entonces NO estará en el filtro: se verá la fuga)

CANNED = {      # respuesta simulada: sirve para que el flujo termine; no es una respuesta de NVIDIA
    "answer": "Respuesta simulada para la inspección local [S1].", "follow_up_question": "¿Puedes explicarlo con tus palabras?",
    "used_segments": ["S1"], "label": "relevant", "reason": "simulado",
}


@contextlib.contextmanager
def blocked_network() -> Iterator[None]:
    """Cualquier intento de abrir un socket falla. Se restaura al salir (importa cuando se ejecuta dentro de pytest)."""
    original_connect, original_create = socket.socket.connect, socket.create_connection

    def refuse(*args, **kwargs):
        raise RuntimeError("inspect_nvidia_payload: red bloqueada (este script nunca envía nada)")
    socket.socket.connect, socket.create_connection = refuse, refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.create_connection = original_connect, original_create


class Recorder:
    """Transporte simulado: captura cada petición (cuerpo y NOMBRES de cabeceras, nunca sus valores) y responde con texto fijo."""

    def __init__(self) -> None:
        self.requests: List[Dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "header_names": sorted(request.headers.keys()), "url": str(request.url)})
        completion = {"id": "simulado", "object": "chat.completion", "model": body.get("model"),
                      "choices": [{"index": 0, "finish_reason": "stop",
                                   "message": {"role": "assistant", "content": json.dumps(CANNED, ensure_ascii=False)}}]}
        return httpx.Response(200, json=completion)


def _tutor_or_guardian(body: Dict) -> str:
    last = body["messages"][-1]["content"]
    return "tutor" if "<mensaje_estudiante>" in last else ("guardián" if "<pregunta>" in last else "otro")


def _segment_ids(body: Dict) -> List[str]:
    return re.findall(r'<segmento id="S\d+" minutos="([^"]+)">', body["messages"][-1]["content"])


def run_cases(assets_dir: Path, workdir: Path):
    real = load_real_lesson(assets_dir)
    if real is None:
        return None
    names = _read_extra_names(assets_dir / MASK_NAMES_FILE)
    listed = (names[0].split() or [FALLBACK_NAME])[0] if names else FALLBACK_NAME

    kb = KnowledgeRetriever()
    register_real_lesson(kb, real)
    recorder = Recorder()
    cfg = LLMConfig(provider="nvidia", api_key=FAKE_KEY, base_url="https://integrate.api.nvidia.com/v1", model="moonshotai/kimi-k3",
                    live_authorized=True, fallback_to_demo=True, timeout_seconds=5.0, max_output_tokens=800,
                    allow_class_content=True)      # SOLO en este proceso y SOLO con el transporte simulado
    provider = NvidiaKimiProvider(cfg, client=httpx.Client(transport=httpx.MockTransport(recorder)))
    provider.restrict_lesson(real.lesson.course_id, real.lesson.lesson_id)
    provider.attach_privacy(real.privacy)

    store = ConversationStore(workdir / "inspeccion.db")
    store.initialize()
    guardian = LLMGuardian(provider, kb, HeuristicGuardian(kb))
    service = TutorService(kb, store, provider, fallback_provider=DemoProvider(), guardian=guardian, privacy=real.privacy)
    unmasked_service = TutorService(kb, store, DemoProvider(), privacy=None)     # simula filas guardadas antes de conocer el nombre

    def ask(svc: TutorService, message: str, session_id: Optional[str] = None):
        return svc.chat(ChatRequest(student_id=STUDENT, course_id=real.lesson.course_id, lesson_id=real.lesson.lesson_id,
                                    session_id=session_id, message=message))

    cases = []

    def record(title: str, note: str, message: str, svc_call):
        start = len(recorder.requests)
        response = svc_call()
        cases.append({"title": title, "note": note, "question_as_processed": service._mask(message),
                      "provider": response.provider, "fallback_used": response.fallback_used,
                      "requests": recorder.requests[start:]})
        return response

    for q in ("¿Qué es n8n?", "¿Cuál es la diferencia entre no-code y low-code?", "¿Qué diferencia existe entre un workflow y un RPA?",
              "¿Para qué sirve Docker en esta clase?"):
        record("Pregunta típica", "Término técnico de la clase.", q, lambda q=q: ask(service, q))

    q = f"¿{listed} preguntó qué es un workflow? Mi correo es alumno.demo@ejemplo.org y mi celular +51 912 345 678."
    record("Pregunta con un nombre de la lista + correo + teléfono",
           "La pregunta original NO se muestra (contiene el nombre); abajo aparece como la procesa el servicio.", q, lambda: ask(service, q))

    first = ask(unmasked_service, f"{listed} dice que no entendió qué es n8n, ¿me lo explicas?")
    q2 = "¿Y para qué sirve Docker con n8n?"
    record("Historial guardado SIN enmascarar (nombre en un turno previo)",
           "Simula conversaciones previas guardadas antes de añadir el nombre a mask-names.txt.", q2,
           lambda: ask(service, q2, first.session_id))

    q3 = "¿Cómo se prepara una pizza napolitana?"
    record("Pregunta fuera de tema", "El guardián la descarta: no debería viajar nada.", q3, lambda: ask(service, q3))

    q4 = "¿Qué opinas de la sostenibilidad ambiental en las empresas?"
    record("Pregunta ambigua", "El guardián puede consultar al modelo (solo pregunta y título, sin términos de la clase).", q4,
           lambda: ask(service, q4))
    return real, cases, provider, recorder


def sanitize_messages(body: Dict) -> List[Dict]:
    out = []
    for m in body.get("messages", []):
        if m["role"] == "system":
            digest = hashlib.sha256(m["content"].encode("utf-8")).hexdigest()[:12]
            out.append({"role": "system", "content": f"<prompt de sistema del repositorio · {len(m['content'])} caracteres · sha256 {digest}… · no contiene datos de la clase>"})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def build_report(real, cases, provider, recorder) -> str:
    privacy = real.privacy
    payloads = [(c, r) for c in cases for r in c["requests"]]
    total_chars = sum(sum(len(m["content"]) for m in r["body"]["messages"]) for _, r in payloads)
    seen_segments = {rng for _, r in payloads for rng in _segment_ids(r["body"])}
    hits = sum(privacy.known_name_hits(m["content"]) for _, r in payloads for m in r["body"]["messages"])
    lines = [
        "# Vista previa del payload hacia NVIDIA (LOCAL — contiene fragmentos de la clase ya enmascarados; NO subir ni compartir)", "",
        "> **No se envió nada.** Las solicitudes se capturaron en memoria con un transporte simulado y sockets bloqueados. "
        "La clave usada es de mentira y no se muestra. `NVIDIA_ALLOW_CLASS_CONTENT` del `.env` no se tocó (sigue en `false`).", "",
        "## Resumen",
        f"- Casos ejecutados: {len(cases)} · solicitudes que HABRÍAN salido: {len(payloads)} · caracteres totales: {total_chars:,}",
        f"- Segmentos distintos incluidos (de {len(real.lesson.segments)}): {len(seen_segments)} · por solicitud: "
        f"{min((len(_segment_ids(r['body'])) for _, r in payloads), default=0)}–{max((len(_segment_ids(r['body'])) for _, r in payloads), default=0)}",
        f"- Nombres que el filtro conoce: {privacy.known_name_count} (los nombres no se listan) · coincidencias residuales en TODOS los mensajes enviados: **{hits}**",
        f"- Barrera final: mensajes re-enmascarados {provider.egress_stats['messages_remasked']} · solicitudes bloqueadas {provider.egress_stats['blocked']} "
        f"· solicitudes revisadas {provider.egress_stats['requests_checked']}",
        "- Cabeceras de cada solicitud (solo nombres): " + ", ".join(sorted({h for _, r in payloads for h in r["header_names"]})) + " · `Authorization: Bearer [REDACTED]`",
        f"- Destino que se usaría: `{payloads[0][1]['url'] if payloads else '(ninguna solicitud)'}`", "",
        "## Qué NO aparece en ninguna solicitud",
        "- El video, la transcripción completa, identificadores de estudiante, rutas locales, la clave ni el correo/teléfono escritos por el estudiante.", "",
    ]
    for i, (case, r) in enumerate(payloads, 1):
        body = r["body"]
        kind = _tutor_or_guardian(body)
        text_all = "".join(m["content"] for m in body["messages"])
        lines += [
            f"## Solicitud {i} · {kind} · caso: {case['title']}",
            f"_{case['note']}_", "",
            f"- Pregunta tal como la procesa el servicio: `{case['question_as_processed']}`",
            f"- Parámetros: model=`{body.get('model')}`, reasoning_effort=`{body.get('reasoning_effort')}`, stream=`{body.get('stream')}`, "
            f"límite de tokens=`{body.get('max_completion_tokens') or body.get('max_tokens')}`",
            f"- Mensajes: {len(body['messages'])} · caracteres: {len(text_all):,} · segmentos: {len(_segment_ids(body))} · "
            f"marcadores `[nombre]`: {text_all.count('[nombre]')} · coincidencias con nombres conocidos: {privacy.known_name_hits(text_all)}", "",
            "```json", json.dumps(sanitize_messages(body), ensure_ascii=False, indent=2), "```", "",
        ]
    skipped = [c for c in cases if not c["requests"]]
    for c in skipped:
        lines += [f"## Sin solicitud · caso: {c['title']}", f"_{c['note']}_ — respondió `{c['provider']}` sin salir del equipo; "
                  f"pregunta procesada: `{c['question_as_processed']}`", ""]
    lines += ["## Límites de esta comprobación",
              "- Demuestra que los nombres CONOCIDOS (lista + reglas) no salen en estos casos. No puede demostrar que no exista un nombre que ni la lista ni las reglas conozcan.",
              "- El texto de los fragmentos sale tal cual (con los errores del reconocimiento automático): puede mencionar empleadores, ciudades o anécdotas personales del docente.",
              "- NVIDIA puede registrar entradas y salidas del servicio de prueba."]
    return "\n".join(lines) + "\n"


def safety_problems(report: str, privacy) -> List[str]:
    problems = []
    if privacy.known_name_hits(report):
        problems.append("el informe contiene nombres conocidos")
    if KEY_RE.search(report):
        problems.append("el informe contiene algo con forma de clave")
    if EMAIL_RE.search(report):
        problems.append("el informe contiene un correo")
    if PHONE_RE.search(report):
        problems.append("el informe contiene un teléfono")
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Vista previa local del payload hacia NVIDIA (no envía nada).")
    parser.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS, help="carpeta con la clase real (por defecto demo-assets/)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="informe privado de salida (por defecto data/real/, ignorada por Git)")
    args = parser.parse_args(argv)

    with blocked_network(), tempfile.TemporaryDirectory() as tmp:
        result = run_cases(Path(args.assets_dir), Path(tmp))
    if result is None:
        print("No se encontró la clase real en la carpeta indicada. Nada que inspeccionar.")
        return 1
    real, cases, provider, recorder = result
    report = build_report(real, cases, provider, recorder)
    problems = safety_problems(report, real.privacy)
    if problems:
        print("INFORME NO ESCRITO: " + "; ".join(problems) + ". Revisa mask-names.txt y las reglas de enmascarado.")
        return 3
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8", newline="\n")

    sent = [r for c in cases for r in c["requests"]]
    chars = [sum(len(m["content"]) for m in r["body"]["messages"]) for r in sent]
    print(f"Informe privado: {out}")
    print(f"Casos: {len(cases)} · solicitudes capturadas (NO enviadas): {len(sent)} · caracteres por solicitud: "
          f"{min(chars, default=0)}–{max(chars, default=0)}")
    print(f"Nombres conocidos por el filtro: {real.privacy.known_name_count} · coincidencias residuales en lo que habría salido: "
          f"{sum(real.privacy.known_name_hits(m['content']) for r in sent for m in r['body']['messages'])}")
    print(f"Barrera final: {provider.egress_stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
