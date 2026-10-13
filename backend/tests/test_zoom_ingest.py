from datetime import datetime

from app import config
from app.services.zoom_ingest import normalize_name, parse_chat, parse_vtt, read_participants

SAMPLE = config.BACKEND_DIR / "data" / "sample" / "session_demo"
START, END = datetime(2026, 10, 8, 19, 30), datetime(2026, 10, 8, 22, 30)


def test_participants_with_spanish_headers_and_preamble():
    rows = read_participants(SAMPLE / "participants.csv", reference=START)
    assert len(rows) == 7
    ana = [r for r in rows if r.name == "Ana Torres"]
    assert len(ana) == 2  # salió y volvió: varias filas
    assert ana[0].join_at == datetime(2026, 10, 8, 19, 29, 40)
    maria = next(r for r in rows if r.name.startswith("María"))
    assert maria.email == ""


def test_participants_english_format(tmp_path):
    f = tmp_path / "participants.csv"
    f.write_text("Name (Original Name),User Email,Join Time,Leave Time,Duration (Minutes),Guest\n"
                 "Ana Torres,ana@x.pe,10/08/2026 07:31:00 PM,10/08/2026 10:20:00 PM,169,No\n", encoding="utf-8")
    rows = read_participants(f, reference=START)
    assert rows[0].join_at == datetime(2026, 10, 8, 19, 31)


def test_chat_formats(tmp_path):
    f = tmp_path / "chat.txt"
    f.write_text("19:45:12\t De Juan Pérez : hola profe\n"
                 "19:46:00 De Ana Torres a Todos: ¿se graba?\n"
                 "00:20:00 From Luis to Everyone:\n\tmensaje en otra línea\n", encoding="utf-8")
    lines = parse_chat(f, START, END)
    assert [line.author for line in lines] == ["Juan Pérez", "Ana Torres", "Luis"]
    assert lines[1].recipient == "Todos"
    assert lines[2].at == datetime(2026, 10, 8, 19, 50)  # tiempo relativo a la grabación
    assert lines[2].text == "mensaje en otra línea"


def test_vtt_speakers():
    segments = parse_vtt(SAMPLE / "transcript.vtt")
    assert segments[0].speaker == "Lincoln Villanueva"
    assert any(s.speaker == "Ana Torres" for s in segments)
    assert segments[-1].start > 10000


def test_normalize_name():
    assert normalize_name("María  Quispe (Invitado)") == "maria quispe"
