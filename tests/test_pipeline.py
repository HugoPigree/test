import json
import subprocess

import pytest

from autoclip import pipeline, subtitles, video
from autoclip.config import Config


def fake_transcribe(wav, cfg):
    assert wav.exists() and wav.stat().st_size > 0
    return [subtitles.Word(0.5, 0.9, "Non"), subtitles.Word(0.9, 1.4, "mais"),
            subtitles.Word(1.4, 2.0, "c'est"), subtitles.Word(2.0, 2.6, "énorme !"),
            subtitles.Word(3.0, 3.5, "Regarde")]


@pytest.mark.parametrize("layout", ["blur", "crop", "split"])
def test_end_to_end(session_dir, tmp_path, monkeypatch, layout):
    monkeypatch.setattr(subtitles, "transcribe", fake_transcribe)
    cfg = Config()
    cfg.paths.export = str(tmp_path / "ready_to_post")
    cfg.vertical.layout = layout
    cfg.vertical.preset = "ultrafast"
    cfg.detection.max_clips = 3 if layout == "blur" else 1

    clips = pipeline.process_session(cfg, session_dir)
    assert len(clips) == cfg.detection.max_clips
    for c in clips:
        info = video.probe(c / "video.mp4")
        assert (info["width"], info["height"]) == (1080, 1920)
        assert info["has_audio"]
        meta = json.loads((c / "meta.json").read_text(encoding="utf-8"))
        assert abs(info["duration"] - (meta["clip_end"] - meta["clip_start"])) < 0.5
        assert (c / "thumbnail.jpg").stat().st_size > 0
        assert (c / "subtitles.ass").exists() and not (c / "audio.wav").exists()
        caption = (c / "caption.txt").read_text(encoding="utf-8")
        assert caption.startswith("Non mais c'est énorme !") and "#jltomy" in caption
        assert meta["tiktok"] is None

    # Idempotent : relancer ne réencode rien et tout reste "à uploader".
    assert pipeline.process_session(cfg, session_dir) == clips
    assert pipeline.pending_clips(cfg) == sorted(clips)


def test_subtitle_failure_does_not_block_export(session_dir, tmp_path, monkeypatch):
    def boom(wav, cfg):
        raise RuntimeError("pas de réseau pour télécharger le modèle")

    monkeypatch.setattr(subtitles, "transcribe", boom)
    cfg = Config()
    cfg.paths.export = str(tmp_path / "out")
    cfg.vertical.preset = "ultrafast"
    cfg.detection.max_clips = 1
    clips = pipeline.process_session(cfg, session_dir)
    assert len(clips) == 1 and (clips[0] / "video.mp4").exists()
    assert "SOIRÉE CHILL" in (clips[0] / "caption.txt").read_text(encoding="utf-8")


def test_probe_duration_of_fmp4_twitch_recording(tmp_path):
    # Comme Streamlink sur une chaîne Twitch en fMP4 : init + segments pris en cours de live
    # (horodatages qui ne commencent pas à 0). 3 segments de 2 s => 6 s de vidéo.
    hls = tmp_path / "hls"
    hls.mkdir()
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
        "-f", "lavfi", "-i", "sine", "-t", "20", "-c:v", "libx264", "-preset", "ultrafast",
        "-g", "60", "-c:a", "aac", "-f", "hls", "-hls_segment_type", "fmp4", "-hls_time", "2",
        "-hls_list_size", "0", str(hls / "live.m3u8")], check=True)
    rec = tmp_path / "stream.ts"
    rec.write_bytes(b"".join((hls / name).read_bytes()
                             for name in ["init.mp4", "live5.m4s", "live6.m4s", "live7.m4s"]))
    assert video.probe(rec)["duration"] == pytest.approx(6.0, abs=0.2)
