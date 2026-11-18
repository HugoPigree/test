import json
import shutil
import subprocess

import pytest

from autoclip import pipeline, subtitles, top, video
from autoclip.config import Config


def clip(id, views, lang="fr", video_id="", offset=None, name="Etostark__"):
    return {"id": id, "url": f"https://clips.twitch.tv/{id}", "broadcaster_name": name,
            "creator_name": "clippeur42", "language": lang, "title": "il a pas fait ça",
            "view_count": views, "created_at": "2026-11-18T18:00:00Z", "video_id": video_id,
            "vod_offset": offset, "game_id": "509658"}


def test_pick_top_filters_language_dedupes_and_sorts():
    clips = [clip("a", 50), clip("b", 900, lang="en"), clip("c", 300), clip("a", 50),
             clip("d", 200, video_id="v1", offset=1000), clip("e", 250, video_id="v1", offset=1030),
             clip("f", 10, video_id="v1", offset=2000)]
    # « e » et « d » : même moment du même live (30 s d'écart), on garde le plus vu.
    assert [c["id"] for c in top.pick_top(clips, "fr", 3)] == ["c", "e", "a"]
    assert [c["id"] for c in top.pick_top(clips, "FR", 10)] == ["c", "e", "a", "f"]


def test_caption_credits_streamer_clipper_and_source():
    cap = top.top_caption(clip("x", 1), "Il a vraiment fait ça 😭", ["fail", "etostark"])
    assert cap == ("Il a vraiment fait ça 😭 Clip de Etostark__, créé par clippeur42. "
                   "Source : Twitch https://clips.twitch.tv/x #fail #etostark")


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self.data


def test_twitch_api_token_games_and_paginated_clips(monkeypatch):
    calls = []

    def post(url, data, timeout):
        assert data["grant_type"] == "client_credentials"
        return FakeResp({"access_token": "tok"})

    def get(url, headers, params, timeout):
        calls.append((url, params))
        assert headers == {"Client-Id": "id", "Authorization": "Bearer tok"}
        if url.endswith("/games"):
            return FakeResp({"data": [{"id": "509658", "name": "Just Chatting"}]})
        if "after" not in params:
            return FakeResp({"data": [clip("a", 5)], "pagination": {"cursor": "p2"}})
        return FakeResp({"data": [clip("b", 3)], "pagination": {}})

    monkeypatch.setattr(top.requests, "post", post)
    monkeypatch.setattr(top.requests, "get", get)
    api = top.TwitchAPI("id", "secret")
    assert api.game_ids(["Just Chatting", "Jeu Inconnu"]) == {"Just Chatting": "509658"}
    from datetime import datetime, timezone
    now = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)
    got = api.clips("509658", now.replace(hour=0), now, pages=5)
    assert [c["id"] for c in got] == ["a", "b"]
    first = calls[1][1]
    assert first["first"] == 100 and first["started_at"] == "2026-11-18T00:00:00Z"


def test_missing_credentials_say_where_to_put_them():
    with pytest.raises(RuntimeError, match="client_id"):
        top.TwitchAPI("", "")


def test_top_clip_end_to_end(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg requis")
    src = tmp_path / "dl" / "source.mp4"
    src.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=70",
                    "-f", "lavfi", "-i", "sine=duration=70", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    monkeypatch.setattr(top, "download", lambda url, dest: src)
    monkeypatch.setattr(subtitles, "transcribe",
                        lambda wav, cfg, title="": [subtitles.Word(0.5, 1.0, "Wesh"), subtitles.Word(1.0, 1.5, "frérot")])
    cfg = Config()
    cfg.paths.export = str(tmp_path / "ready_to_post")
    cfg.paths.recordings = str(tmp_path / "rec")
    cfg.vertical.preset = "ultrafast"
    c = clip("Abc", 4321)
    out = top.make_top_clip(cfg, c, 1, "2026-11-18", tmp_path / "work")
    assert out.name == "top_2026-11-18_01"
    info = video.probe(out / "video.mp4")
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(info["duration"] - 60.0) < 0.5  # clip entier, plafonné à 60 s, pas recoupé
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["views"] == 4321 and meta["source_url"] == c["url"] and meta["channel"] == "Etostark__"
    assert meta["tiktok"] is None and meta["clip_start"] == 0.0
    caption = (out / "caption.txt").read_text(encoding="utf-8")
    assert "Clip de Etostark__, créé par clippeur42" in caption and c["url"] in caption
    assert "#etostark" in caption
    ass = (out / "subtitles.ass").read_text(encoding="utf-8")
    hook = [l for l in ass.splitlines() if l.startswith("Dialogue:") and ",Hook," in l]
    assert hook and hook[0].split(",")[2] == "0:00:03.00"  # accroche les 3 premières secondes
    assert not src.exists()  # le téléchargement est supprimé une fois le clip prêt
    # Relancer le même jour ne refait rien et n'écrase pas un autre clip.
    assert top.make_top_clip(cfg, c, 1, "2026-11-18", tmp_path / "work") == out
    assert top.make_top_clip(cfg, clip("Autre", 1), 1, "2026-11-18", tmp_path / "work") == out
    assert json.loads((out / "meta.json").read_text(encoding="utf-8"))["twitch_clip"]["id"] == "Abc"
