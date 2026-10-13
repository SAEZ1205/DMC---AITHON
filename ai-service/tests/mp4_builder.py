"""Construye MP4 mínimos pero estructuralmente válidos (ftyp/moov/trak/mdat) para probar el sondeo y el video con Range."""
import os
import struct


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mvhd(duration_seconds: float, timescale: int = 1000) -> bytes:
    body = struct.pack(">B3xIIII", 0, 0, 0, timescale, int(duration_seconds * timescale)) + b"\x00" * 80
    return box(b"mvhd", body)


def trak(handler: bytes, codec: bytes, width: int = 1280, height: int = 720) -> bytes:
    if handler == b"vide":
        entry = b"\x00" * 24 + struct.pack(">HH", width, height) + b"\x00" * 50
    else:
        entry = b"\x00" * 28
    stsd = box(b"stsd", struct.pack(">II", 0, 1) + box(codec, entry))
    hdlr = box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 12)
    return box(b"trak", box(b"mdia", hdlr + box(b"minf", box(b"stbl", stsd))))


def build_mp4(duration_seconds: float = 7300.0, video: bytes = b"avc1", audio: bytes = b"mp4a",
              faststart: bool = True, mdat_bytes: int = 200_000, width: int = 1280, height: int = 720) -> bytes:
    moov = box(b"moov", mvhd(duration_seconds) + trak(b"vide", video, width, height) + (trak(b"soun", audio) if audio else b""))
    mdat = box(b"mdat", os.urandom(mdat_bytes))
    ftyp = box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    return ftyp + (moov + mdat if faststart else mdat + moov)
