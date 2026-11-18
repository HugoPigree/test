import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta

import pytest

from autoclip import daily, subtitles, video
from autoclip.config import Config
from autoclip.live import PTS_WRAP, _start_time
from autoclip.peaks import Peak

from test_live import frame_md5


def make_ts(path, seconds=40, offset=0.0):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x180:rate=30:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
                    "-c:a", "aac", "-output_ts_offset", str(offset), "-f", "mpegts", str(path)], check=True)


def cfg_in(tmp_path):
    cfg = Config()
    cfg.paths.candidates = str(tmp_path / "candidates")
    cfg.paths.export = str(tmp_path / "ready_to_post")
    cfg.vertical.preset = "ultrafast"
    cfg.context.enabled = False
    cfg.music.enabled = False
    return cfg


def candidate(cfg, name, channel, t_peak_abs, score, created=None, status="candidate", raw_bytes=0):
    d = daily.candidates_dir(cfg)
    if raw_bytes:
        (d / f"{name}.ts").write_bytes(b"x" * raw_bytes)
    c = {"_json": d / f"{name}.json", "name": name, "channel": channel, "raw": f"{name}.ts",
         "created": created if created is not None else time.time(), "t_peak_abs": t_peak_abs,
         "start_ts": t_peak_abs - 30, "peak": {}, "score": score, "duration": 36.0,
         "reason": "Le chat s'emballe", "metadata": {}, "chat": [], "status": status}
    daily.save(c)
    return c


@pytest.mark.parametrize("ts_offset", [10.0, PTS_WRAP - 5], ids=["normal", "horodatage-qui-reboucle"])
def test_trim_copy_without_reencoding_keeps_the_right_frames(tmp_path, ts_offset):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg requis")
    src, out = tmp_path / "src.ts", tmp_path / "out.ts"
    make_ts(src, 40, ts_offset)
    offset = daily.trim_copy(src, 13.4, 10.0, out)
    # Pas de réencodage : ffmpeg part de l'image clé précédente (GOP de 2 s).
    assert 11.0 <= offset <= 13.4
    assert 10.0 <= video.probe(out)["duration"] <= 13.0
    # La seconde 17 de la source est bien à 17 - offset dans la coupe.
    assert frame_md5(out, 17.0 - offset) == frame_md5(src, 17.0)


def test_pick_best_scores_with_a_gap_per_channel(tmp_path):
    cfg = cfg_in(tmp_path)
    t = 1_800_000_000.0
    cands = [candidate(cfg, "a", "jltomy", t, 9.0), candidate(cfg, "b", "jltomy", t + 600, 8.0),
             candidate(cfg, "c", "etostark__", t + 60, 7.0), candidate(cfg, "d", "jltomy", t + 1500, 6.0),
             candidate(cfg, "e", "jltomy", t + 3000, 2.0)]
    # « b » est à 10 min de « a » sur la même chaîne : écarté ; « c » est sur une autre chaîne : gardé.
    assert [c["name"] for c in daily.pick(cands, 3, 20 * 60)] == ["a", "c", "d"]
    assert [c["name"] for c in daily.pick(cands, 10, 20 * 60)] == ["a", "c", "d", "e"]


def test_duplicates_are_found_on_disk_after_a_restart(tmp_path):
    cfg = cfg_in(tmp_path)
    candidate(cfg, "a", "jltomy", 1000.0, 5.0)
    assert daily.is_duplicate(cfg, "jltomy", 1000.0 + cfg.live.cooldown - 1)
    assert not daily.is_duplicate(cfg, "jltomy", 1000.0 + cfg.live.cooldown + 1)
    assert not daily.is_duplicate(cfg, "etostark__", 1000.0)


def test_selection_time_and_catch_up(tmp_path):
    cfg = cfg_in(tmp_path)
    cfg.daily.selection_time = "23:00"
    day = datetime(2026, 11, 18)
    assert not daily.selection_due(cfg, day.replace(hour=22, minute=59))
    assert daily.selection_due(cfg, day.replace(hour=23, minute=0))
    assert daily.next_selection(cfg, day.replace(hour=10)) == day.replace(hour=23)
    # Déjà fait aujourd'hui : prochain tri demain.
    (daily.candidates_dir(cfg) / daily.STATE).write_text('{"last_selection": "2026-11-18"}')
    assert not daily.selection_due(cfg, day.replace(hour=23, minute=30))
    assert daily.next_selection(cfg, day.replace(hour=23, minute=30)) == day.replace(day=19, hour=23)


def test_cleanup_retention_disk_limit_and_leftovers(tmp_path):
    cfg = cfg_in(tmp_path)
    now = datetime(2026, 11, 18, 12, 0)
    old = (now - timedelta(hours=49)).timestamp()
    candidate(cfg, "vieille", "jltomy", 1.0, 9.0, created=old, status="rejected", raw_bytes=10)
    candidate(cfg, "bonne", "jltomy", 2.0, 9.0, raw_bytes=4000)
    candidate(cfg, "rejetee", "jltomy", 3.0, 8.0, status="rejected", raw_bytes=4000)
    candidate(cfg, "faible", "jltomy", 4.0, 1.0, raw_bytes=4000)
    d = daily.candidates_dir(cfg)
    orphan = d / "reste_segments.ts"
    orphan.write_bytes(b"x")
    os.utime(orphan, (old, old))
    cfg.daily.max_disk_gb = 9000 / 1e9  # ~9 ko : il faut en supprimer une
    daily.cleanup(cfg, now)
    names = sorted(f.stem for f in d.glob("*.json"))
    # Plus de 48 h : supprimée ; puis la non retenue part avant la candidate au plus petit score.
    assert names == ["bonne", "faible"]
    assert not orphan.exists() and (d / "bonne.ts").exists()


