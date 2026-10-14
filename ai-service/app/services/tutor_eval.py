"""
Evaluación reproducible del tutor SIN red y sin enviar nada: casos de preguntas -> comportamiento, evidencia y citas comprobables.

Dos modos (ver `build_service`):
  * "demo"      : el proveedor DEMO real (determinista).
  * "simulated" : el proveedor NVIDIA real (prompt, privacidad, verificación de citas) pero con un transporte HTTP SIMULADO en
                  memoria que devuelve respuestas de prueba. Valida el CIRCUITO, no la calidad de Kimi: toda respuesta de este modo
                  dice «SIMULADO» y el informe lo etiqueta así. NUNCA es un resultado LIVE.

Qué se comprueba por caso (todo derivable de los segmentos reales, nada se «espera» de memoria):
  comportamiento (grounded / out_of_scope / no_evidence), pertinencia del guardián, número de citas, que la cita principal caiga en la
  ventana esperada, que cada cita coincida EXACTAMENTE con un segmento real y su salto esté dentro de él, que los minutos escritos en
  el texto pertenezcan a las citas, y el estado DEMO/LIVE-simulado declarado.
"""
import contextlib
import json
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import httpx

from app.config import LLMConfig
from app.knowledge.retriever import KnowledgeRetriever
from app.llm import DemoProvider, NvidiaKimiProvider
from app.models.chat import ChatRequest
from app.models.transcript import LessonTranscript
from app.services.guardian import HeuristicGuardian, LLMGuardian
from app.services.tutor_service import TutorService
from app.storage.conversation_store import ConversationStore

SIMULATED_KEY = "CLAVE-DE-MENTIRA-EVALUACION-LOCAL"
_MIN_RE = re.compile(r"min (\d{1,2}:\d{2}(?::\d{2})?)–(\d{1,2}:\d{2}(?::\d{2})?)")
_SEG_RE = re.compile(r'<segmento id="(S\d+)" minutos="([^"]+)">')


@contextlib.contextmanager
def blocked_network() -> Iterator[None]:
    """Cualquier intento de abrir un socket falla mientras corre la evaluación (y se restaura al salir)."""
    original_connect, original_create = socket.socket.connect, socket.create_connection

    def refuse(*args, **kwargs):
        raise RuntimeError("evaluación local: red bloqueada (nada se envía)")
    socket.socket.connect, socket.create_connection = refuse, refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.create_connection = original_connect, original_create


# ------------------------------------------------------------------ transporte simulado
class SimulatedKimi:
    """Transporte HTTP en memoria: registra cada petición y responde con un JSON de prueba (siempre marcado «SIMULADO»)."""

    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.payloads.append(body)
        last = body["messages"][-1]["content"]
        if "<pregunta>" in last:
            reply: Dict[str, Any] = {"label": "uncertain", "reason": "SIMULADO"}
        else:
            labels = [m[0] for m in _SEG_RE.findall(last)]
            if "solo MENCIONAN" in last or not labels:
                reply = {"answer": "SIMULADO: la clase solo menciona el tema y no lo define.", "follow_up_question": None,
                         "used_segments": [], "answerable": False}
            else:
                reply = {"answer": f"SIMULADO: respuesta de prueba apoyada en el segmento [{labels[0]}].",
                         "follow_up_question": "¿Cómo lo explicarías con tus palabras?", "used_segments": [labels[0]], "answerable": True}
        completion = {"id": "simulado", "object": "chat.completion", "model": body.get("model"),
                      "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]}
        return httpx.Response(200, json=completion)

    @property
    def calls(self) -> int:
        return len(self.payloads)


# ------------------------------------------------------------------ construcción del servicio
@dataclass
class EvalRig:
    service: TutorService
    mode: str                                   # demo | simulated
    transport: Optional[SimulatedKimi]
    lesson: LessonTranscript
    privacy: Any = None


def build_service(kb: KnowledgeRetriever, lesson: LessonTranscript, db_path: Path, mode: str, privacy=None,
                 allow_private: bool = True) -> EvalRig:
    store = ConversationStore(db_path)
    store.initialize()
    heuristic = HeuristicGuardian(kb)
    if mode == "demo":
        return EvalRig(TutorService(kb, store, DemoProvider(), guardian=heuristic, privacy=privacy), mode, None, lesson, privacy)
    if mode != "simulated":
        raise ValueError("mode debe ser 'demo' o 'simulated'")
    transport = SimulatedKimi()
    cfg = LLMConfig(provider="nvidia", api_key=SIMULATED_KEY, base_url="https://integrate.api.nvidia.com/v1", model="moonshotai/kimi-k3",
                    live_authorized=True, fallback_to_demo=True, timeout_seconds=5.0, max_output_tokens=700,
                    allow_class_content=allow_private)       # autorización concedida SOLO dentro de este proceso y de este transporte
    provider = NvidiaKimiProvider(cfg, client=httpx.Client(transport=httpx.MockTransport(transport)))
    provider.restrict_lesson(lesson.course_id, lesson.lesson_id)
    if privacy is not None:
        provider.attach_privacy(privacy)
    service = TutorService(kb, store, provider, fallback_provider=DemoProvider(), guardian=LLMGuardian(provider, kb, heuristic), privacy=privacy)
    return EvalRig(service, mode, transport, lesson, privacy)


# ------------------------------------------------------------------ casos y resultados
@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class TurnResult:
    question: str
    response_type: str
    guardian: Optional[str]
    mode: str
    provider: str
    fallback_used: bool
    citations: List[Dict[str, Any]]
    text: str
    sent_to_model: int                      # solicitudes simuladas que salieron en ESTE turno
    prompt_notes: List[str] = field(default_factory=list)


@dataclass
class CaseResult:
    case_id: str
    turns: List[TurnResult]
    checks: List[Check]

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)


