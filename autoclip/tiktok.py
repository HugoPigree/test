"""Upload TikTok via l'API officielle Content Posting.

Deux modes :
- "inbox"  : la vidéo arrive en brouillon dans l'appli TikTok (notification), tu finalises
             la légende/son et tu postes depuis le téléphone. Scope `video.upload`.
- "direct" : publication directe. Scope `video.publish`. Tant que l'appli développeur
             n'est pas auditée par TikTok, seul `SELF_ONLY` (privé) est autorisé.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from autoclip.config import TikTokConfig

log = logging.getLogger(__name__)

API = "https://open.tiktokapis.com"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024


class TikTokError(RuntimeError):
    pass


# --------------------------------------------------------------------------- OAuth

def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    # TikTok (Login Kit Desktop) attend le SHA256 encodé en hexadécimal.
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    return verifier, challenge


def _scopes(cfg: TikTokConfig) -> str:
    return "user.info.basic,video.upload" + (",video.publish" if cfg.mode == "direct" else "")


def authorize(cfg: TikTokConfig) -> dict:
    if not cfg.client_key or not cfg.client_secret:
        raise TikTokError("client_key / client_secret manquants (config.toml ou TIKTOK_CLIENT_KEY/SECRET).")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    params = {
        "client_key": cfg.client_key, "response_type": "code", "scope": _scopes(cfg),
        "redirect_uri": cfg.redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    redirect = urllib.parse.urlparse(cfg.redirect_uri)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in qs.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>autoclip : connexion TikTok OK, tu peux fermer cet onglet.</h2>".encode())

        def log_message(self, *args):
            pass

    server = None
    if redirect.hostname in ("localhost", "127.0.0.1"):
        server = http.server.HTTPServer((redirect.hostname, redirect.port or 80), Handler)
        threading.Thread(target=server.handle_request, daemon=True).start()

    print("\nOuvre cette URL pour autoriser l'appli (elle devrait s'ouvrir toute seule) :\n" + url + "\n")
    webbrowser.open(url)

    if server:
        deadline = time.time() + 300
        while not result and time.time() < deadline:
            time.sleep(0.5)
        server.server_close()
    if not result:
        pasted = input("Colle ici l'URL complète de redirection après autorisation : ").strip()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        result = {k: v[0] for k, v in qs.items()}

    if result.get("error"):
        raise TikTokError(f"Autorisation refusée : {result.get('error')} {result.get('error_description', '')}")
    if result.get("state") != state:
        raise TikTokError("Paramètre state invalide (réessaie).")

    token = _token_request(cfg, {
        "grant_type": "authorization_code", "code": result["code"],
        "redirect_uri": cfg.redirect_uri, "code_verifier": verifier,
    })
    save_token(cfg, token)
    log.info("Token TikTok enregistré dans %s", cfg.token_file)
    return token


def _token_request(cfg: TikTokConfig, data: dict) -> dict:
    payload = {"client_key": cfg.client_key, "client_secret": cfg.client_secret, **data}
    r = requests.post(f"{API}/v2/oauth/token/", data=payload, timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    body = r.json()
    if r.status_code != 200 or "access_token" not in body:
        raise TikTokError(f"Échec token TikTok : {body}")
    body["obtained_at"] = time.time()
    return body


def save_token(cfg: TikTokConfig, token: dict) -> None:
    Path(cfg.token_file).write_text(json.dumps(token, indent=2), encoding="utf-8")


def get_access_token(cfg: TikTokConfig) -> str:
    path = Path(cfg.token_file)
    if not path.exists():
        raise TikTokError("Pas de token TikTok : lance d'abord `autoclip tiktok-auth`.")
    token = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - token.get("obtained_at", 0)
    if age > token.get("expires_in", 86400) - 300:
        log.info("Rafraîchissement du token TikTok…")
        token = _token_request(cfg, {"grant_type": "refresh_token",
                                     "refresh_token": token["refresh_token"]})
        save_token(cfg, token)
    return token["access_token"]


# --------------------------------------------------------------------------- Upload

def chunk_plan(size: int, chunk_size: int = DEFAULT_CHUNK) -> tuple[int, int]:
    """Renvoie (chunk_size, total_chunk_count) selon les règles TikTok.

    < 5 Mo ou <= 64 Mo : un seul morceau ; sinon morceaux de `chunk_size`, le dernier
    absorbant le reste (il peut aller jusqu'à 128 Mo).
    """
    if size <= 0:
        raise TikTokError("Fichier vide.")
    if size <= MAX_CHUNK:
        return size, 1
    chunk_size = min(max(chunk_size, MIN_CHUNK), MAX_CHUNK)
    return chunk_size, size // chunk_size


def _api(token: str, path: str, body: dict) -> dict:
    r = requests.post(f"{API}{path}", json=body, timeout=60, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    try:
        data = r.json()
    except ValueError:
        raise TikTokError(f"{path} : réponse invalide HTTP {r.status_code}")
    err = data.get("error", {})
    if r.status_code != 200 or err.get("code") not in (None, "ok"):
        raise TikTokError(f"{path} : {err.get('code')} {err.get('message')} (HTTP {r.status_code})")
    return data.get("data", {})


def _put_chunks(upload_url: str, video: Path, size: int, chunk_size: int, total: int) -> None:
    with video.open("rb") as fh:
        for idx in range(total):
            first = idx * chunk_size
            last = size - 1 if idx == total - 1 else first + chunk_size - 1
            fh.seek(first)
            data = fh.read(last - first + 1)
            for attempt in range(4):
                try:
                    r = requests.put(upload_url, data=data, timeout=300, headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {first}-{last}/{size}",
                    })
                    if r.status_code in (200, 201, 206):
                        break
                    raise TikTokError(f"PUT chunk {idx + 1}/{total} : HTTP {r.status_code} {r.text[:200]}")
                except (requests.RequestException, TikTokError) as exc:
                    if attempt == 3:
                        raise
                    log.warning("Upload chunk %d échoué (%s), nouvel essai…", idx + 1, exc)
                    time.sleep(2 ** attempt)
            log.info("  chunk %d/%d envoyé", idx + 1, total)


def upload_video(cfg: TikTokConfig, video: Path, title: str) -> dict:
    token = get_access_token(cfg)
    size = video.stat().st_size
    chunk_size, total = chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size,
              "chunk_size": chunk_size, "total_chunk_count": total}

    if cfg.mode == "inbox":
        init = _api(token, "/v2/post/publish/inbox/video/init/", {"source_info": source})
    elif cfg.mode == "direct":
        creator = _api(token, "/v2/post/publish/creator_info/query/", {})
        allowed = creator.get("privacy_level_options", [])
        privacy = cfg.privacy_level if cfg.privacy_level in allowed else "SELF_ONLY"
        if privacy != cfg.privacy_level:
            log.warning("privacy_level %s non autorisé (%s) → SELF_ONLY", cfg.privacy_level, allowed)
        post_info = {
            "title": title[:2200], "privacy_level": privacy,
            "disable_comment": cfg.disable_comment, "disable_duet": cfg.disable_duet,
            "disable_stitch": cfg.disable_stitch, "video_cover_timestamp_ms": 1000,
        }
        init = _api(token, "/v2/post/publish/video/init/", {"post_info": post_info, "source_info": source})
    else:
        raise TikTokError(f"mode TikTok inconnu : {cfg.mode!r} (inbox ou direct)")

    publish_id, upload_url = init["publish_id"], init["upload_url"]
    log.info("Upload TikTok %s (%.1f Mo, %d morceau(x))…", video.name, size / 1e6, total)
    _put_chunks(upload_url, video, size, chunk_size, total)

    status = {}
    for _ in range(60):
        status = _api(token, "/v2/post/publish/status/fetch/", {"publish_id": publish_id})
        state = status.get("status")
        if state in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            break
        if state == "FAILED":
            raise TikTokError(f"TikTok a refusé la vidéo : {status.get('fail_reason')}")
        time.sleep(5)
    return {"publish_id": publish_id, "status": status.get("status"), "mode": cfg.mode}
