"""Capítulos sin LLM: anclaje a diapositivas, TextTiling, reglas de tamaño, logística, tramos clave y edición."""
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.models import Chapter, ClassSession, Course, User
from app.services import chaptering
from app.services.zoom_ingest import VttSegment, parse_vtt
from helpers import START

MIN = 60.0


@dataclass
class FakeUnit:
    unit_no: int
    title: str
    text: str
    is_key: bool = False


TOPICS = [
    ("Agenda", "agenda de la sesion acuerdos horario presentacion", 0, 5),
    ("Webhooks", "webhook url produccion listener endpoint recibir peticion post", 5, 27),
    ("HTTP Request", "http request autenticacion header token bearer api respuesta", 27, 50),
    ("Expresiones", "expresion json item campo mapear variable llaves dolar", 50, 78),
    ("Google Sheets", "google sheets hoja fila columna credencial oauth planilla", 78, 108),
    ("AI Agent", "agente modelo lenguaje prompt memoria herramienta llm", 108, 140),
    ("Manejo de errores", "error trigger reintento fallo notificacion excepcion workflow", 140, 172),
    ("Gracias", "gracias nos vemos proxima semana", 172, 180),
]
FILLER = "entonces bueno ahora vamos a ver aqui como funciona esto chicos"


def deck(key: set[int] = frozenset()) -> list[FakeUnit]:
    return [FakeUnit(i + 1, title, f"{title}\n{words}", i + 1 in key) for i, (title, words, _, _) in enumerate(TOPICS)]


def transcript(noise_at: float | None = None) -> list[VttSegment]:
    segs = []
    for _title, words, a, b in TOPICS:
        vocab = words.split()
        t, k = a * MIN, 0
        while t < b * MIN:
            text = f"{FILLER} {vocab[k % len(vocab)]} {vocab[(k + 3) % len(vocab)]} {vocab[(k + 5) % len(vocab)]}"
            if noise_at is not None and abs(t - noise_at) < 20:
                text = f"{FILLER} {TOPICS[6][1]}"
            segs.append(VttSegment(t, t + 15, "", text))
            t, k = t + 15, k + 1
    return segs


def true_bounds() -> list[float]:
    return [a * MIN for _, _, a, _ in TOPICS[1:]]


def test_slides_anchor_boundaries_titles_and_logistics():
    drafts = chaptering.build_chapters(transcript(), 180 * MIN, deck())
    assert chaptering.MIN_CHAPTERS <= len(drafts) <= chaptering.MAX_CHAPTERS
    assert drafts[0].start == 0 and drafts[-1].end == 180 * MIN
    assert all(a.end == b.start for a, b in zip(drafts, drafts[1:]))
    starts = [d.start for d in drafts[1:]]
    assert all(min(abs(s - b) for b in true_bounds()) <= 2 * MIN for s in starts)
    titles = [d.title for d in drafts]
    for name in ("Webhooks", "HTTP Request", "Expresiones", "Google Sheets", "AI Agent", "Manejo de errores"):
        assert name in titles
    assert drafts[0].logistics and not drafts[0].is_key
    assert all(d.source == "diapositivas" for d in drafts)
    assert drafts[1].slide_from == 2
    share = chaptering.key_share(drafts)
    assert chaptering.KEY_SHARE_LOW <= share <= chaptering.KEY_SHARE_HIGH
    assert not any(d.is_key for d in drafts if d.logistics)


def test_isolated_minute_does_not_jump_slides():
    drafts = chaptering.build_chapters(transcript(noise_at=15 * MIN), 180 * MIN, deck())
    assert [d.title for d in drafts if d.start < 27 * MIN][-1] == "Webhooks"
    assert sum(d.title.startswith("Manejo de errores") for d in drafts) == 1


def test_key_segments_follow_teacher_key_slides():
    drafts = chaptering.build_chapters(transcript(), 180 * MIN, deck(key={3, 6}))
    assert sorted(d.title for d in drafts if d.is_key) == ["AI Agent", "HTTP Request"]