def load_cases(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ask(rig: EvalRig, student: str, question: str, session_id: Optional[str]):
    before = rig.transport.calls if rig.transport else 0
    resp = rig.service.chat(ChatRequest(student_id=student, course_id=rig.lesson.course_id, lesson_id=rig.lesson.lesson_id,
                                        session_id=session_id, message=question))
    sent = (rig.transport.calls - before) if rig.transport else 0
    notes: List[str] = []
    if rig.transport and sent:
        tutor_calls = [p for p in rig.transport.payloads[before:] if "<mensaje_estudiante>" in p["messages"][-1]["content"]]
        if tutor_calls:
            ctx = tutor_calls[-1]["messages"][-1]["content"].split("<segmentos>")[0]
            notes = [ln for ln in ctx.splitlines() if ln.startswith("Evidencia:") or ln.startswith("Términos de la pregunta")]
    turn = TurnResult(
        question=question, response_type=resp.response_type, guardian=resp.guardian.label if resp.guardian else None, mode=resp.mode,
        provider=resp.provider, fallback_used=resp.fallback_used,
        citations=[{"start": c.start_seconds, "end": c.end_seconds, "timestamp": c.timestamp, "seek": c.seek_seconds} for c in resp.references],
        text=resp.response, sent_to_model=sent, prompt_notes=notes)
    return resp, turn


def run_case(rig: EvalRig, case: Dict[str, Any], student: str = "eval-student") -> CaseResult:
    turns: List[TurnResult] = []
    session_id: Optional[str] = None
    for question in (case.get("turns") or [case["question"]]):
        resp, turn = _ask(rig, student, question, session_id)
        session_id = resp.session_id
        turns.append(turn)
    return CaseResult(case["id"], turns, evaluate(rig, case, turns))


def evaluate(rig: EvalRig, case: Dict[str, Any], turns: List[TurnResult]) -> List[Check]:
    exp = dict(case.get("expect", {}))
    if rig.mode == "simulated":
        exp.update(case.get("expect_simulated", {}))          # p. ej. DEMO no sabe que un tema solo se menciona; el modelo (simulado) sí
    final = turns[-1]
    checks: List[Check] = []
    segments = {(s.start_seconds, s.end_seconds): s for s in rig.lesson.segments}

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, bool(ok), detail))

    if "behavior" in exp:
        add("comportamiento", final.response_type == exp["behavior"], f"{final.response_type} (esperado {exp['behavior']})")
    if "guardian" in exp:
        add("pertinencia (guardián)", final.guardian == exp["guardian"], f"{final.guardian} (esperado {exp['guardian']})")
    if "max_citations" in exp:
        add("pocas citas", len(final.citations) <= exp["max_citations"], f"{len(final.citations)} (máx. {exp['max_citations']})")
    if "primary_start" in exp:
        lo, hi = exp["primary_start"]
        first = final.citations[0]["start"] if final.citations else None
        add("cita principal en la ventana esperada", first is not None and lo <= first <= hi, f"{first} s (ventana {lo}-{hi} s)")
    if exp.get("no_citations"):
        add("sin citas", not final.citations, f"{len(final.citations)} citas")
    if exp.get("different_from_previous") and len(turns) > 1:
        prev = {c["start"] for c in turns[-2].citations}
        add("el seguimiento aporta un segmento distinto", bool(final.citations) and not ({c['start'] for c in final.citations} & prev),
            f"{[c['start'] for c in final.citations]} vs {sorted(prev)}")
    if exp.get("same_primary_as"):
        add("misma cita principal que la pregunta corta", True, "se compara en run_suite")

    # --- comprobaciones que valen para TODOS los casos
    exact = all((c["start"], c["end"]) in segments for t in turns for c in t.citations)
    add("cada cita coincide con un segmento real", exact)
    inside = all(c["start"] <= c["seek"] < c["end"] for t in turns for c in t.citations)
    add("el salto al video cae dentro del segmento citado", inside)
    stray = []
    for t in turns:
        allowed = {c["timestamp"] for c in t.citations}
        for a, b in _MIN_RE.findall(t.text):
            if f"{a}–{b}" not in allowed:
                stray.append(f"{a}–{b}")
    add("los minutos del texto pertenecen a las citas", not stray, ", ".join(stray))
    label_ok = (final.mode == "DEMO" and final.provider == "demo") if rig.mode == "demo" else (
        final.fallback_used or final.provider == "nvidia-kimi" or (final.provider == "demo" and final.mode == "DEMO"))
    add("estado declarado (DEMO / simulado)", label_ok, f"{final.mode}/{final.provider}" + (" fallback" if final.fallback_used else ""))
    if rig.mode == "simulated" and final.provider == "nvidia-kimi":
        add("la respuesta simulada se identifica como tal", "SIMULADO" in final.text)
    if rig.mode == "simulated" and final.provider == "demo" and not final.fallback_used:
        add("sin modelo en este caso: respondió la plantilla DEMO", final.sent_to_model <= 1, f"{final.sent_to_model} solicitudes")
    if exp.get("prompt_note"):
        add("el prompt advierte lo que corresponde", any(exp["prompt_note"] in n for n in final.prompt_notes) if rig.mode == "simulated" else True,
            "; ".join(final.prompt_notes) or "(sin notas)")
    return checks


