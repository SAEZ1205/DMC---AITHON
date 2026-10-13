"""Sondeo de MP4: duración, códecs, faststart y robustez ante archivos malos (sin cargar el archivo en memoria)."""
import struct

import pytest

from app.media import probe_mp4

from mp4_builder import box, build_mp4


def write(tmp_path, data: bytes, name="v.mp4"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_probe_reads_duration_codecs_resolution_and_faststart(tmp_path):
    info = probe_mp4(write(tmp_path, build_mp4(3725.5, width=1920, height=1080)))
    assert info.ok and info.duration_seconds == 3725.5 and info.faststart is True
    assert (info.video_codec, info.audio_codec, info.width, info.height) == ("avc1", "mp4a", 1920, 1080)
    assert info.browser_playable is True and info.problems == []
    assert info.to_public()["resolution"] == "1920x1080" and "path" not in str(info.to_public())


def test_moov_at_the_end_is_reported_but_still_valid(tmp_path):
    info = probe_mp4(write(tmp_path, build_mp4(100, faststart=False)))
    assert info.ok and info.faststart is False and any("moov" in p and "final" in p for p in info.problems)


@pytest.mark.parametrize("video, playable", [(b"avc1", True), (b"avc3", True), (b"hvc1", False), (b"hev1", False), (b"av01", False)])
def test_only_h264_with_aac_is_reported_as_broadly_playable(tmp_path, video, playable):
    info = probe_mp4(write(tmp_path, build_mp4(100, video=video)))
    assert info.browser_playable is playable and (not playable) == any(video.decode() in p for p in info.problems)


def test_non_aac_audio_is_flagged(tmp_path):
    info = probe_mp4(write(tmp_path, build_mp4(100, audio=b"ac-3")))
    assert info.browser_playable is False and any("ac-3" in p for p in info.problems)


def test_video_without_audio_track_is_playable(tmp_path):
    info = probe_mp4(write(tmp_path, build_mp4(100, audio=b"")))
    assert info.ok and info.audio_codec is None and info.browser_playable is True


@pytest.mark.parametrize("data", [b"", b"no es un mp4", b"\x00" * 64, box(b"ftyp", b"isom") + box(b"mdat", b"x" * 100)])
def test_garbage_and_incomplete_files_are_rejected_without_exceptions(tmp_path, data):
    info = probe_mp4(write(tmp_path, data))
    assert info.ok is False and info.problems and info.browser_playable is None


def test_a_huge_moov_is_not_loaded(tmp_path):
    fake = struct.pack(">I4s", 0xFFFFFFF0, b"moov") + b"\x00" * 64
    info = probe_mp4(write(tmp_path, box(b"ftyp", b"isom") + fake))
    assert info.ok is False and any("sospechosamente" in p or "moov" in p for p in info.problems)


def test_missing_file_is_reported(tmp_path):
    info = probe_mp4(tmp_path / "no-existe.mp4")
    assert info.ok is False and any("No se pudo leer" in p for p in info.problems)


def test_the_mdat_payload_is_skipped_not_read(tmp_path, monkeypatch):
    path = write(tmp_path, build_mp4(100, mdat_bytes=5_000_000))
    reads = []
    real_open = type(path).open

    def spy(self, *a, **k):
        f = real_open(self, *a, **k)
        original = f.read
        f.read = lambda n=-1: (reads.append(n), original(n))[1]
        return f
    monkeypatch.setattr(type(path), "open", spy)
    assert probe_mp4(path).ok
    assert max(reads) < 1_000_000                       # nunca se leyó el mdat (5 MB) ni el archivo completo
