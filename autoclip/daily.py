"""Fonctionnement continu avec quota (`autoclip live`).

- Pendant la journée, chaque pic de chat devient une **candidate** dans `candidates/` : le clip
  brut coupé sans réencodage (`.ts`) et un `.json` (chaîne, heure, score, raison, chat…).
- Une fois par jour à `daily.selection_time`, on garde les `max_clips_per_day` meilleures
  (au moins `min_gap_minutes` entre deux clips d'une même chaîne) et on ne fait le rendu complet
  que pour elles, dans `ready_to_post/AAAA-MM-JJ/`, avec un `classement.md`.
- Les candidates non retenues sont supprimées après `retention_hours` ; `candidates/` reste sous
  `max_disk_gb` (les moins bonnes partent en premier).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from autoclip import video
from autoclip.config import Config
from autoclip.peaks import Peak

log = logging.getLogger(__name__)

STATE = "_etat.json"


def candidates_dir(cfg: Config) -> Path:
    d = Path(cfg.paths.candidates)
    d.mkdir(parents=True, exist_ok=True)
    return d


def reason(peak: Peak) -> str:
    """Une ligne : pourquoi ce moment a été repéré."""
    msgs = ", ".join(f"« {m[:25]} »" for m in peak.sample[:3])
    return (f"Le chat s'emballe : {peak.activity:.0f} messages pondérés en 10 s contre "
            f"{peak.baseline:.0f} d'habitude (score {peak.score:.1f})" + (f" : {msgs}" if msgs else ""))


def load_candidates(cfg: Config) -> list[dict]:
    out = []
    for f in sorted(candidates_dir(cfg).glob("*.json")):
        if f.name == STATE:
            continue
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # écrit à moitié (arrêt brutal) : ignoré, nettoyé avec la rétention
        c["_json"] = f
        out.append(c)
    return out


def save(c: dict) -> None:
    data = {k: v for k, v in c.items() if k != "_json"}
    tmp = c["_json"].with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(c["_json"])  # jamais de .json à moitié écrit


def delete(c: dict) -> None:
    d = c["_json"].parent
    if c.get("raw"):
        (d / c["raw"]).unlink(missing_ok=True)
    c["_json"].unlink(missing_ok=True)


def is_duplicate(cfg: Config, channel: str, t_peak: float) -> bool:
    """Déjà une candidate de cette chaîne à moins de `live.cooldown` s ? (survit aux redémarrages)"""
    return any(c["channel"] == channel and abs(c["t_peak_abs"] - t_peak) < cfg.live.cooldown
               for c in load_candidates(cfg))


def trim_copy(src: Path, start: float, dur: float, out: Path) -> float:
    """Coupe [start, start + dur] de `src` (secondes depuis son début) sans réencoder.

    ffmpeg démarre sur l'image clé précédente : renvoie le vrai début de `out` dans `src`.
    """
    from autoclip.live import PTS_WRAP, _start_time

    # -t côté entrée : avec -copyts, un -t de sortie se compterait depuis l'horodatage d'origine.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(start, 0.0):.3f}", "-t", f"{dur:.3f}",
                    "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-copyts",
                    "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", str(out)],
                   check=True, capture_output=True)
    s_in, s_out = _start_time(src), _start_time(out)
    if s_in is None or s_out is None:
        raise RuntimeError(f"coupe sans réencodage illisible : {out}")
    return (s_out - s_in) % PTS_WRAP


def save_candidate(cfg: Config, job: dict, peak_abs: Peak) -> Path | None:
    """Transforme une découpe du buffer (live._cut) en candidate ; supprime la découpe."""
    raw, clip = Path(job["raw"]), job["peak"]
    d = candidates_dir(cfg)
    name = job["name"]
    try:
        # Seulement le clip (+ 3 s avant pour l'image clé), pas les segments entiers.
        offset = trim_copy(raw, clip.start - 3, clip.end - clip.start + 4, d / f"{name}.ts")
    finally:
        raw.unlink(missing_ok=True)
    peak = Peak(**{**clip.to_dict(), "start": max(clip.start - offset, 0.0),
                   "end": clip.end - offset, "t_peak": clip.t_peak - offset})
    c = {"_json": d / f"{name}.json", "name": name, "channel": cfg.twitch.channel,
         "raw": f"{name}.ts", "created": datetime.now().timestamp(),
         "t_peak_abs": peak_abs.t_peak, "start_ts": job["start_ts"] + offset,
         "peak": peak.to_dict(), "score": peak.score, "duration": round(peak.end - peak.start, 1),
         "reason": reason(peak), "metadata": job["metadata"], "chat": job["chat"],
         "status": "candidate"}
    save(c)
    log.info("[%s] Candidate %s (score %.1f, %.0f s)", c["channel"], name, c["score"], c["duration"])
    return c["_json"]


def pick(cands: list[dict], n: int, min_gap_s: float) -> list[dict]:
    """Les n meilleurs scores, au moins `min_gap_s` entre deux clips d'une même chaîne."""
    chosen: list[dict] = []
    for c in sorted(cands, key=lambda c: c["score"], reverse=True):
        if len(chosen) >= n:
            break
        if all(c["channel"] != k["channel"] or abs(c["t_peak_abs"] - k["t_peak_abs"]) >= min_gap_s
               for k in chosen):
            chosen.append(c)
    return chosen


