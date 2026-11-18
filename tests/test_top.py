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


def test_top_games_paginates_up_to_count(monkeypatch):
    pages = {None: ([{"id": str(i), "name": f"jeu{i}"} for i in range(100)], "p2"),
             "p2": ([{"id": str(i), "name": f"jeu{i}"} for i in range(100, 200)], "p3")}
    seen = []

    def get(url, headers, params, timeout):
        seen.append(params)
        data, cursor = pages[params.get("after")]
        return FakeResp({"data": data[:params["first"]], "pagination": {"cursor": cursor}})

    monkeypatch.setattr(top.requests, "post", lambda url, data, timeout: FakeResp({"access_token": "t"}))
    monkeypatch.setattr(top.requests, "get", get)
    games = top.TwitchAPI("id", "s").top_games(150)
    assert len(games) == 150 and games[-1]["id"] == "149"
    assert [p["first"] for p in seen] == [100, 50]


def test_run_top_searches_top_games_plus_config_categories(monkeypatch, tmp_path):
    asked = {}

    class FakeAPI:
        def __init__(self, cid, secret):
            pass

        def top_games(self, n):
            asked["top_n"] = n
            return [{"id": "gta6", "name": "Grand Theft Auto VI"}, {"id": "509658", "name": "Just Chatting"}]

        def game_ids(self, names):
            return {"Just Chatting": "509658", "VALORANT": "516575"}

        def clips(self, gid, since, until, pages):
            asked[gid] = pages
            return {"gta6": [clip("big", 6398)], "509658": [clip("jc", 300)],
                    "516575": [clip("valo", 1431), clip("en", 9000, lang="en")]}[gid]

    made = []
    monkeypatch.setattr(top, "TwitchAPI", FakeAPI)
    monkeypatch.setattr(top, "make_top_clip",
                        lambda cfg, c, rank, day, work: made.append((rank, c["id"])) or tmp_path / c["id"])
    cfg = Config()
    out = top.run_top(cfg, "fr", 2)
    assert made == [(1, "big"), (2, "valo")]  # GTA VI n'est pas dans la liste : trouvé via le top
    assert len(out) == 2 and asked["top_n"] == 100
    # Top : 1 page ; liste de la config : 5 pages (y compris Just Chatting, présent dans les deux).
    assert (asked["gta6"], asked["509658"], asked["516575"]) == (1, 5, 5)
    cfg.top.top_games = 0
    made.clear()
    top.run_top(cfg, "fr", 2)
    assert made == [(1, "valo"), (2, "jc")]  # sans le top : seulement la liste


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
