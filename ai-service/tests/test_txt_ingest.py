"""Ingesta del TXT con marcas de tiempo: formatos, asociación texto-segundos, validaciones y reporte de problemas."""
import hashlib
from pathlib import Path

import pytest

from app.knowledge.real_lesson import REAL_COURSE_ID, REAL_LESSON_ID, TRANSCRIPT_FILE, VIDEO_FILE, load_real_lesson
from app.knowledge.txt_transcript import (
    CHUNK_MAX_CHARS, MAX_LINE_SPAN_SECONDS, ingest_timestamped_txt, parse_timestamp_line,
)
from app.media import probe_mp4

from conftest import SENTINEL_EMAIL, SENTINEL_NAME, SENTINEL_PHONE, SYNTH_DURATION, build_synthetic_txt


def ingest(text: str, duration=None, **kw):
    return ingest_timestamped_txt(text.encode("utf-8"), "c", "l", "Clase", video_duration=duration, **kw)


def codes(report):
    return [i.code for i in report.issues]


# ================================================================== marcas de tiempo
@pytest.mark.parametrize("line, expected", [
    ("0:05", 5), ("00:05", 5), ("17:09", 1029), ("9:59", 599), ("59:59", 3599),
    ("1:00:03", 3603), ("01:00:03", 3603), ("3:30:04", 12604), ("12:00:00", 43200), ("  17:09  ", 1029), ("17:09\r", 1029),
    ("75:30", 4530),          # MM:SS con minutos >= 60: se acepta (algunas herramientas no pasan a horas)
])
def test_valid_timestamps_with_and_without_hours(line, expected):
    assert parse_timestamp_line(line) == expected


@pytest.mark.parametrize("line", [
    "", "abc", "17", "17:9", "17:099", "12:60", "1:75:10", "1:00:60", "a las 2:30", "2:30,", "2:30 de la tarde",
    "17:09 ¿Qué es n8n?", "(17:09)", "-5:00", "1:2:3:4", "17.09", "１７:０９",
])
def test_lines_that_are_not_timestamps_are_never_parsed_as_such(line):
    assert parse_timestamp_line(line) is None


# ======================================================================= asociación
def test_text_is_associated_with_the_preceding_timestamp_and_normalized_to_seconds():
    res = ingest("0:05\nHola\n0:12\nVamos a empezar\n0:50\nSegundo bloque\n1:00:03\nYa pasó la primera hora\n", duration=3700)
    assert res.report.ok
    seg = res.lesson.segments
    assert [(s.start_seconds, s.source_label, s.source_line) for s in seg] == [
        (5.0, "0:05", 1), (50.0, "0:50", 5), (3603.0, "1:00:03", 7)]         # 0:05+0:12 | 0:50 (hueco largo corta) | 1:00:03
    assert seg[0].text == "Hola Vamos a empezar" and seg[2].text == "Ya pasó la primera hora"
    first = seg[0]
    assert first.lines[0].text == "Hola" and first.lines[0].label == "0:05" and first.lines[0].start_seconds == 5.0
    assert first.source_line == 1 and first.source_label == "0:05"          # referencia al archivo original


def test_each_line_keeps_its_own_second_inside_the_segment():
    res = ingest("0:10\nuno\n0:15\ndos\n0:22\ntres\n0:30\ncuatro\n", duration=60)
    (seg,) = res.lesson.segments
    assert [(ln.label, ln.start_seconds, ln.text) for ln in seg.lines] == [
        ("0:10", 10.0, "uno"), ("0:15", 15.0, "dos"), ("0:22", 22.0, "tres"), ("0:30", 30.0, "cuatro")]
    assert seg.text == "uno dos tres cuatro" and seg.start_seconds == 10.0


def test_crlf_lf_blank_lines_and_bom_are_tolerated():
    base = "0:05\nHola\n\n0:10\nMundo\n"
    for variant in (base, base.replace("\n", "\r\n"), "﻿" + base):
        res = ingest(variant, duration=30)
        assert res.report.ok and res.lesson.segments[0].text == "Hola Mundo"


def test_multiline_text_after_a_timestamp_is_joined():
    res = ingest("0:05\nprimera línea\nsegunda línea\n0:30\notra\n", duration=60)
    assert res.lesson.segments[0].lines[0].text == "primera línea segunda línea"


