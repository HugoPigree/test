import json
import os
import subprocess
import time

import pytest

from autoclip import live, pipeline
from autoclip.config import Config, load_config
from autoclip.live import PTS_WRAP, VIDEO_MARGIN, _start_time, cut_segments, purge, ready_peaks

from conftest import BURSTS, START_TS, synthetic_chat


def frame_md5(path, at):
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
                           "-frames:v", "1", "-f", "md5", "-"],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("ts_offset", [0.0, PTS_WRAP - 15], ids=["normal", "horodatage-qui-reboucle"])
def test_cut_from_segments_lands_on_the_right_frame(tmp_path, ts_offset):
    # 30 s de vidéo en segments de 10 s, comme `autoclip live` (le 2e cas repasse par 0 à 15 s).
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=30",
        "-f", "lavfi", "-i", "sine=duration=30", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
        "-c:a", "aac", "-output_ts_offset", str(ts_offset), "-f", "segment", "-segment_time", "10",
        "-segment_format", "mpegts", str(tmp_path / "seg_%05d.ts")], check=True)
    files = sorted(tmp_path.glob("seg_*.ts"))
    pts0 = _start_time(files[0])
    segments = [((_start_time(f) - pts0) % PTS_WRAP, f) for f in files]
    # ~10 s chacun (ffmpeg coupe sur une image clé : parfois 11 s).
    starts = [s for s, _ in segments]
    assert starts[0] == 0 and all(9.5 < b - a < 11.5 for a, b in zip(starts, starts[1:]))

    reference = tmp_path / "reference.ts"
    reference.write_bytes(b"".join(f.read_bytes() for f in files))
    raw = tmp_path / "raw.ts"
    offset = cut_segments(segments, 13.0, 18.0, raw)
    assert offset == starts[1]
    # La seconde 14 du live se retrouve au bon endroit dans le fichier coupé.
    assert frame_md5(raw, 14.0 - offset) == frame_md5(reference, 14.0)
    # Un clip à cheval sur deux segments les prend tous les deux.
    assert cut_segments(segments, 8.0, 14.0, raw) == 0.0
    assert raw.stat().st_size == files[0].stat().st_size + files[1].stat().st_size
    assert cut_segments(segments, 40.0, 50.0, raw) == starts[2]  # segment en cours d'écriture
    assert cut_segments([], 0.0, 10.0, raw) is None


def test_purge_keeps_only_recent_segments(tmp_path):
    old_run, active = tmp_path / "ancienne", tmp_path / "active"
    old_run.mkdir()
    active.mkdir()
    for f in [old_run / "seg_00000.ts", active / "seg_00000.ts", active / "seg_00001.ts"]:
        f.write_bytes(b"x")
    hour_ago = time.time() - 3600
    for f in [old_run / "seg_00000.ts", active / "seg_00000.ts"]:
        os.utime(f, (hour_ago, hour_ago))
    purge(tmp_path, keep_seconds=45 * 60, active=active)
    assert not old_run.exists()
    assert [f.name for f in active.iterdir()] == ["seg_00001.ts"]


def test_each_chat_peak_is_clipped_once_after_post_roll():
    cfg = Config()
    msgs = synthetic_chat()  # 4 min de chat avec 3 moments de hype
    clipped, last = [], 0.0
    for now in range(int(START_TS) + 5, int(START_TS) + 400, 5):  # analyse toutes les 5 s
        seen = [m for m in msgs if m["ts"] <= now]
        for p in ready_peaks(cfg, seen, now, last):
            assert p.end + VIDEO_MARGIN <= now  # on a attendu la fin du clip
            last = p.t_peak
            clipped.append((now, p))
    assert len(clipped) == len(BURSTS)
    history_ok = msgs[0]["ts"] + cfg.detection.baseline_window  # 5 min de chat avant de détecter
    for (now, p), b in zip(clipped, BURSTS):
        assert p.start <= START_TS + b <= p.t_peak
        assert now - max(p.end + VIDEO_MARGIN, history_ok) <= 5  # et pas plus que nécessaire
        assert cfg.detection.min_duration <= p.duration <= cfg.detection.max_duration