def test_texttiling_without_materials():
    drafts = chaptering.build_chapters(transcript(), 180 * MIN)
    assert chaptering.MIN_CHAPTERS <= len(drafts) <= chaptering.MAX_CHAPTERS
    assert all(d.source == "texttiling" and d.slide_from is None for d in drafts)
    cuts = [d.start for d in drafts[1:]]
    found = sum(any(abs(c - b) <= 2 * MIN for c in cuts) for b in true_bounds()[:-1])
    assert found / len(true_bounds()[:-1]) >= 0.8
    assert all(" · " in d.title or d.title for d in drafts)
    assert all(d.end - d.start >= chaptering.MIN_CHAPTER_S for d in drafts)


def test_size_rules_split_long_chapters():
    long_deck = [FakeUnit(1, "Agenda", "agenda acuerdos"), FakeUnit(2, "Webhooks", TOPICS[1][1]),
                 FakeUnit(3, "Errores", TOPICS[6][1])]
    segs = [s for s in transcript() if s.start < 5 * MIN]
    t = 5 * MIN
    while t < 120 * MIN:
        segs.append(VttSegment(t, t + 15, "", f"{FILLER} webhook url listener"))
        t += 15
    while t < 130 * MIN:
        segs.append(VttSegment(t, t + 15, "", f"{FILLER} error reintento fallo"))
        t += 15
    drafts = chaptering.build_chapters(segs, 130 * MIN, long_deck)
    assert all(d.end - d.start <= chaptering.MAX_CHAPTER_S for d in drafts)
    assert any("(parte 2)" in d.title for d in drafts)


def test_sample_session_gets_5_to_12_chapters():
    segs = parse_vtt(Path(__file__).resolve().parents[1] / "data" / "sample" / "session_demo" / "transcript.vtt")
    drafts = chaptering.build_chapters(segs, 3 * 3600)
    assert chaptering.MIN_CHAPTERS <= len(drafts) <= chaptering.MAX_CHAPTERS
    assert drafts[-1].end == 3 * 3600


def _seed_chapters(db):
    tutor = User(name="Tutora", email="tutora@dmc.pe", role="tutor")
    db.add(tutor)
    db.flush()
    course = Course(name="Curso", start_date=START.date(), end_date=START.date(), total_sessions=1, schedule="",
                    **config.COURSE_DEFAULTS)
    db.add(course)
    db.flush()
    s = ClassSession(course_id=course.id, number=1, title="Sesión", starts_at=START, ends_at=START + timedelta(hours=3),
                     processed=True)
    db.add(s)
    db.flush()
    rows = [Chapter(session_id=s.id, start=a, end=b, title=t, is_key=k, logistics=log)
            for a, b, t, k, log in [(0, 600, "Bienvenida", False, True), (600, 5400, "Webhooks", True, False),
                                    (5400, 10800, "Errores", False, False)]]
    db.add_all(rows)
    db.commit()
    return tutor.id, s.id, [r.id for r in rows]


def test_teacher_edits_title_bounds_and_logistics(db):
    tid, sid, ids = _seed_chapters(db)
    h = {"X-User-Id": str(tid)}
    with TestClient(app) as client:
        body = {"chapters": [{"id": ids[0], "is_key": False, "start": 0, "end": 900},
                             {"id": ids[1], "is_key": True, "title": "Webhooks en producción", "start": 900, "end": 5400},
                             {"id": ids[2], "is_key": True}]}
        res = client.patch(f"/api/tutor/sessions/{sid}/chapters", headers=h, json=body)
        assert res.status_code == 200
        chs = res.json()["chapters"]
        assert chs[1]["title"] == "Webhooks en producción" and chs[1]["start"] == 900
        assert chs[0]["end"] == 900 and all(c["validated"] for c in chs)
        assert res.json()["warning"] and "40 %" in res.json()["warning"]

        overlap = {"chapters": [{"id": ids[1], "is_key": True, "start": 800}]}
        assert client.patch(f"/api/tutor/sessions/{sid}/chapters", headers=h, json=overlap).status_code == 400
        logistic_key = {"chapters": [{"id": ids[0], "is_key": True}]}
        assert client.patch(f"/api/tutor/sessions/{sid}/chapters", headers=h, json=logistic_key).status_code == 400
        outside = {"chapters": [{"id": ids[2], "is_key": False, "end": 20000}]}
        assert client.patch(f"/api/tutor/sessions/{sid}/chapters", headers=h, json=outside).status_code == 400
