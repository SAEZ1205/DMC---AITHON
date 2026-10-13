import json

import pytest

from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.transcripts import (
    TranscriptParseError, parse_json_transcript, parse_timestamp, parse_vtt,
)
from app.models.transcript import format_timestamp

from conftest import LESSON_1, LESSON_2


# --------------------------------------------------------------- timestamps
@pytest.mark.parametrize("raw, expected", [
    ("00:00:42.000", 42.0),
    ("01:35.500", 95.5),
    ("1:02:03.250", 3723.25),
    ("00:01:05,5", 65.5),     # coma y fracción corta
    ("00:00:07", 7.0),
])
def test_parse_timestamp(raw, expected):
    assert parse_timestamp(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "12", "00:75", "00:00:61.000"])
def test_parse_timestamp_rejects_invalid(raw):
    with pytest.raises(TranscriptParseError):
        parse_timestamp(raw)


def test_format_timestamp():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(382.9) == "06:22"
    assert format_timestamp(3723) == "1:02:03"


# ---------------------------------------------------------------------- VTT
VTT = """﻿WEBVTT

NOTE
title: Mi clase
is_official_dmc: false

intro
00:00:00.000 --> 00:00:10.000 align:start
<v Docente>Hola &amp; bienvenidos
a la clase.</v>

STYLE
::cue { color: red }

00:10.000 --> 00:20.500

00:20.500 --> 00:30.000
Segundo   segmento
"""


def test_parse_vtt_cues_tags_ids_and_multiline():
    lesson = parse_vtt(VTT.replace("\n", "\r\n"), "curso-x", "clase-1")
    assert lesson.title == "Mi clase"
    assert lesson.source_format == "vtt"
    assert lesson.is_official_dmc is False
    # el cue vacío se omite, STYLE y NOTE no generan segmentos
    assert [s.text for s in lesson.segments] == ["Hola & bienvenidos a la clase.", "Segundo segmento"]
    assert [s.segment_id for s in lesson.segments] == [0, 1]
    assert lesson.segments[0].start_seconds == 0 and lesson.segments[0].end_seconds == 10
    assert lesson.segments[1].start_seconds == 20.5
    assert all(s.course_id == "curso-x" and s.lesson_id == "clase-1" for s in lesson.segments)


@pytest.mark.parametrize("content, message", [
    ("00:00:00.000 --> 00:00:05.000\nHola", "WEBVTT"),
    ("WEBVTT\n\nsolo texto sin tiempo", "línea de tiempo"),
    ("WEBVTT\n\n00:00:09.000 --> 00:00:05.000\nAl revés", "end_seconds"),
    ("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n\n", "no contiene cues"),
    ("WEBVTT\n\nNOTE\nlesson_id: otra\n\n00:00:00.000 --> 00:00:05.000\nHola", "no coincide"),
])
def test_parse_vtt_errors(content, message):
    with pytest.raises(TranscriptParseError, match=message):
        parse_vtt(content, "curso-x", "clase-1")


def test_invalid_ids_are_rejected():
    with pytest.raises(TranscriptParseError):
        parse_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHola", "../etc", "clase-1")


# --------------------------------------------------------------------- JSON
def test_parse_json_transcript():
    data = {"title": "T", "segments": [
        {"start_seconds": 0, "end_seconds": 30, "text": "uno"},
        {"start_seconds": 30, "end_seconds": 61.5, "text": "dos"},
    ]}
    lesson = parse_json_transcript(json.dumps(data), "c", "l")
    assert lesson.source_format == "json"
    assert lesson.duration_seconds == 61.5
    assert lesson.segments[1].segment_id == 1
    assert lesson.segments[1].timestamp_range == "00:30–01:01"


@pytest.mark.parametrize("data", [
    "{no es json",
    "[]",
    {"segments": []},
    {"segments": [{"start_seconds": 5, "end_seconds": 5, "text": "x"}]},
    {"segments": [{"start_seconds": 0, "text": "falta end"}]},
    {"course_id": "otro", "segments": [{"start_seconds": 0, "end_seconds": 1, "text": "x"}]},
])
def test_parse_json_errors(data):
    with pytest.raises(TranscriptParseError):
        parse_json_transcript(data, "c", "l")


# -------------------------------------------------- transcripciones DEMO reales
def test_demo_lessons_are_loaded_without_errors(retriever):
    assert retriever.load_errors == []
    ids = {l.lesson_id for l in retriever.list_lessons("sql-demo")}
    assert ids == {LESSON_1, LESSON_2}


def test_demo_vtt_lesson_content(retriever):
    lesson = retriever.get_lesson("sql-demo", LESSON_1)
    assert lesson.source_format == "vtt"
    assert len(lesson.segments) == 9
    assert lesson.duration_seconds == 485.0  # 00:08:05
    first, last = lesson.segments[0], lesson.segments[-1]
    assert (first.start_seconds, first.end_seconds) == (0.0, 42.0)
    assert (last.start_seconds, last.end_seconds) == (440.0, 485.0)
    assert last.timestamp_range == "07:20–08:05"
    # los tags <v ...> del VTT no deben filtrarse al texto
    assert all("<" not in s.text and ">" not in s.text for s in lesson.segments)
    assert "SELECT" in lesson.segments[3].text


def test_demo_json_lesson_content(retriever):
    lesson = retriever.get_lesson("sql-demo", LESSON_2)
    assert lesson.source_format == "json"
    assert len(lesson.segments) == 10
    assert lesson.duration_seconds == 805.0


@pytest.mark.parametrize("lesson_id", [LESSON_1, LESSON_2])
def test_demo_lessons_are_clearly_fictional_and_well_formed(retriever, lesson_id):
    lesson = retriever.get_lesson("sql-demo", lesson_id)
    assert lesson.is_official_dmc is False
    assert "FICTICIO" in lesson.disclaimer and "ni es material oficial de DMC" in lesson.disclaimer
    starts = [s.start_seconds for s in lesson.segments]
    assert starts == sorted(starts)
    assert all(s.end_seconds > s.start_seconds for s in lesson.segments)
    assert [s.segment_id for s in lesson.segments] == list(range(len(lesson.segments)))


def test_sql_course_topics_still_load_despite_utf8_bom(retriever):
    """Regresión: el JSON del curso trae BOM y antes json.load lo rechazaba en silencio."""
    course = retriever.get_course("sql-demo")
    assert course is not None and len(course["topics"]) == 7
    assert [c.course_id for c in retriever.list_courses()] == ["sql-demo"]


# ------------------------------------------------------ carga con archivos malos
def test_bad_transcript_files_are_skipped_and_reported(tmp_path):
    course_dir = tmp_path / "t" / "curso-a"
    course_dir.mkdir(parents=True)
    (course_dir / "buena.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHola mundo", encoding="utf-8")
    (course_dir / "mala.vtt").write_text("esto no es vtt", encoding="utf-8")
    (course_dir / "Mala_ID.json").write_text("{}", encoding="utf-8")  # id con mayúscula
    r = KnowledgeRetriever(courses_dir=tmp_path / "nada", transcripts_dir=tmp_path / "t")
    assert [l.lesson_id for l in r.list_lessons("curso-a")] == ["buena"]
    assert len(r.load_errors) == 2