def test_no_peak_right_after_launch():
    # Juste après le lancement, 1 min de chat ne suffit pas pour une ligne de base fiable.
    msgs = [m for m in synthetic_chat() if m["ts"] <= START_TS + 60]
    assert ready_peaks(Config(), msgs, START_TS + 60, 0.0) == []


def test_cooldown_blocks_a_second_clip_of_the_same_moment():
    cfg = Config()
    msgs = synthetic_chat(duration=420, bursts=[330, 375])  # deux réactions à 45 s d'écart
    now, last_clip = START_TS + 420, START_TS + 200  # un clip a déjà été fait à 200 s
    assert [round(p.t_peak - START_TS) for p in ready_peaks(cfg, msgs, now, last_clip)] == [338]
    cfg.live.cooldown = 20
    cfg.detection.min_gap = 20
    assert len(ready_peaks(cfg, msgs, now, last_clip)) == 2


def test_small_video_buffer_still_detects_peaks():
    # Bug réel : avec buffer_minutes = 5 (config de test), le chat n'était gardé que 5 min et
    # ready_peaks attend 5 min d'historique : aucun pic n'était jamais détecté.
    cfg = Config()
    cfg.live.buffer_minutes = 5
    keep = live.chat_history_seconds(cfg)
    assert keep > cfg.detection.baseline_window
    msgs = synthetic_chat(duration=420, bursts=[330])
    now = START_TS + 420
    old = [m for m in msgs if m["ts"] > now - cfg.live.buffer_minutes * 60]  # ancien comportement
    assert ready_peaks(cfg, old, now, 0.0) == []
    kept = [m for m in msgs if m["ts"] > now - keep]
    assert any(p.start <= START_TS + 330 <= p.t_peak for p in ready_peaks(cfg, kept, now, 0.0))


def test_channels_from_config(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[twitch]\nchannels = ["JLTomy", "#Etostark__"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.twitch.channels == ["jltomy", "etostark__"] and cfg.twitch.channel == "jltomy"
    path.write_text('[twitch]\nchannel = "gotaga"\n', encoding="utf-8")
    assert load_config(path).twitch.channels == ["gotaga"]  # ancienne config à une chaîne


def test_each_channel_keeps_its_own_config_and_hashtag():
    cfg = Config()
    eto = live.channel_config(cfg, "etostark__")
    assert eto.twitch.channel == "etostark__" and cfg.twitch.channel == "jltomy"
    assert eto.detection == cfg.detection  # mêmes règles
    eto.detection.threshold = 9
    assert cfg.detection.threshold != 9    # copie indépendante
    assert pipeline.fixed_hashtags(eto) == ["etostark", "twitchfr", "fyp", "pourtoi"]
    assert pipeline.make_caption(eto, "Il a dit oui", "titre").endswith("#etostark #twitchfr #fyp #pourtoi")


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_run_live_watches_every_channel_and_writes_a_heartbeat(monkeypatch, tmp_path):
    seen = []

    def fake_watch(cfg, status, stop):
        seen.append(cfg.twitch.channel)
        status[cfg.twitch.channel] = {"en_live": True}
        if cfg.twitch.channel == "etostark__":
            time.sleep(1.5)
            raise RuntimeError("panne simulée")  # une chaîne plante => tout s'arrête (code 1)
        stop.wait(10)

    monkeypatch.setattr(live, "_watch", fake_watch)
    cfg = Config()
    cfg.paths.candidates = str(tmp_path / "candidates")
    cfg.twitch.channels = ["jltomy", "etostark__"]
    beat = tmp_path / "heartbeat.json"
    assert live.run_live(cfg, heartbeat=beat) == 1
    assert sorted(seen) == ["etostark__", "jltomy"]
    data = json.loads(beat.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid() and "prochain_tri" in data
    assert set(data["chaines"]) <= {"jltomy", "etostark__"}
