"""`autoclip top --radar` : radar GTA 6 des dernières 24 h, dans ready_to_post/AAAA-MM-JJ/radar.md.

- Twitch (API Helix, identifiants de [top]) : clips de la catégorie `[radar] twitch_game`,
  toutes langues, triés par vues ;
- YouTube (Data API v3, `[radar] youtube_api_key`) : recherche `youtube_query`, vidéos courtes
  (< 4 min) publiées dans les 24 h, triées par vues ; au plus `youtube_max_searches_per_day`
  recherches par jour (une recherche coûte 100 unités sur 10 000 gratuites) ;
- Reddit : top du jour de r/`reddit_subreddit` (OAuth si `reddit_client_id` est rempli, sinon
  l'adresse .json publique).
Top 10 global par score de tendance (vues ou votes / heures depuis la publication), doublons
regroupés. Rien n'est téléchargé ni reposté. Une info absente s'écrit « inconnu ».
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from autoclip.config import Config

log = logging.getLogger(__name__)

YT = "https://www.googleapis.com/youtube/v3"
QUOTA_FILE = Path("radar_quota.json")
STOP = {"gta", "gta6", "gtavi", "vi", "six", "grand", "theft", "auto", "the", "and", "for", "you",
        "les", "des", "une", "pour", "avec", "dans", "est", "pas", "que", "qui", "this", "that",
        "with", "trailer", "new", "clip", "game"}


def _dt(s: str | float | None) -> datetime | None:
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s, timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def entry(source, title, author, views, duration, url, published, **extra) -> dict:
    return {"source": source, "title": title or "", "author": author or None, "views": views,
            "duration": duration, "url": url, "published": published, **extra}


# --- Twitch ---------------------------------------------------------------------------------

def twitch(cfg: Config, since: datetime, until: datetime) -> list[dict]:
    from autoclip.top import TwitchAPI

    api = TwitchAPI(cfg.top.client_id, cfg.top.client_secret)
    gid = api.game_ids([cfg.radar.twitch_game]).get(cfg.radar.twitch_game)
    if not gid:
        raise RuntimeError(f"catégorie Twitch introuvable : {cfg.radar.twitch_game}")
    clips = sorted(api.clips(gid, since, until, cfg.radar.twitch_pages),
                   key=lambda c: c.get("view_count", 0), reverse=True)[:cfg.radar.count]
    return [entry("Twitch", c.get("title"), f"{c['broadcaster_name']} (clip de {c.get('creator_name') or 'inconnu'})",
                  c.get("view_count"), c.get("duration"), c["url"], _dt(c.get("created_at")),
                  language=c.get("language"), video_id=c.get("video_id"), vod_offset=c.get("vod_offset"))
            for c in clips]


# --- YouTube --------------------------------------------------------------------------------

def _iso_duration(s: str | None) -> float | None:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def _take_search(cfg: Config, today: str) -> bool:
    """Compte une recherche YouTube ; False si la limite du jour est atteinte."""
    try:
        q = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        q = {}
    if q.get("date") != today:
        q = {"date": today, "searches": 0}
    if q["searches"] >= cfg.radar.youtube_max_searches_per_day:
        return False
    q["searches"] += 1
    QUOTA_FILE.write_text(json.dumps(q), encoding="utf-8")
    return True


def youtube(cfg: Config, since: datetime, today: str) -> list[dict]:
    key = cfg.radar.youtube_api_key
    if not key:
        raise RuntimeError("pas de clé YouTube ([radar] youtube_api_key dans config.toml)")
    if not _take_search(cfg, today):
        raise RuntimeError(f"limite de {cfg.radar.youtube_max_searches_per_day} recherches "
                           f"YouTube atteinte aujourd'hui (quota gratuit)")
    r = requests.get(f"{YT}/search", timeout=30, params={
        "part": "snippet", "q": cfg.radar.youtube_query, "type": "video", "videoDuration": "short",
        "order": "viewCount", "publishedAfter": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "maxResults": min(50, cfg.radar.count), "key": key})
    if r.status_code != 200:
        raise RuntimeError(f"YouTube search : {r.status_code} {r.text[:200]}")
    ids = [i["id"]["videoId"] for i in r.json().get("items", []) if i.get("id", {}).get("videoId")]
    if not ids:
        return []
    r = requests.get(f"{YT}/videos", timeout=30, params={
        "part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": key})
    if r.status_code != 200:
        raise RuntimeError(f"YouTube videos : {r.status_code} {r.text[:200]}")
    out = []
    for v in r.json().get("items", []):
        sn, st = v.get("snippet", {}), v.get("statistics", {})
        out.append(entry("YouTube", sn.get("title"), sn.get("channelTitle"),
                         int(st["viewCount"]) if "viewCount" in st else None,
                         _iso_duration(v.get("contentDetails", {}).get("duration")),
                         f"https://www.youtube.com/watch?v={v['id']}", _dt(sn.get("publishedAt")),
                         video=v["id"]))
    return sorted(out, key=lambda e: e["views"] or 0, reverse=True)[:cfg.radar.count]


# --- Reddit ---------------------------------------------------------------------------------

def reddit(cfg: Config) -> list[dict]:
    rc = cfg.radar
    headers = {"User-Agent": rc.reddit_user_agent}
    params = {"t": "day", "limit": rc.count}
    if rc.reddit_client_id and rc.reddit_client_secret:
        tok = requests.post("https://www.reddit.com/api/v1/access_token", timeout=30, headers=headers,
                            auth=(rc.reddit_client_id, rc.reddit_client_secret),
                            data={"grant_type": "client_credentials"})
        if tok.status_code != 200:
            raise RuntimeError(f"jeton Reddit refusé : {tok.status_code}")
        headers["Authorization"] = f"bearer {tok.json()['access_token']}"
        url = f"https://oauth.reddit.com/r/{rc.reddit_subreddit}/top"
    else:
        url = f"https://www.reddit.com/r/{rc.reddit_subreddit}/top.json"
    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code in (401, 403) and "Authorization" not in headers:
        raise RuntimeError(f"Reddit refuse l'accès sans application ({r.status_code}) : renseigne "
                           f"[radar] reddit_client_id et reddit_client_secret (appli « script » sur "
                           f"https://www.reddit.com/prefs/apps)")
    if r.status_code != 200:
        raise RuntimeError(f"Reddit : erreur {r.status_code}")
    out = []
    for child in r.json().get("data", {}).get("children", []):
        p = child.get("data", {})
        vid = ((p.get("media") or {}).get("reddit_video") or {})
        out.append(entry("Reddit", p.get("title"), p.get("author"), p.get("view_count"),
                         vid.get("duration"), "https://www.reddit.com" + p.get("permalink", ""),
                         _dt(p.get("created_utc")), votes=p.get("score"), flair=p.get("link_flair_text"),
                         link=p.get("url"), kind="vidéo" if p.get("is_video") else (p.get("post_hint") or "inconnu")))
    return out[:rc.count]


# --- Top 10 global ---------------------------------------------------------------------------

def trend(e: dict, now: datetime) -> float | None:
    """Vues (ou votes) par heure depuis la publication ; None si l'un des deux manque."""
    value = e["views"] if e["views"] is not None else e.get("votes")
    if value is None or e["published"] is None:
        return None
    hours = max(0.5, (now - e["published"]).total_seconds() / 3600)
    return value / hours


