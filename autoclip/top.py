"""`autoclip top` : le top des clips Twitch d'une langue sur les dernières 24 h, prêts à poster.

1. API Twitch officielle (Helix, jeton d'application) : les `[top] top_games` catégories les plus
   regardées du moment (Get Top Games) + celles de `[top] categories` (Get Games), puis Get Clips
   des dernières 24 h par catégorie ;
2. on garde la langue demandée, on déduplique, on trie par vues, on prend les N premiers ;
3. téléchargement avec yt-dlp, puis le pipeline habituel sur le clip entier (60 s max, pas
   recoupé) : 9:16, sous-titres Whisper, titre d'accroche les 3 premières secondes ;
4. ready_to_post/top_AAAA-MM-JJ_NN/ avec une légende qui cite le streamer, le clippeur et la
   source. Rien n'est envoyé sur TikTok.
"""

from __future__ import annotations

import copy
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from autoclip import pipeline, video
from autoclip.config import Config
from autoclip.peaks import Peak

log = logging.getLogger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"
MAX_DURATION = 60.0


class TwitchAPI:
    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            raise RuntimeError("Identifiants de l'appli Twitch manquants : [top] client_id / "
                               "client_secret dans config.toml (ou TWITCH_CLIENT_ID / "
                               "TWITCH_CLIENT_SECRET). Voir README, « Top des clips ».")
        resp = requests.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret,
                                              "grant_type": "client_credentials"}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Jeton Twitch refusé ({resp.status_code}) : {resp.text[:200]}")
        self.headers = {"Client-Id": client_id,
                        "Authorization": f"Bearer {resp.json()['access_token']}"}

    def get(self, path: str, params) -> dict:
        resp = requests.get(f"{HELIX}/{path}", headers=self.headers, params=params, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"API Twitch {path} : {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def game_ids(self, names: list[str]) -> dict[str, str]:
        """{nom de catégorie: id} ; les noms inconnus de Twitch sont signalés et ignorés."""
        data = self.get("games", [("name", n) for n in names]).get("data", [])
        found = {g["name"].lower(): g["id"] for g in data}
        out = {}
        for n in names:
            if n.lower() in found:
                out[n] = found[n.lower()]
            else:
                log.warning("Catégorie Twitch introuvable : %r (nom exact attendu)", n)
        return out

    def top_games(self, count: int) -> list[dict]:
        """Les `count` catégories les plus regardées en ce moment ([{"id", "name"}, …])."""
        out, cursor = [], None
        while len(out) < count:
            params = {"first": min(100, count - len(out))}
            if cursor:
                params["after"] = cursor
            page = self.get("games/top", params)
            out += page.get("data", [])
            cursor = (page.get("pagination") or {}).get("cursor")
            if not cursor or not page.get("data"):
                break
        return out[:count]

    def clips(self, game_id: str, since: datetime, until: datetime, pages: int) -> list[dict]:
        out, cursor = [], None
        for _ in range(pages):
            params = {"game_id": game_id, "first": 100,
                      "started_at": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "ended_at": until.strftime("%Y-%m-%dT%H:%M:%SZ")}
            if cursor:
                params["after"] = cursor
            page = self.get("clips", params)
            out += page.get("data", [])
            cursor = (page.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return out


def pick_top(clips: list[dict], lang: str, count: int) -> list[dict]:
    """Langue demandée, sans doublons, par vues décroissantes.

    Doublons : même id, ou même moment du même live clippé par plusieurs viewers (même VOD et
    décalages à moins de 60 s) ; on garde le plus vu.
    """
    keep: list[dict] = []
    for c in sorted(clips, key=lambda c: c.get("view_count", 0), reverse=True):
        if (c.get("language") or "").lower() != lang.lower():
            continue
        dup = any(c["id"] == k["id"] or (
            c.get("video_id") and c.get("video_id") == k.get("video_id")
            and c.get("vod_offset") is not None and k.get("vod_offset") is not None
            and abs(c["vod_offset"] - k["vod_offset"]) < 60) for k in keep)
        if not dup:
            keep.append(c)
    return keep[:count]


def download(url: str, dest_dir: Path) -> Path:
    """Télécharge le clip avec yt-dlp (meilleure qualité) ; renvoie le fichier vidéo."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "yt_dlp", "--no-progress", "--no-playlist", "-f", "best",
           "-o", str(dest_dir / "source.%(ext)s"), url]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    files = [f for f in dest_dir.glob("source.*") if f.suffix not in {".part", ".ytdl"}]
    if proc.returncode != 0 or not files:
        raise RuntimeError(f"yt-dlp a échoué pour {url} : {proc.stderr.strip()[-300:]}")
    return files[0]


def top_caption(clip: dict, text: str, hashtags: list[str]) -> str:
    tags = " ".join(f"#{h}" for h in hashtags)
    credit = (f"Clip de {clip['broadcaster_name']}, créé par {clip['creator_name']}. "
              f"Source : Twitch {clip['url']}")
    return " ".join(f"{text} {credit} {tags}".split())


def make_top_clip(cfg: Config, clip: dict, rank: int, day: str, work: Path) -> Path | None:
    name = f"top_{day}_{rank:02d}"
    out_dir = Path(cfg.paths.export) / name
    meta_path = out_dir / "meta.json"
    if meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        if old.get("twitch_clip", {}).get("id") == clip["id"]:
            log.info("Déjà prêt : %s", out_dir)
        else:
            log.warning("%s existe déjà avec un autre clip : non remplacé (supprime le dossier "
                        "pour le refaire).", out_dir)
        return out_dir
    src = download(clip["url"], work / clip["id"])
    meta = video.probe(src)
    dur = min(meta["duration"], MAX_DURATION)
    created = datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00")).timestamp()
    session = {"video_path": src, "start_ts": created, "channel": clip["broadcaster_name"].lower(),
               "metadata": {"title": clip.get("title", ""), "author": clip["broadcaster_name"]}}
    # Le clip entier, sans le recouper ; pas de pic de chat ici (pas de zoom ni de chat incrusté).
    peak = Peak(t_peak=0.0, start=0.0, end=round(dur, 2), score=0.0, activity=0.0, baseline=0.0,
                n_messages=0, sample=[clip.get("title", "")])
    ccfg = copy.deepcopy(cfg)
    ccfg.twitch.channel = session["channel"]
    ccfg.export.hook_duration = 3.0
    ccfg.vertical.peak_zoom = False
    pipeline.process_peak(ccfg, session, meta, peak, rank, name=name, messages=[])

    m = json.loads(meta_path.read_text(encoding="utf-8"))
    ctx = m.get("context")
    text = ctx["caption"] if ctx else (clip.get("title") or m["hook"])
    caption = top_caption(clip, text, (ctx or {}).get("hashtags", [])
                          + pipeline.fixed_hashtags(ccfg))
    (out_dir / "caption.txt").write_text(caption + "\n", encoding="utf-8")
    m.update({"caption": caption, "channel": clip["broadcaster_name"], "source_url": clip["url"],
              "views": clip.get("view_count", 0), "twitch_clip": clip})
    meta_path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    src.unlink(missing_ok=True)
    return out_dir


def run_top(cfg: Config, lang: str, count: int) -> list[Path]:
    api = TwitchAPI(cfg.top.client_id, cfg.top.client_secret)
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=24)
    # Catégories : les plus regardées du moment (`top_games`, 1 page chacune par défaut) + la liste
    # de la config (`pages` chacune). Une catégorie présente deux fois garde le plus de pages.
    games: dict[str, tuple[str, int]] = {}
    if cfg.top.top_games > 0:
        for g in api.top_games(cfg.top.top_games):
            games[g["id"]] = (g["name"], cfg.top.top_games_pages)
    for name, gid in api.game_ids(cfg.top.categories).items():
        games[gid] = (name, max(cfg.top.pages, games.get(gid, ("", 0))[1]))
    log.info("Recherche des clips des dernières 24 h dans %d catégories…", len(games))
    clips = []
    for gid, (name, pages) in games.items():
        found = api.clips(gid, since, until, pages)
        n_lang = sum((c.get("language") or "").lower() == lang.lower() for c in found)
        log.log(logging.INFO if n_lang else logging.DEBUG,
                "%s : %d clips sur 24 h, dont %d en « %s »", name, len(found), n_lang, lang)
        clips += found
    top = pick_top(clips, lang, count)
    if not top:
        log.warning("Aucun clip en « %s » sur les dernières 24 h.", lang)
        return []
    day = until.astimezone().strftime("%Y-%m-%d")
    work = Path(cfg.paths.recordings) / "top_downloads"
    out = []
    for rank, clip in enumerate(top, 1):
        log.info("#%d %s (%s vues) : %s", rank, clip["broadcaster_name"], clip.get("view_count"),
                 clip.get("title", ""))
        try:
            d = make_top_clip(cfg, clip, rank, day, work)
            if d:
                out.append(d)
        except Exception:  # noqa: BLE001 - un clip raté ne doit pas bloquer les autres
            log.exception("Clip #%d raté (%s)", rank, clip.get("url"))
    return out
