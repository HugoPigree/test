import json
from datetime import datetime, timedelta, timezone

import pytest

from autoclip import radar
from autoclip.config import Config

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self.data


def e(source, title, views=None, hours=2.0, votes=None, **kw):
    return radar.entry(source, title, "auteur", views, kw.pop("duration", 30.0), kw.pop("url", f"https://x/{title}"),
                       NOW - timedelta(hours=hours), votes=votes, **kw)


def test_trend_is_views_or_votes_per_hour():
    assert radar.trend(e("Twitch", "a", views=1000, hours=4), NOW) == 250
    assert radar.trend(e("Reddit", "b", votes=300, hours=3), NOW) == 100
    assert radar.trend(e("Reddit", "c"), NOW) is None
    assert radar.trend(e("YouTube", "d", views=100, hours=0.01), NOW) == 200  # plancher 30 min


def test_duplicates_are_grouped_and_counted():
    a = e("Twitch", "Il saute du pont en moto", views=5000, video_id="v1", vod_offset=1000)
    b = e("Twitch", "PONT MOTO", views=900, video_id="v1", vod_offset=1030)        # même moment
    c = e("YouTube", "il saute du pont en moto GTA 6 !!", views=20000, video="yt1")  # titre proche
    d = e("Reddit", "Wow", votes=50, link="https://youtu.be/yt1")                    # renvoie vers c
    f = e("Reddit", "Leak : la carte complète", votes=4000, hours=1)
    top = radar.top_global([a, b, c, d, f], NOW)
    sizes = {best["title"]: len(g) for _, g, best in top}
    assert sizes == {"il saute du pont en moto GTA 6 !!": 4, "Leak : la carte complète": 1}
    line = radar.why(top[0][1], top[0][2])
    assert "Repris 4 fois (Reddit, Twitch, YouTube)" in line and "Scène : inconnu" in line


def test_why_stays_factual():
    g = [e("Reddit", "", duration=None, flair="Leak")]
    assert radar.why(g, g[0]) == "Accroche : inconnu · Scène : inconnu (flair « Leak ») · Durée : inconnu"


def test_youtube_search_respects_the_daily_limit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []

    def get(url, params, timeout):
        calls.append(url)
        if url.endswith("/search"):
            assert params["videoDuration"] == "short" and params["order"] == "viewCount"
            return Resp({"items": [{"id": {"videoId": "A"}}, {"id": {"videoId": "B"}}]})
        return Resp({"items": [
            {"id": "A", "snippet": {"title": "t1", "channelTitle": "c1", "publishedAt": "2026-11-19T10:00:00Z"},
             "statistics": {"viewCount": "10"}, "contentDetails": {"duration": "PT45S"}},
            {"id": "B", "snippet": {"title": "t2", "channelTitle": "c2", "publishedAt": "2026-11-19T11:00:00Z"},
             "statistics": {"viewCount": "500"}, "contentDetails": {"duration": "PT1M5S"}}]})

    monkeypatch.setattr(radar.requests, "get", get)
    cfg = Config()
    cfg.radar.youtube_api_key, cfg.radar.youtube_max_searches_per_day = "cle", 1
    got = radar.youtube(cfg, NOW - timedelta(hours=24), "2026-11-19")
    assert [(x["title"], x["views"], x["duration"]) for x in got] == [("t2", 500, 65), ("t1", 10, 45)]
    with pytest.raises(RuntimeError, match="limite de 1"):
        radar.youtube(cfg, NOW, "2026-11-19")
    assert len(calls) == 2  # la 2e recherche n'a pas été envoyée
    cfg.radar.youtube_api_key = ""
    with pytest.raises(RuntimeError, match="pas de clé"):
        radar.youtube(cfg, NOW, "2026-11-20")


def test_reddit_public_json(monkeypatch):
    def get(url, headers, params, timeout):
        assert url == "https://www.reddit.com/r/GTA6/top.json" and params == {"t": "day", "limit": 20}
        assert headers["User-Agent"] == "autoclip-radar/0.1"
        return Resp({"data": {"children": [{"data": {
            "title": "Trailer 3 ?", "author": "u1", "score": 1200, "view_count": None,
            "permalink": "/r/GTA6/comments/x/trailer/", "created_utc": NOW.timestamp() - 7200,
            "is_video": True, "media": {"reddit_video": {"duration": 31}}, "link_flair_text": "News"}}]}})

    monkeypatch.setattr(radar.requests, "get", get)
    [p] = radar.reddit(Config())
    assert (p["votes"], p["views"], p["duration"], p["flair"]) == (1200, None, 31, "News")
    assert p["url"] == "https://www.reddit.com/r/GTA6/comments/x/trailer/"


def test_radar_md_lists_every_source_and_unknowns(tmp_path):
    sections = {"Twitch": ([e("Twitch", "clip 1", views=100)], None),
                "YouTube": ([], "pas de clé YouTube"),
                "Reddit": ([e("Reddit", "post", votes=10, duration=None)], None)}
    path = tmp_path / "radar.md"
    radar.write(path, NOW, sections)
    text = path.read_text(encoding="utf-8")
    assert "## Top 10 global" in text and "Indisponible : pas de clé YouTube" in text
    assert "| 1 | Reddit | post | auteur | inconnu | inconnu | https://x/post | 10 |" in text
    assert "| Rang | Tendance (/h) | Source |" in text