# ===================================================== números pronunciados vs marcas
def test_a_time_spoken_inside_a_sentence_is_not_a_timestamp():
    res = ingest("0:05\nSe ejecuta a las 2:30, por ejemplo\n0:20\nsiguiente\n", duration=60)
    assert res.report.timestamp_lines == 2 and "2:30, por ejemplo" in res.lesson.segments[0].text


def test_a_lone_number_line_that_goes_back_in_time_is_text_and_is_reported():
    res = ingest("10:00\ndigo una hora\n2:30\nsigo hablando\n10:20\nfin\n", duration=1000)
    assert res.report.timestamp_lines == 2 and res.report.rejected_timestamp_like_lines == 1
    assert "timestamp_no_monotono" in codes(res.report)
    seg = res.lesson.segments[0]
    assert "2:30" in seg.text and seg.start_seconds == 600.0               # no se abrió un segmento en el segundo 150
    issue = next(i for i in res.report.issues if i.code == "timestamp_no_monotono")
    assert issue.severity == "warning" and issue.line == 3                 # reportado con su línea, no corregido en silencio


def test_a_repeated_timestamp_is_text_and_reported():
    res = ingest("0:10\nuno\n0:10\ndos\n0:20\ntres\n", duration=60)
    assert "timestamp_repetido" in codes(res.report) and res.report.timestamp_lines == 2


def test_a_timestamp_beyond_the_video_duration_is_an_error_and_not_accepted():
    res = ingest("0:10\nuno\n9:59:59\nfuera de la duración\n", duration=120)
    assert not res.report.ok
    assert [i.code for i in res.report.errors] == ["timestamp_fuera_de_duracion"]
    assert res.report.timestamp_lines == 1 and "9:59:59" in res.lesson.segments[0].text


# ===================================================== pausas, finales y duraciones
def test_marks_without_text_are_pauses_not_segments():
    res = ingest("0:10\nuno\n0:20\n0:30\ndos\n", duration=60)
    assert "marca_sin_texto" in codes(res.report) and [ln.label for ln in res.lesson.segments[0].lines] == ["0:10", "0:30"]


def test_a_long_gap_is_reported_and_the_line_span_is_capped_not_stretched():
    res = ingest("0:24\npalabras sueltas\n4:09\nvuelve a hablar\n4:15\nfin\n", duration=400)
    assert "pausa_larga" in codes(res.report) and res.report.capped_spans == 1
    first = res.lesson.segments[0]
    assert first.start_seconds == 24.0 and first.end_seconds <= 24.0 + MAX_LINE_SPAN_SECONDS and first.end_is_estimated
    assert res.lesson.segments[1].start_seconds == 249.0                   # una pausa larga cierra el segmento


def test_the_last_segment_end_comes_from_the_video_duration_and_is_flagged_as_estimated():
    res = ingest("0:10\nuno\n0:50\nultimo\n", duration=62.5)
    last = res.lesson.segments[-1]
    assert last.end_seconds == 62.5 and last.end_is_estimated
    assert "ultimo_segmento_fin_estimado" in codes(res.report)


def test_without_video_duration_the_last_end_is_a_minimal_flagged_estimate():
    res = ingest("0:10\nuno\n0:50\nultimo\n", duration=None)
    last = res.lesson.segments[-1]
    assert last.end_seconds == last.start_seconds + 1.0 and last.end_is_estimated
    issue = next(i for i in res.report.issues if i.code == "ultimo_segmento_sin_duracion")
    assert issue.severity == "warning"


def test_last_segment_duration_is_never_longer_than_the_line_cap():
    res = ingest("0:10\nuno\n0:20\nultimo\n", duration=3600)
    assert res.lesson.segments[-1].end_seconds - 20.0 <= MAX_LINE_SPAN_SECONDS


def test_segments_never_overlap_and_stay_ordered():
    res = ingest_timestamped_txt(build_synthetic_txt(), "c", "l", "Clase", video_duration=SYNTH_DURATION)
    segs = res.lesson.segments
    assert all(a.end_seconds <= b.start_seconds + 1e-9 for a, b in zip(segs, segs[1:]))
    assert [s.segment_id for s in segs] == list(range(len(segs)))
    assert all(len(s.text) <= CHUNK_MAX_CHARS + 400 for s in segs) and segs[-1].end_seconds <= SYNTH_DURATION