def test_daily_selection_renders_only_the_best_and_ranks_them(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg requis")
    # Whisper (simulé) : 8 mots juste après le début du clip (l'audio transcrit commence 2 s avant).
    words = [subtitles.Word(2.3 + i * 0.4, 2.6 + i * 0.4, w) for i, w in
             enumerate(["Wesh", "frérot", "regarde", "ça", "il", "a", "pas", "fait."])]
    monkeypatch.setattr(subtitles, "transcribe", lambda wav, cfg, title="": words)
    cfg = cfg_in(tmp_path)
    cfg.daily.max_clips_per_day = 2
    d = daily.candidates_dir(cfg)
    t0 = datetime(2026, 11, 18, 15, 0).timestamp()
    for i, (ch, score, minute) in enumerate([("jltomy", 7.5, 0), ("jltomy", 9.1, 5),
                                             ("etostark__", 4.2, 30), ("jltomy", 3.0, 60)]):
        name = f"c{i}"
        make_ts(d / f"{name}.ts", 30)
        c = candidate(cfg, name, ch, t0 + minute * 60, score)
        c["peak"] = Peak(t_peak=20.0, start=2.0, end=28.0, score=score, activity=40, baseline=8,
                         n_messages=50, sample=["KEKW"]).to_dict()
        c["chat"] = [{"ts": c["start_ts"] + 22, "user": "viewer", "msg": "KEKW"}]
        c["duration"] = 26.0
        daily.save(c)
    now = datetime(2026, 11, 18, 23, 0)
    out = daily.run_selection(cfg, now)
    assert out.name == "2026-11-18"
    clips = sorted(p.name for p in out.iterdir() if p.is_dir())
    # 9,1 d'abord ; 7,5 est à 5 min sur la même chaîne (< 20 min) : c'est etostark qui passe.
    assert clips == ["01_jltomy_15h05", "02_etostark___15h30"]
    for c in clips:
        info = video.probe(out / c / "video.mp4")
        assert (info["width"], info["height"]) == (1080, 1920)
    ranking = (out / "classement.md").read_text(encoding="utf-8")
    assert "4 candidate(s) repérée(s), 2 retenue(s)" in ranking
    lines = [l for l in ranking.splitlines() if l.startswith("| 1 ") or l.startswith("| 2 ")]
    assert "/100 |" in lines[0] and "26 s" in lines[0] and "Le chat s'emballe" in lines[0]
    assert "chat " in lines[0] and "parole " in lines[0]  # détail du score
    # Les non retenues sont expliquées (c0 : 5 min avant le n°1 sur la même chaîne).
    assert "## Non retenues" in ranking and "à 5 min d'un clip retenu mieux classé (15:05)" in ranking
    meta = json.loads((out / clips[0] / "meta.json").read_text(encoding="utf-8"))
    assert meta["rang"] == 1 and 0 <= meta["qualite"]["total"] <= 100
    assert "voix_db" in meta["mesures"] and meta["mesures"]["mots"] == 8
    assert meta["mesures"]["debut_phrase"] is True and "pause" not in meta["qualite"]["penalites"]
    status = {c["name"]: c["status"] for c in daily.load_candidates(cfg)}
    assert status == {"c0": "rejected", "c1": "selected", "c2": "selected", "c3": "rejected"}
    assert not (d / "c1.ts").exists() and (d / "c0.ts").exists()  # brut des retenues supprimé
    assert not daily.selection_due(cfg, now)  # pas deux tris le même jour


def test_ties_at_zero_are_broken_by_the_score_before_penalties(tmp_path):
    cfg = cfg_in(tmp_path)
    a = candidate(cfg, "a", "jltomy", 1000.0, 3.0)
    b = candidate(cfg, "b", "jltomy", 9000.0, 3.0)
    a["qualite"] = {"total": 0, "base": 12.0}
    b["qualite"] = {"total": 0, "base": 20.0}
    assert [c["name"] for c in daily.pick([a, b], 1, 60)] == ["b"]


def test_reason_is_one_readable_line():
    p = Peak(t_peak=1, start=0, end=2, score=6.4, activity=58, baseline=10, n_messages=80,
             sample=["KEKW", "MDR", "il a pas fait ça"])
    r = daily.reason(p)
    assert "\n" not in r and "58" in r and "10" in r and "6.4" in r and "« KEKW »" in r


def test_heartbeat_is_written_atomically(tmp_path):
    beat = tmp_path / "heartbeat.json"
    daily.write_heartbeat(beat, {"jltomy": {"en_live": True}}, datetime(2026, 11, 18, 23, 0))
    data = json.loads(beat.read_text(encoding="utf-8"))
    assert data["prochain_tri"] == "2026-11-18T23:00" and data["chaines"]["jltomy"]["en_live"]
    assert not list(tmp_path.glob("*.tmp"))
