import json
import subprocess

import pytest

from autoclip import pipeline, subtitles, video
from autoclip.config import Config
from autoclip.peaks import Peak


def fake_transcribe(wav, cfg, title=""):
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
    def boom(wav, cfg, title=""):
        raise RuntimeError("pas de réseau pour télécharger le modèle")

    monkeypatch.setattr(subtitles, "transcribe", boom)
    cfg = Config()
    cfg.paths.export = str(tmp_path / "out")
    cfg.vertical.preset = "ultrafast"
    cfg.detection.max_clips = 1
    clips = pipeline.process_session(cfg, session_dir)
    assert len(clips) == 1 and (clips[0] / "video.mp4").exists()
    # Pas de parole : l'accroche vient du chat, et elle est quand même incrustée.
    caption = (clips[0] / "caption.txt").read_text(encoding="utf-8")
    assert caption.split(" #")[0] in {"MDR", "KEKW", "PTDRRR", "CLIP CLIP", "ahahahah", "OMEGALUL", "jpp", "????"}
    assert ",Hook,," in (clips[0] / "subtitles.ass").read_text(encoding="utf-8")


def test_hook_is_the_sentence_spoken_at_the_peak():
    words = [subtitles.Word(1.0, 1.5, "Bon"), subtitles.Word(1.5, 2.0, "on"),
             subtitles.Word(2.0, 2.5, "commence."),
             subtitles.Word(20.0, 20.5, "Non"), subtitles.Word(20.5, 21.0, "il"),
             subtitles.Word(21.0, 21.5, "l'a"), subtitles.Word(21.5, 22.0, "fait !"),
             subtitles.Word(24.0, 24.5, "Quoi ?")]
    peak = Peak(t_peak=123.0, start=100.0, end=129.0, score=5, activity=20, baseline=3,
                n_messages=30, sample=["MAIS NON"])
    cfg = Config()
    assert pipeline.make_hook(cfg, words, peak, "titre") == "Non il l'a fait !"
    assert pipeline.make_hook(cfg, [], peak, "titre") == "MAIS NON"
    peak.sample = []
    assert pipeline.make_hook(cfg, [], peak, "titre") == "titre"
    cfg.export.hook_max_chars = 12
    assert pipeline.make_hook(cfg, words, peak, "") == "Non il l'a…"


def test_music_and_peak_zoom(session_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(subtitles, "transcribe", fake_transcribe)
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=5",
                    str(music_dir / "boucle.mp3")], check=True)
    cfg = Config()
    cfg.paths.export = str(tmp_path / "out")
    cfg.vertical.preset = "ultrafast"
    cfg.vertical.peak_zoom = True
    cfg.music.enabled = True
    cfg.music.dir = str(music_dir)
    cfg.detection.max_clips = 1
    clips = pipeline.process_session(cfg, session_dir)
    assert len(clips) == 1
    info = video.probe(clips[0] / "video.mp4")
    meta = json.loads((clips[0] / "meta.json").read_text(encoding="utf-8"))
    # La musique (5 s) boucle : la vidéo garde la durée du clip.
    assert abs(info["duration"] - (meta["clip_end"] - meta["clip_start"])) < 0.5
    assert info["has_audio"] and (info["width"], info["height"]) == (1080, 1920)
    assert meta["music"] == "boucle.mp3"


def test_claude_context_gives_title_caption_and_fixes(session_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(subtitles, "transcribe", fake_transcribe)
    seen = {}

    def fake_describe(cfg, channel, stream_title, transcript, chat, fixed_tags):
        seen.update(transcript=transcript, chat=chat, tags=fixed_tags)
        return {"title": "JLTomy perd son pari contre le chat", "caption": "Il y croyait trop 😭",
                "hashtags": ["pari", "irl"], "fixes": {"énorme": "ÉNORME"}}

    monkeypatch.setattr(pipeline.context, "describe", fake_describe)
    cfg = Config()
    cfg.paths.export = str(tmp_path / "out")
    cfg.vertical.preset = "ultrafast"
    cfg.detection.max_clips = 1
    clip = pipeline.process_session(cfg, session_dir)[0]
    assert seen["transcript"].startswith("Non mais c'est énorme")
    assert seen["chat"] and all(0 <= t for t, _u, _m in seen["chat"])
    assert "jltomy" in seen["tags"]
    caption = (clip / "caption.txt").read_text(encoding="utf-8").strip()
    assert caption == "Il y croyait trop 😭 #pari #irl #jltomy #twitchfr #fyp #pourtoi"
    ass = (clip / "subtitles.ass").read_text(encoding="utf-8")
    assert "JLTomy perd son pari contre le chat" in ass
    assert "ÉNORME !" in ass  # correction de Claude, ponctuation gardée
    assert ",Chat,," in ass  # messages du chat incrustés
    meta = json.loads((clip / "meta.json").read_text(encoding="utf-8"))
    assert meta["context"]["hashtags"] == ["pari", "irl"]


def test_probe_duration_of_fmp4_twitch_recording(tmp_path):
    # Comme Streamlink sur une chaîne Twitch en fMP4 : init + segments pris en cours de live
    # (horodatages qui ne commencent pas à 0). 3 segments de 2 s => 6 s de vidéo.
    hls = tmp_path / "hls"
    hls.mkdir()
    # Lancé depuis le dossier hls : sous Windows, ffmpeg n'écrit pas init.mp4 à côté de la
    # playlist quand on lui donne un chemin avec des « \ ».
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
        "-f", "lavfi", "-i", "sine", "-t", "20", "-c:v", "libx264", "-preset", "ultrafast",
        "-g", "60", "-c:a", "aac", "-f", "hls", "-hls_segment_type", "fmp4", "-hls_time", "2",
        "-hls_list_size", "0", "live.m3u8"], check=True, cwd=hls)
    rec = tmp_path / "stream.ts"
    rec.write_bytes(b"".join((hls / name).read_bytes()
                             for name in ["init.mp4", "live5.m4s", "live6.m4s", "live7.m4s"]))
    assert video.probe(rec)["duration"] == pytest.approx(6.0, abs=0.2)