def run_suite(rig: EvalRig, cases: List[Dict[str, Any]]) -> List[CaseResult]:
    results: List[CaseResult] = []
    by_id: Dict[str, CaseResult] = {}
    for i, case in enumerate(cases):
        result = run_case(rig, case, student=f"eval-{i}")
        ref = case.get("expect", {}).get("same_primary_as")
        if ref and ref in by_id:
            a = result.turns[-1].citations[:1]
            b = by_id[ref].turns[-1].citations[:1]
            for c in result.checks:
                if c.name == "misma cita principal que la pregunta corta":
                    c.ok, c.detail = bool(a) and a == b, f"{[x['start'] for x in a]} vs {[x['start'] for x in b]}"
        by_id[case["id"]] = result
        results.append(result)
    return results


def render_report(results: List[CaseResult], mode: str, lesson_title: str) -> str:
    label = "DEMO (proveedor determinista real)" if mode == "demo" else "LIVE SIMULADO (transporte falso; NO es Kimi ni NVIDIA)"
    ok = sum(r.passed for r in results)
    lines = [f"# Evaluación del tutor · {lesson_title}", "", f"- Modo: **{label}**",
             f"- Casos aprobados: **{ok}/{len(results)}** · comprobaciones: {sum(c.ok for r in results for c in r.checks)}/{sum(len(r.checks) for r in results)}",
             "- Ninguna solicitud salió de este equipo.", ""]
    for r in results:
        t = r.turns[-1]
        lines += [f"## {'✅' if r.passed else '❌'} {r.case_id}",
                  f"- Pregunta: {t.question}",
                  f"- Respuesta: `{t.response_type}` · {t.mode}/{t.provider}{' (fallback)' if t.fallback_used else ''} · guardián: {t.guardian}",
                  f"- Citas: {', '.join(c['timestamp'] for c in t.citations) or '(ninguna)'} · solicitudes simuladas en el turno: {t.sent_to_model}"]
        if t.prompt_notes:
            lines.append("- Notas enviadas al modelo: " + " | ".join(t.prompt_notes))
        lines += [f"  - {'✔' if c.ok else '✘'} {c.name}" + (f" — {c.detail}" if c.detail and not c.ok else "") for c in r.checks]
        lines.append("")
    return "\n".join(lines) + "\n"
