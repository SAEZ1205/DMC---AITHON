"""
Inspección ligera de un MP4: duración, códecs y si el índice (moov) está al inicio.

Lee solo las cabeceras de las "cajas" (atoms) y la caja `moov`; NUNCA carga el archivo completo en memoria
(la caja `mdat`, donde está el video, se salta con `seek`). No modifica el archivo.
"""
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Tuple

MAX_MOOV_BYTES = 64 * 1024 * 1024      # defensa: un moov mayor que esto se considera sospechoso

# Sample entries que los navegadores de escritorio reproducen de forma amplia
BROWSER_FRIENDLY_VIDEO = {"avc1", "avc3"}                  # H.264
BROWSER_FRIENDLY_AUDIO = {"mp4a"}                          # AAC (o MP3 dentro de mp4a)


@dataclass
class Mp4Info:
    ok: bool
    duration_seconds: Optional[float] = None
    video_codec: Optional[str] = None          # fourcc, p. ej. 'avc1'
    audio_codec: Optional[str] = None          # fourcc, p. ej. 'mp4a'
    width: Optional[int] = None
    height: Optional[int] = None
    faststart: Optional[bool] = None           # moov antes que mdat => arranque rápido en el navegador
    size_bytes: int = 0
    problems: List[str] = field(default_factory=list)

    @property
    def browser_playable(self) -> Optional[bool]:
        """True si los códecs son los de máxima compatibilidad (H.264 + AAC); None si no se pudo determinar."""
        if not self.ok or self.video_codec is None:
            return None
        video_ok = self.video_codec in BROWSER_FRIENDLY_VIDEO
        audio_ok = self.audio_codec is None or self.audio_codec in BROWSER_FRIENDLY_AUDIO
        return video_ok and audio_ok

    def to_public(self) -> Dict[str, object]:
        return {
            "duration_seconds": self.duration_seconds, "video_codec": self.video_codec, "audio_codec": self.audio_codec,
            "resolution": f"{self.width}x{self.height}" if self.width and self.height else None,
            "faststart": self.faststart, "browser_compatible_codecs": self.browser_playable, "problems": self.problems,
        }


def _read_box_header(f: BinaryIO, limit: int) -> Optional[Tuple[str, int, int]]:
    """(tipo, tamaño total, tamaño de la cabecera) de la caja en la posición actual, o None al terminar."""
    start = f.tell()
    head = f.read(8)
    if len(head) < 8:
        return None
    size, kind = struct.unpack(">I4s", head)
    header = 8
    if size == 1:                               # tamaño de 64 bits
        ext = f.read(8)
        if len(ext) < 8:
            return None
        size, header = struct.unpack(">Q", ext)[0], 16
    elif size == 0:                             # hasta el final del archivo
        size = limit - start
    if size < header:
        return None
    return kind.decode("latin-1"), size, header


def _children(buf: bytes, offset: int = 0, end: Optional[int] = None):
    end = len(buf) if end is None else end
    while offset + 8 <= end:
        size, kind = struct.unpack(">I4s", buf[offset:offset + 8])
        header = 8
        if size == 1 and offset + 16 <= end:
            size, header = struct.unpack(">Q", buf[offset + 8:offset + 16])[0], 16
        if size < header or offset + size > end:
            return
        yield kind.decode("latin-1"), offset + header, offset + size
        offset += size


def _parse_moov(moov: bytes, info: Mp4Info) -> None:
    for kind, start, end in _children(moov):
        if kind == "mvhd":
            version = moov[start]
            if version == 1:
                timescale, duration = struct.unpack(">IQ", moov[start + 20:start + 32])
            else:
                timescale, duration = struct.unpack(">II", moov[start + 12:start + 20])
            if timescale:
                info.duration_seconds = round(duration / timescale, 3)
        elif kind == "trak":
            _parse_trak(moov, start, end, info)


def _parse_trak(buf: bytes, start: int, end: int, info: Mp4Info) -> None:
    handler = None
    for kind, s, e in _children(buf, start, end):
        if kind == "mdia":
            for k2, s2, e2 in _children(buf, s, e):
                if k2 == "hdlr":
                    handler = buf[s2 + 8:s2 + 12].decode("latin-1")
                elif k2 == "minf":
                    for k3, s3, e3 in _children(buf, s2, e2):
                        if k3 == "stbl":
                            for k4, s4, e4 in _children(buf, s3, e3):
                                if k4 == "stsd":
                                    _parse_stsd(buf, s4, e4, handler, info)


def _parse_stsd(buf: bytes, start: int, end: int, handler: Optional[str], info: Mp4Info) -> None:
    # stsd: versión+flags (4) + nº de entradas (4) + entradas (cada una es una caja con su fourcc)
    for kind, s, e in _children(buf, start + 8, end):
        if handler == "vide" and info.video_codec is None:
            info.video_codec = kind
            # VisualSampleEntry: 6 reservados + 2 índice + 16 + width(2) height(2)
            if e - s >= 28:
                info.width, info.height = struct.unpack(">HH", buf[s + 24:s + 28])
        elif handler == "soun" and info.audio_codec is None:
            info.audio_codec = kind


def probe_mp4(path: Path) -> Mp4Info:
    info = Mp4Info(ok=False)
    try:
        size = path.stat().st_size
        info.size_bytes = size
        order: List[str] = []
        with path.open("rb") as f:
            while f.tell() < size:
                pos = f.tell()
                box = _read_box_header(f, size)
                if box is None:
                    break
                kind, total, header = box
                order.append(kind)
                if kind == "moov":
                    if total > MAX_MOOV_BYTES:
                        info.problems.append("La caja 'moov' es sospechosamente grande; no se analiza.")
                        break
                    f.seek(pos + header)
                    _parse_moov(f.read(total - header), info)
                f.seek(pos + total)               # salta mdat y demás sin leerlos
        if "moov" not in order:
            info.problems.append("No se encontró la caja 'moov': el archivo no parece un MP4 válido o está incompleto.")
            return info
        if "mdat" in order:
            info.faststart = order.index("moov") < order.index("mdat")
        info.ok = info.duration_seconds is not None
        if not info.ok:
            info.problems.append("No se pudo leer la duración del MP4.")
        if info.video_codec is None:
            info.problems.append("No se encontró una pista de video.")
        elif info.video_codec not in BROWSER_FRIENDLY_VIDEO:
            info.problems.append(f"Códec de video '{info.video_codec}': puede no reproducirse en todos los navegadores.")
        if info.audio_codec and info.audio_codec not in BROWSER_FRIENDLY_AUDIO:
            info.problems.append(f"Códec de audio '{info.audio_codec}': puede no reproducirse en todos los navegadores.")
        if info.faststart is False:
            info.problems.append("El índice 'moov' está al final: el navegador necesitará pedir el final del archivo antes de "
                                 "iniciar (funciona con solicitudes Range, pero el arranque puede tardar).")
    except OSError as exc:
        info.problems.append(f"No se pudo leer el archivo ({type(exc).__name__}).")
    return info
