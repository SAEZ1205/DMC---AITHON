"""Ingesta tolerante de las exportaciones de Zoom: participants.csv, chat.txt, transcript.vtt y recording.mp4."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import webvtt
from dateutil import parser as dateparser
from sqlmodel import Session, delete, select

from ..models import (
    ClassSession,
    Enrollment,
    TranscriptSegment,
    User,
    ZoomChat,
    ZoomInterval,
    ZoomVoice,
)


def normalize_name(value: str) -> str:
    value = re.sub(r"\(.*?\)", " ", value or "")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-zA-Z0-9 ]+", " ", value).lower()
    return re.sub(r"\s+", " ", value).strip()


def _norm_header(value: str) -> str:
    return normalize_name(value.replace("_", " ")) if value else ""


# --- participants.csv ---

@dataclass
class ParticipantRow:
    name: str
    email: str
    join_at: datetime
    leave_at: datetime


_COLUMN_KEYS = {
    "name": ("name", "nombre"),
    "email": ("email", "correo", "e mail"),
    "join": ("join", "entrada", "unio", "ingreso"),
    "leave": ("leave", "salida", "abandon"),
}


def _map_columns(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(header):
        col = _norm_header(raw)
        for key, needles in _COLUMN_KEYS.items():
            if key in mapping:
                continue
            if any(n in col for n in needles):
                if key == "name" and ("email" in col or "correo" in col):
                    continue
                mapping[key] = idx
                break
    return mapping


def _parse_dt(value: str, dayfirst: bool, reference: datetime | None) -> datetime:
    value = value.strip()
    parsed = dateparser.parse(value, dayfirst=dayfirst)
    if reference is not None and abs((parsed.date() - reference.date()).days) > 2:
        alt = dateparser.parse(value, dayfirst=not dayfirst)
        if abs((alt.date() - reference.date()).days) <= 2:
            return alt
    return parsed.replace(tzinfo=None)


def read_participants(path: Path, reference: datetime | None = None) -> list[ParticipantRow]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines()]
    rows: list[ParticipantRow] = []
    header_idx = None
    mapping: dict[str, int] = {}
    for i, line in enumerate(lines):
        cells = next(csv.reader([line]), [])
        candidate = _map_columns(cells)
        if {"name", "join", "leave"} <= candidate.keys():
            header_idx, mapping = i, candidate
            break
    if header_idx is None:
        return rows
    header_text = " ".join(_norm_header(c) for c in next(csv.reader([lines[header_idx]])))
    dayfirst = any(w in header_text for w in ("nombre", "entrada", "salida", "correo"))
    reader = csv.reader(io.StringIO("\n".join(lines[header_idx + 1:])))
    for cells in reader:
        if not cells or len(cells) <= max(mapping.values()):
            continue
        name = cells[mapping["name"]].strip()
        join_raw, leave_raw = cells[mapping["join"]].strip(), cells[mapping["leave"]].strip()
        if not name or not join_raw or not leave_raw:
            continue
        try:
            join_at = _parse_dt(join_raw, dayfirst, reference)
            leave_at = _parse_dt(leave_raw, dayfirst, reference)
        except (ValueError, OverflowError):
            continue
        email = cells[mapping["email"]].strip().lower() if "email" in mapping else ""
        if leave_at > join_at:
            rows.append(ParticipantRow(name=name, email=email, join_at=join_at, leave_at=leave_at))
    return rows


# --- chat.txt ---

@dataclass
class ChatLine:
    at: datetime
    author: str
    recipient: str
    text: str


_CHAT_WITH_TO = re.compile(
    r"^(\d{1,2}:\d{2}:\d{2})\s+(?:De|From)\s+(.+?)\s+(?:a|to|para)\s+(.+?)\s*:\s?(.*)$", re.IGNORECASE
)
_CHAT_SIMPLE = re.compile(r"^(\d{1,2}:\d{2}:\d{2})\s+(?:De|From)\s+(.+?)\s*:\s?(.*)$", re.IGNORECASE)


def _chat_time(clock: str, start: datetime, end: datetime) -> datetime:
    h, m, s = (int(x) for x in clock.split(":"))
    absolute = start.replace(hour=h % 24, minute=m, second=s)
    if start - timedelta(hours=1) <= absolute <= end + timedelta(hours=1):
        return absolute
    return start + timedelta(hours=h, minutes=m, seconds=s)  # tiempo relativo a la grabación


def parse_chat(path: Path, start: datetime, end: datetime) -> list[ChatLine]:
    out: list[ChatLine] = []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if raw.startswith(("\t", " ")) and out and not re.match(r"^\s*\d{1,2}:\d{2}:\d{2}", raw):
            out[-1].text = (out[-1].text + " " + line.strip()).strip()
            continue
        match = _CHAT_WITH_TO.match(line.strip())
        if match:
            clock, author, recipient, text = match.groups()
        else:
            match = _CHAT_SIMPLE.match(line.strip())
            if not match:
                continue
            clock, author, text = match.groups()
            recipient = "Todos"
        out.append(ChatLine(_chat_time(clock, start, end), author.strip(), recipient.strip(), text.strip()))
    return out


# --- transcript.vtt ---

@dataclass
class VttSegment:
    start: float
    end: float
    speaker: str
    text: str


def parse_vtt(path: Path) -> list[VttSegment]:
    segments: list[VttSegment] = []
    for caption in webvtt.read(str(path)):
        text = " ".join(caption.text.split())
        speaker = ""
        if ": " in text:
            head, body = text.split(": ", 1)
            if 0 < len(head) <= 60 and not re.search(r"[.?!]", head):
                speaker, text = head.strip(), body.strip()
        segments.append(VttSegment(caption.start_in_seconds, caption.end_in_seconds, speaker, text))
    return segments


# --- vinculación de identidades ---

class IdentityResolver:
    """Vincula filas de Zoom a alumnos: primero por correo, si no, por nombre normalizado (confianza baja)."""

    def __init__(self, db: Session, course_id: int):
        users = db.exec(
            select(User).join(Enrollment, Enrollment.user_id == User.id).where(Enrollment.course_id == course_id)
        ).all()
        self.by_email = {u.email.lower(): u for u in users}
        self.by_name = {normalize_name(u.name): u for u in users}

    def resolve(self, name: str, email: str = "") -> tuple[User | None, str]:
        if email and email.lower() in self.by_email:
            return self.by_email[email.lower()], "high"
        user = self.by_name.get(normalize_name(name))
        return (user, "low") if user else (None, "none")


@dataclass
class IngestReport:
    session_id: int
    intervals: int = 0
    unmatched_participants: int = 0
    low_confidence: int = 0
    chat_messages: int = 0
    transcript_segments: int = 0
    voice_turns: int = 0
    video: bool = False


def ingest_session(db: Session, session: ClassSession, folder: Path, store_transcript: bool = True,
                   attendance: bool = True) -> IngestReport:
    """Reemplaza los datos de Zoom de la sesión con lo que haya en `folder`.
    attendance=False solo toma transcripción y video; store_transcript=False solo cuenta turnos de voz."""
    report = IngestReport(session_id=session.id)
    resolver = IdentityResolver(db, session.course_id)

    participants = folder / "participants.csv"
    if attendance and participants.exists():
        db.exec(delete(ZoomInterval).where(ZoomInterval.session_id == session.id))
        for row in read_participants(participants, reference=session.starts_at):
            user, confidence = resolver.resolve(row.name, row.email)
            if user is None:
                report.unmatched_participants += 1
                continue
            report.low_confidence += confidence == "low"
            db.add(ZoomInterval(session_id=session.id, user_id=user.id, join_at=row.join_at,
                                leave_at=row.leave_at, match_confidence=confidence))
            report.intervals += 1

    chat = folder / "chat.txt"
    if attendance and chat.exists():
        db.exec(delete(ZoomChat).where(ZoomChat.session_id == session.id))
        for line in parse_chat(chat, session.starts_at, session.ends_at):
            user, _ = resolver.resolve(line.author)
            if user:
                db.add(ZoomChat(session_id=session.id, user_id=user.id, at=line.at, text=line.text))
                report.chat_messages += 1

    vtt = folder / "transcript.vtt"
    if vtt.exists():
        segments = parse_vtt(vtt)
        if attendance:
            db.exec(delete(ZoomVoice).where(ZoomVoice.session_id == session.id))
            turns: dict[int, int] = {}
            for seg in segments:
                if seg.speaker:
                    user, _ = resolver.resolve(seg.speaker)
                    if user:
                        turns[user.id] = turns.get(user.id, 0) + 1
            for user_id, count in turns.items():
                db.add(ZoomVoice(session_id=session.id, user_id=user_id, turns=count))
                report.voice_turns += count
        if store_transcript:
            store_segments(db, session, segments)
            session.transcript_path = str(vtt)
            report.transcript_segments = len(segments)

    video = folder / "recording.mp4"
    if video.exists():
        session.video_path = str(video)
        report.video = True
    db.add(session)
    db.commit()
    return report


def store_segments(db: Session, session: ClassSession, segments: list[VttSegment]) -> None:
    db.exec(delete(TranscriptSegment).where(TranscriptSegment.session_id == session.id))
    for seg in segments:
        db.add(TranscriptSegment(session_id=session.id, start=seg.start, end=seg.end, speaker=seg.speaker, text=seg.text))