# ==================================================================== problemas graves
def test_empty_or_markless_files_fail_loudly():
    for text in ("", "solo texto sin marcas\notra línea\n", "0:05\n0:10\n"):
        res = ingest(text, duration=100)
        assert res.lesson is None and not res.report.ok and "sin_segmentos" in codes(res.report)


def test_text_before_the_first_timestamp_is_reported_and_dropped():
    res = ingest("encabezado suelto\n0:05\ncontenido\n", duration=60)
    assert "texto_sin_marca_inicial" in codes(res.report) and "encabezado" not in res.lesson.segments[0].text


def test_non_utf8_input_is_decoded_with_a_warning():
    res = ingest_timestamped_txt("0:05\nañadir niño\n".encode("cp1252"), "c", "l", "Clase", video_duration=30)
    assert res.report.encoding == "cp1252" and "codificacion_no_utf8" in codes(res.report)
    assert "niño" in res.lesson.segments[0].text


def test_the_content_hash_is_the_sha256_of_the_original_bytes():
    raw = build_synthetic_txt()
    res = ingest_timestamped_txt(raw, "c", "l", "Clase", video_duration=SYNTH_DURATION)
    assert res.report.content_hash == hashlib.sha256(raw).hexdigest() == res.lesson.content_hash
    other = ingest_timestamped_txt(raw + b"\r\n7000\r\n", "c", "l", "Clase", video_duration=SYNTH_DURATION)
    assert other.report.content_hash != res.report.content_hash


def test_distributed_samples_are_checked_automatically_but_flagged_for_listening():
    res = ingest_timestamped_txt(build_synthetic_txt(), "c", "l", "Clase", video_duration=SYNTH_DURATION)
    samples = res.report.samples
    assert 5 <= len(samples) <= 10 and samples[0]["seconds"] == 5 and samples[-1]["seconds"] > 7000
    assert all(s["label_matches_seconds"] and s["inside_video_duration"] and s["chunk_contains_text"] for s in samples)
    assert all(s["needs_listening"] for s in samples)                      # la sincronía audio-texto NO se da por verificada


# =================================================================== privacidad
def test_personal_data_is_masked_in_memory_and_counted():
    res = ingest_timestamped_txt(build_synthetic_txt(), "c", "l", "Clase", video_duration=SYNTH_DURATION)
    text = " ".join(s.text for s in res.lesson.segments)
    for secret in (SENTINEL_EMAIL, SENTINEL_PHONE, "Maria", "Lopezzi", "Pedro"):
        assert secret not in text
    assert "[correo]" in text and "[teléfono]" in text and "[nombre]" in text
    assert res.report.pii_counts["email"] == 1 and res.report.pii_counts["phone"] == 1 and res.report.pii_counts["name"] >= 3
    assert "Maria" not in str(res.report.public()) and SENTINEL_EMAIL not in str(res.report.public())


def test_the_original_file_is_never_modified(fake_assets):
    path = fake_assets / TRANSCRIPT_FILE
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    real = load_real_lesson(fake_assets)
    assert real is not None and real.report.ok
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before          # ni contenido ni fecha de modificación
    assert SENTINEL_EMAIL.encode() in path.read_bytes()                    # el original conserva el dato; solo la copia en memoria se enmascara


def test_extra_names_file_masks_additional_names(fake_assets):
    (fake_assets / "mask-names.txt").write_text("# nombres a ocultar\nPlantilla\n", encoding="utf-8")
    real = load_real_lesson(fake_assets)
    assert "Plantilla" not in " ".join(s.text for s in real.lesson.segments)


