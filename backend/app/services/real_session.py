"""Sesión real: normaliza la carpeta que entrega el equipo y la aplica a una sesión del curso (spec v2, 3.1)."""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session

from .. import config
from ..models import ClassSession
from . import materials as mat
from .transcript_txt import parse_file
from .zoom_ingest import ingest_session, parse_vtt, store_segments

log = logging.getLogger("dmc.real_session")
LIMA_OFFSET = timedelta(hours=-5)
RE_ZOOM_NAME = re.compile(r"GMT(\d{8})-(\d{6})")


def load_metadata(folder: Path) -> dict:
    path = folder / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def has_real_content(folder: Path) -> bool:
    return (folder / "metadata.json").exists()


def _find_transcript(src: Path) -> Path | None:
    for name in ("transcript.vtt", "transcript.txt"):
        if (src / name).exists():
            return src / name
    vtts = sorted(src.glob("*.vtt"))
    if vtts:
        return vtts[0]
    txts = [p for p in sorted(src.glob("*.txt")) if "transcrip" in p.stem.lower()] or sorted(src.glob("*.txt"))
    return txts[0] if txts else None


def _find_materials(src: Path) -> Path | None:
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        if d.name.lower() in {"material", "materials", "materiales"}:
            return d
    return None


def _recorded_at(video: Path | None) -> datetime | None:
    m = RE_ZOOM_NAME.search(video.name) if video else None
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") + LIMA_OFFSET


CONTINUES = {"a", "al", "de", "del", "y", "e", "o", "en", "con", "para", "por", "la", "el", "los", "las", "sobre"}


def _title_from_deck(units: list[mat.Unit]) -> str | None:
    """«Sesión 01 – Introducción a / N8N y manejo…» → «Introducción a N8N y manejo…». La línea siguiente se
    une solo si el título queda cortado en una palabra de enlace."""
    if not units:
        return None
    lines = [l.strip() for l in units[0].text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        m = re.search(r"Sesi[oó]n\s*\d+\s*[–—-]\s*(.+)$", line)
        if not m:
            continue
        title = m.group(1).strip()
        for nxt in lines[i + 1:]:
            if title.split()[-1].lower() not in CONTINUES:
                break
            title = f"{title} {nxt}"
        return " ".join(title.split())
    return units[0].title or None


def normalize(src: Path, number: int, dest_root: Path | None = None) -> Path:
    """Copia la carpeta entregada (tal como viene) a data/zoom/<number>/ con nombres estándar.
    El video no se copia: metadata.json guarda su ruta. No pisa quiz.json ni key_slides.json existentes."""
    src = src.resolve()
    dest = (dest_root or config.ZOOM_DIR) / str(number)
    dest.mkdir(parents=True, exist_ok=True)

    transcript = _find_transcript(src)
    if transcript is None:
        raise FileNotFoundError(f"No encontré la transcripción en {src}")
    target = dest / ("transcript.vtt" if transcript.suffix.lower() == ".vtt" else "transcript.txt")
    if transcript.resolve() != target.resolve():
        shutil.copyfile(transcript, target)

    materials_src = _find_materials(src)
    if materials_src and materials_src.resolve() != (dest / "materials").resolve():
        if (dest / "materials").exists():
            shutil.rmtree(dest / "materials")
        shutil.copytree(materials_src, dest / "materials")

    for optional in ("quiz.json", "key_slides.json", "chat.txt", "participants.csv"):
        if (src / optional).exists() and not (dest / optional).exists():
            shutil.copyfile(src / optional, dest / optional)

    videos = sorted(src.glob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    video = videos[0] if videos else None
    deck = next((mat.extract(p) for p in mat.material_files(dest / "materials")
                 if p.suffix.lower() in {".pptx", ".pdf"}), [])
    parsed = parse_file(target) if target.suffix == ".txt" else None
    last = parsed.segments[-1].end if parsed and parsed.segments else None
    if target.suffix == ".vtt":
        segs = parse_vtt(target)
        last = segs[-1].end if segs else None

    meta = load_metadata(dest)
    recorded = _recorded_at(video)
    meta.update({
        "number": number,
        "title": meta.get("title") or _title_from_deck(deck) or src.name,
        "date": meta.get("date") or (recorded.date().isoformat() if recorded else None),
        "recorded_at": recorded.isoformat() if recorded else meta.get("recorded_at"),
        "video_url": meta.get("video_url"),
        "video_path": str(video) if video else meta.get("video_path"),
        "duration_s": meta.get("duration_s") or (int(math.ceil(last / 10) * 10) if last else None),
        "source_folder": str(src),
    })
    (dest / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def apply_to_session(db: Session, session: ClassSession, folder: Path) -> dict:
    """Carga en la sesión el contenido real de `folder`: título, duración, transcripción, materiales y video.
    Mantiene la fecha y hora de la sesión en el curso (la asistencia de la cohorte sigue en ese horario)."""
    meta = load_metadata(folder)
    if meta.get("title"):
        session.title = meta["title"]
    if meta.get("duration_s"):
        session.ends_at = session.starts_at + timedelta(seconds=float(meta["duration_s"]))
    duration = (session.ends_at - session.starts_at).total_seconds()

    vtt, txt = folder / "transcript.vtt", folder / "transcript.txt"
    approx = False
    if vtt.exists():
        segments = parse_vtt(vtt)
        session.transcript_path = str(vtt)
    elif txt.exists():
        parsed = parse_file(txt, duration)
        segments, approx = parsed.segments, parsed.approx_times
        session.transcript_path = str(txt)
    else:
        raise FileNotFoundError(f"{folder} no tiene transcript.vtt ni transcript.txt")
    store_segments(db, session, segments)
    session.approx_times = approx
    session.real_content = True
    session.video_url = meta.get("video_url") or None
    video = meta.get("video_path")
    session.video_path = video if video and Path(video).exists() else (
        str(folder / "recording.mp4") if (folder / "recording.mp4").exists() else None)
    db.add(session)
    db.commit()

    report = {"segments": len(segments), "approx_times": approx,
              "materials": mat.store_materials(db, session, folder)}
    if (folder / "participants.csv").exists() or (folder / "chat.txt").exists():
        ingest_session(db, session, folder, store_transcript=False, attendance=True)
    log.info("Sesión %s: contenido real desde %s (%s)", session.number, folder, report)
    return report