def _tokens(title: str) -> set[str]:
    t = unicodedata.normalize("NFKD", title.lower()).encode("ascii", "ignore").decode()
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 3 and w not in STOP}


def same_moment(a: dict, b: dict) -> bool:
    """Même moment repris par plusieurs créateurs : même VOD Twitch à moins de 60 s, un post qui
    renvoie vers l'autre entrée, ou des titres presque identiques (mots communs >= 60 %)."""
    if a.get("video_id") and a.get("video_id") == b.get("video_id") and \
            a.get("vod_offset") is not None and b.get("vod_offset") is not None and \
            abs(a["vod_offset"] - b["vod_offset"]) < 60:
        return True
    for x, y in ((a, b), (b, a)):
        link = x.get("link") or ""
        slug = y["url"].rstrip("/").rsplit("/", 1)[-1]
        if link and (y.get("video") and y["video"] in link or slug and slug in link):
            return True
    ta, tb = _tokens(a["title"]), _tokens(b["title"])
    common = ta & tb
    return len(common) >= 2 and len(common) / len(ta | tb) >= 0.6


def group(entries: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for e in entries:
        hits = [g for g in groups if any(same_moment(e, x) for x in g)]
        merged = [e] + [x for g in hits for x in g]
        groups = [g for g in groups if g not in hits] + [merged]
    return groups


def fmt_duration(d: float | None) -> str:
    if d is None:
        return "inconnu"
    d = int(round(d))
    return f"{d} s" if d < 60 else f"{d // 60} min {d % 60:02d} s"


def why(g: list[dict], best: dict) -> str:
    """Ce qui la fait marcher, seulement avec ce qu'on sait (sans regarder la vidéo)."""
    title = best["title"].strip()
    hook = f"« {title} »" if title else "inconnu"
    d = best["duration"]
    length = "inconnu" if d is None else f"{fmt_duration(d)} ({'très court' if d <= 30 else 'court' if d <= 60 else 'long'})"
    scene = f"inconnu (flair « {best['flair']} »)" if best.get("flair") else "inconnu"
    line = f"Accroche : {hook} · Scène : {scene} · Durée : {length}"
    if len(g) > 1:
        line += f" · Repris {len(g)} fois ({', '.join(sorted({x['source'] for x in g}))})"
    return line


def top_global(entries: list[dict], now: datetime, n: int = 10) -> list[tuple[float | None, list[dict], dict]]:
    out = []
    for g in group(entries):
        best = max(g, key=lambda e: trend(e, now) or -1)
        out.append((trend(best, now), g, best))
    return sorted(out, key=lambda x: x[0] if x[0] is not None else -1, reverse=True)[:n]


# --- radar.md ---------------------------------------------------------------------------------

def _cell(x) -> str:
    return "inconnu" if x is None or x == "" else str(x).replace("|", "/").replace("\n", " ")


def _num(x) -> str:
    return "inconnu" if x is None else f"{x:,}".replace(",", " ")


def write(path: Path, now: datetime, sections: dict[str, tuple[list[dict], str | None]]) -> None:
    lines = [f"# Radar GTA 6 du {now.astimezone():%Y-%m-%d %H:%M}", "",
             "Dernières 24 h. Rien n'est téléchargé ni reposté. Une info absente : « inconnu ».", ""]
    everything = [e for es, _ in sections.values() for e in es]
    lines += ["## Top 10 global", "",
              "Score de tendance = vues (ou votes Reddit) divisés par les heures depuis la "
              "publication. Vues et votes ne se comparent pas vraiment : à lire avec ça en tête.", "",
              "| Rang | Tendance (/h) | Source | Titre | Auteur | Vues / votes | Durée | Reprises | Lien | Ce qui la fait marcher |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i, (t, g, b) in enumerate(top_global(everything, now), 1):
        value = _num(b["views"]) + " vues" if b["views"] is not None else (
            _num(b.get("votes")) + " votes" if b.get("votes") is not None else "inconnu")
        lines.append("| " + " | ".join(_cell(x) for x in [
            i, "inconnu" if t is None else f"{t:,.0f}".replace(",", " "), b["source"], b["title"],
            b["author"], value, fmt_duration(b["duration"]), len(g), b["url"], why(g, b)]) + " |")
    for name, (es, err) in sections.items():
        lines += ["", f"## {name}", ""]
        if err:
            lines.append(f"Indisponible : {err}")
            continue
        if not es:
            lines.append("Aucune entrée.")
            continue
        reddit_cols = es[0]["source"] == "Reddit"
        head = "| # | Source | Titre | Auteur | Vues | Durée | Lien |" + (" Votes |" if reddit_cols else "")
        lines += [head, "|" + "---|" * (head.count("|") - 1)]
        for i, e in enumerate(es, 1):
            row = [i, e["source"], e["title"], e["author"], _num(e["views"]), fmt_duration(e["duration"]), e["url"]]
            if reddit_cols:
                row.append(_num(e.get("votes")))
            lines.append("| " + " | ".join(_cell(x) for x in row) + " |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_radar(cfg: Config, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    sections: dict[str, tuple[list[dict], str | None]] = {}
    for name, fetch in [
        (f"Twitch : {cfg.radar.twitch_game} (top {cfg.radar.count} par vues)", lambda: twitch(cfg, since, now)),
        (f"YouTube : « {cfg.radar.youtube_query} », vidéos courtes (top {cfg.radar.count} par vues)",
         lambda: youtube(cfg, since, f"{now:%Y-%m-%d}")),
        (f"Reddit : r/{cfg.radar.reddit_subreddit}, top du jour", lambda: reddit(cfg))]:
        try:
            sections[name] = (fetch(), None)
            log.info("%s : %d entrée(s)", name, len(sections[name][0]))
        except Exception as exc:  # noqa: BLE001 - une source en panne n'empêche pas les autres
            log.warning("%s : %s", name, exc)
            sections[name] = ([], str(exc))
    path = Path(cfg.paths.export) / f"{now.astimezone():%Y-%m-%d}" / "radar.md"
    write(path, now, sections)
    return path