def read_state(cfg: Config) -> dict:
    try:
        return json.loads((candidates_dir(cfg) / STATE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def selection_time(cfg: Config, day: datetime) -> datetime:
    h, m = (int(x) for x in cfg.daily.selection_time.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def selection_due(cfg: Config, now: datetime) -> bool:
    """L'heure du tri est passée et le tri du jour n'a pas encore été fait (rattrapé au redémarrage)."""
    return now >= selection_time(cfg, now) and read_state(cfg).get("last_selection") != f"{now:%Y-%m-%d}"


def next_selection(cfg: Config, now: datetime) -> datetime:
    t = selection_time(cfg, now)
    return t if not selection_due(cfg, now) and t > now else t + timedelta(days=1)


def run_selection(cfg: Config, now: datetime) -> Path:
    """Tri du jour : rendu complet des meilleures candidates + classement.md."""
    from autoclip.live import channel_config
    from autoclip.pipeline import process_peak, upload_clips

    day = f"{now:%Y-%m-%d}"
    out_root = Path(cfg.paths.export) / day
    out_root.mkdir(parents=True, exist_ok=True)
    cands = [c for c in load_candidates(cfg) if c["status"] == "candidate"]
    chosen = pick(cands, cfg.daily.max_clips_per_day, cfg.daily.min_gap_minutes * 60)
    log.info("Tri du %s : %d candidate(s), %d retenue(s)", day, len(cands), len(chosen))
    rows = []
    for rank, c in enumerate(chosen, 1):
        name = f"{rank:02d}_{c['channel']}_{datetime.fromtimestamp(c['t_peak_abs']):%Hh%M}"
        ccfg = channel_config(cfg, c["channel"])
        ccfg.paths.export = str(out_root)
        raw = c["_json"].parent / c["raw"]
        hook = ""
        try:
            session = {"video_path": raw, "start_ts": c["start_ts"], "channel": c["channel"],
                       "metadata": c["metadata"]}
            out = process_peak(ccfg, session, video.probe(raw), Peak(**c["peak"]), rank, name=name,
                               messages=c["chat"])
            hook = json.loads((out / "meta.json").read_text(encoding="utf-8")).get("hook", "")
            c.update(status="selected", output=str(out))
            if cfg.tiktok.auto_upload:
                upload_clips(ccfg, [out])
        except Exception:  # noqa: BLE001 - un clip raté ne bloque pas les autres
            log.exception("Rendu de %s raté", c["name"])
            c.update(status="failed")
        raw.unlink(missing_ok=True)
        save(c)
        rows.append((rank, c, hook))
    for c in cands:
        if c not in chosen:
            c["status"] = "rejected"
            save(c)
    write_ranking(cfg, out_root / "classement.md", day, rows, len(cands))
    state = read_state(cfg)
    state["last_selection"] = day
    (candidates_dir(cfg) / STATE).write_text(json.dumps(state), encoding="utf-8")
    return out_root


def write_ranking(cfg: Config, path: Path, day: str, rows: list, n_cands: int) -> None:
    lines = [f"# Clips du {day}", "",
             f"{n_cands} candidate(s) repérée(s), {len(rows)} retenue(s) (max "
             f"{cfg.daily.max_clips_per_day}, au moins {cfg.daily.min_gap_minutes:g} min entre deux "
             f"clips d'une même chaîne).", ""]
    if rows:
        lines += ["| Rang | Chaîne | Heure | Score | Durée | Accroche | Pourquoi | Dossier |",
                  "|---|---|---|---|---|---|---|---|"]
        for rank, c, hook in rows:
            folder = Path(c["output"]).name if c.get("output") else "rendu raté"
            cells = [str(rank), c["channel"], f"{datetime.fromtimestamp(c['t_peak_abs']):%H:%M}",
                     f"{c['score']:.1f}", f"{c['duration']:.0f} s", hook, c["reason"], folder]
            lines.append("| " + " | ".join(x.replace("|", "/") for x in cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _size(cands: list[dict]) -> int:
    total = 0
    for c in cands:
        for f in (c["_json"], c["_json"].parent / c.get("raw", "")):
            if f.is_file():
                total += f.stat().st_size
    return total


def cleanup(cfg: Config, now: datetime) -> None:
    """Supprime les candidates de plus de `retention_hours`, puis les moins bonnes tant que
    `candidates/` dépasse `max_disk_gb` (d'abord les non retenues, puis les plus petits scores)."""
    limit_age = now.timestamp() - cfg.daily.retention_hours * 3600
    cands = []
    for c in load_candidates(cfg):
        if c["created"] < limit_age:
            log.info("Candidate %s supprimée (plus de %g h)", c["name"], cfg.daily.retention_hours)
            delete(c)
        else:
            cands.append(c)
    limit = cfg.daily.max_disk_gb * 1e9
    victims = sorted(cands, key=lambda c: (c["status"] == "candidate", c["score"]))
    while victims and _size(victims) > limit:
        c = victims.pop(0)
        log.warning("candidates/ dépasse %g Go : %s supprimée (score %.1f, %s)",
                    cfg.daily.max_disk_gb, c["name"], c["score"], c["status"])
        delete(c)
    # Restes d'un arrêt brutal : .tmp, et .ts sans .json depuis plus d'une heure.
    known = {c.get("raw") for c in cands}
    for f in candidates_dir(cfg).iterdir():
        orphan = f.suffix == ".ts" and f.name not in known and f.stat().st_mtime < now.timestamp() - 3600
        if f.suffix == ".tmp" or orphan:
            f.unlink(missing_ok=True)


def write_heartbeat(path: Path, status: dict, next_sel: datetime) -> None:
    """Fichier mis à jour chaque minute : s'il est vieux, autoclip ne tourne plus."""
    data = {"time": datetime.now().isoformat(timespec="seconds"), "pid": os.getpid(),
            "prochain_tri": next_sel.isoformat(timespec="minutes"), "chaines": status}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
