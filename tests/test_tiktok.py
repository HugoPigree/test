import json
import time

import pytest

from autoclip import tiktok
from autoclip.config import TikTokConfig

MB = 1024 * 1024


def test_chunk_plan():
    assert tiktok.chunk_plan(3 * MB) == (3 * MB, 1)
    assert tiktok.chunk_plan(64 * MB) == (64 * MB, 1)
    size, n = tiktok.chunk_plan(105 * MB)
    assert size == 10 * MB and n == 10  # le dernier morceau absorbe le reste (15 Mo)


class FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._body


@pytest.mark.parametrize("mode", ["inbox", "direct"])
def test_upload_flow(tmp_path, monkeypatch, mode):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x" * 1000)
    token = tmp_path / "tok.json"
    token.write_text(json.dumps({"access_token": "AT", "refresh_token": "RT",
                                 "expires_in": 86400, "obtained_at": time.time()}))
    cfg = TikTokConfig(mode=mode, token_file=str(token), client_key="k", client_secret="s")
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append((url, json))
        ok = {"error": {"code": "ok"}}
        if url.endswith("/creator_info/query/"):
            return FakeResp({**ok, "data": {"privacy_level_options": ["SELF_ONLY"]}})
        if url.endswith("/init/"):
            return FakeResp({**ok, "data": {"publish_id": "P1", "upload_url": "https://up/1"}})
        if url.endswith("/status/fetch/"):
            done = "SEND_TO_USER_INBOX" if mode == "inbox" else "PUBLISH_COMPLETE"
            return FakeResp({**ok, "data": {"status": done}})
        raise AssertionError(url)

    puts = []
    monkeypatch.setattr(tiktok.requests, "post", fake_post)
    monkeypatch.setattr(tiktok.requests, "put",
                        lambda url, data=None, headers=None, **kw: puts.append(headers) or FakeResp({}, 201))

    res = tiktok.upload_video(cfg, video, "MDR #jltomy")
    assert res["publish_id"] == "P1"
    assert puts[0]["Content-Range"] == "bytes 0-999/1000"
    init = next(body for url, body in calls if url.endswith("/init/"))
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": 1000,
                                   "chunk_size": 1000, "total_chunk_count": 1}
    if mode == "direct":
        assert init["post_info"]["privacy_level"] == "SELF_ONLY"
        assert init["post_info"]["title"] == "MDR #jltomy"
        assert "/inbox/" not in [u for u, _ in calls if u.endswith("/init/")][0]


def test_missing_token(tmp_path):
    with pytest.raises(tiktok.TikTokError):
        tiktok.get_access_token(TikTokConfig(token_file=str(tmp_path / "none.json")))