# ===================================================== carga de la clase (identidad)
def test_real_lesson_identity_is_consistent(fake_assets):
    real = load_real_lesson(fake_assets)
    assert (real.lesson.course_id, real.lesson.lesson_id) == (REAL_COURSE_ID, REAL_LESSON_ID) == ("n8n-intro", "clase-01-introduccion-n8n")
    assert all(s.course_id == REAL_COURSE_ID and s.lesson_id == REAL_LESSON_ID for s in real.lesson.segments)
    assert real.lesson.title == "Introducción a n8n — Clase 1" and real.lesson.source_format == "txt"
    assert real.video_path == (fake_assets / VIDEO_FILE).resolve() and real.mp4.duration_seconds == SYNTH_DURATION
    assert real.report.video_duration_seconds == SYNTH_DURATION and real.lesson.duration_seconds <= SYNTH_DURATION


def test_missing_assets_degrade_gracefully(tmp_path, fake_assets):
    assert load_real_lesson(tmp_path / "no-existe") is None
    (fake_assets / VIDEO_FILE).unlink()
    real = load_real_lesson(fake_assets)
    assert real is not None and real.video_path is None and any("sin reproductor" in w for w in real.warnings)


# ============================== contra el archivo REAL (se omiten si demo-assets/ no existe)
REAL_DIR = Path(__file__).resolve().parents[2] / "demo-assets"
needs_real = pytest.mark.skipif(not (REAL_DIR / TRANSCRIPT_FILE).is_file() or not (REAL_DIR / VIDEO_FILE).is_file(),
                                reason="demo-assets/ no está presente en este equipo")


@pytest.fixture(scope="module")
def real_lesson():
    return load_real_lesson(REAL_DIR)


@needs_real
def test_real_file_is_ingested_without_errors(real_lesson):
    r = real_lesson.report
    assert r.ok and not r.errors and r.timestamp_lines == 2107 and r.rejected_timestamp_like_lines == 0
    assert (r.first_seconds, r.last_seconds) == (5, 12604)                 # 0:05 … 3:30:04
    assert r.video_duration_seconds == pytest.approx(12611.328, abs=0.01) and 280 <= r.segments <= 290


@needs_real
def test_real_file_timeline_is_monotonic_inside_the_video_and_non_overlapping(real_lesson):
    segs, duration = real_lesson.lesson.segments, real_lesson.mp4.duration_seconds
    assert all(0 <= s.start_seconds < s.end_seconds <= duration + 1e-6 for s in segs)
    assert all(a.end_seconds <= b.start_seconds + 1e-9 for a, b in zip(segs, segs[1:]))
    assert segs[-1].end_is_estimated and segs[-1].end_seconds == pytest.approx(duration, abs=0.01)


@needs_real
def test_real_file_minute_17_09_is_where_the_class_defines_n8n(real_lesson):
    """Punto verificado manualmente por el equipo (~17:09). Aquí solo se comprueban NÚMEROS, no se cita la clase."""
    seg = next(s for s in real_lesson.lesson.segments if s.start_seconds <= 17 * 60 + 9 < s.end_seconds)
    assert seg.source_label == "16:39" and (seg.start_seconds, seg.end_seconds) == (999.0, 1045.0)
    line = next(ln for ln in seg.lines if ln.label == "17:09")
    assert line.start_seconds == 1029.0 and "n8n" in seg.text.lower()


@needs_real
def test_real_file_hours_format_is_read_correctly(real_lesson):
    labels = {ln.label: ln.start_seconds for s in real_lesson.lesson.segments for ln in s.lines}
    assert labels["1:00:03"] == 3603.0 and labels["3:30:04"] == 12604.0 and labels["59:59"] == 3599.0


@needs_real
def test_real_file_distributed_samples_pass_automatic_checks_but_need_listening(real_lesson):
    samples = real_lesson.report.samples
    assert len(samples) == 10
    assert all(s["label_matches_seconds"] and s["inside_video_duration"] and s["chunk_contains_text"] for s in samples)
    assert all(s["needs_listening"] for s in samples)


@needs_real
def test_real_mp4_is_browser_compatible_and_matches_the_stated_duration():
    info = probe_mp4(REAL_DIR / VIDEO_FILE)
    assert info.ok and (info.video_codec, info.audio_codec) == ("avc1", "mp4a") and info.browser_playable
    assert info.faststart is True and 12610 <= info.duration_seconds <= 12612    # 3:30:11


@needs_real
def test_real_personal_data_does_not_survive_masking(real_lesson):
    text = " ".join(s.text for s in real_lesson.lesson.segments)
    import re
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text) and "[nombre]" in text
