"""Mode continu (`autoclip live`) : pour un live qui ne s'arrête jamais.

- Streamlink -> ffmpeg écrit le live en segments MPEG-TS de `live.segment_seconds` dans
  recordings/<chaîne>_buffer/<connexion>/ ; les segments plus vieux que `live.buffer_minutes`
  sont supprimés, donc le disque ne grossit pas.
- Le chat est gardé en mémoire et analysé toutes les 5 s avec la même détection que `process`.
- Quand la fin d'un pic (pic + post_roll) est passée, les segments qui le couvrent sont copiés
  dans recordings/<chaîne>_queue/ puis le clip part dans une file : un seul clip est traité à la
  fois, en priorité basse (nice sous Linux, « inférieure à la normale » sous Windows), pour ne
  pas gêner l'enregistrement.
- Si Streamlink s'arrête (coupure, fin du live), on revérifie le live et on relance une nouvelle
  connexion. Le chat se reconnecte tout seul (ChatLogger).
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from autoclip import video
from autoclip.chat import ChatLogger
from autoclip.config import Config
from autoclip.peaks import Peak, detect_peaks
from autoclip.recorder import _streamlink_cmd, _twitch_args, channel_url, is_live

log = logging.getLogger(__name__)

PTS_WRAP = 2 ** 33 / 90000  # les horodatages MPEG-TS repartent de 0 toutes les ~26,5 h
DETECT_EVERY = 5.0          # secondes entre deux analyses du chat
VIDEO_MARGIN = 10.0         # secondes laissées à la vidéo pour arriver sur le disque
# Windows : l'enregistrement garde la priorité normale même si autoclip est en priorité basse.
WIN_NORMAL = {"creationflags": subprocess.NORMAL_PRIORITY_CLASS} if sys.platform == "win32" else {}


def _start_time(path: Path) -> float | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None  # segment tout juste créé, encore vide


class Recording:
    """Une connexion Streamlink -> segments.

    Horloge : `start_ts` (heure du 1er octet écrit) correspond au début du 1er segment ; comme
    ffmpeg garde des horodatages continus d'un segment à l'autre, la position d'un segment dans
    la connexion = son start_time - celui du 1er segment.
    """

    def __init__(self, cfg: Config, root: Path):
        self.dir = root / datetime.now().strftime("%Y-%m-%d_%Hh%Mm%Ss")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.start_ts: float | None = None
        self.pts0: float | None = None
        self._starts: dict[str, float] = {}
        streamlink = _streamlink_cmd() + _twitch_args(cfg) + [
            "--retry-open", "5", "--stream-segment-threads", "2", "--hls-live-edge", "3",
            "--stdout", channel_url(cfg), cfg.twitch.quality]
        ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                  "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                  "-f", "segment", "-segment_time", str(cfg.live.segment_seconds),
                  "-segment_format", "mpegts", str(self.dir / "seg_%05d.ts")]
        log.info("Enregistrement en segments → %s", self.dir)
        self.streamlink = subprocess.Popen(streamlink, stdout=subprocess.PIPE, **WIN_NORMAL)
        self.ffmpeg = subprocess.Popen(ffmpeg, stdin=self.streamlink.stdout, **WIN_NORMAL)
        self.streamlink.stdout.close()  # seul ffmpeg lit le tuyau

    def alive(self) -> bool:
        return self.ffmpeg.poll() is None

    def tick(self) -> None:
        if self.start_ts is None:
            first = self.dir / "seg_00000.ts"
            if first.exists() and first.stat().st_size > 0:
                self.start_ts = time.time()
                log.info("Premières données vidéo reçues, synchro chat calée.")

    def segments(self) -> list[tuple[float, Path]]:
        """[(début du segment en s depuis le début de la connexion, fichier)], dans l'ordre."""
        out = []
        for f in sorted(self.dir.glob("seg_*.ts")):
            if f.name not in self._starts:
                start = _start_time(f)
                if start is None:
                    continue
                self._starts[f.name] = start
            if self.pts0 is None:
                self.pts0 = self._starts[f.name]
            out.append(((self._starts[f.name] - self.pts0) % PTS_WRAP, f))
        return out

    def stop(self) -> None:
        # On arrête Streamlink : ffmpeg voit la fin du flux et ferme proprement son dernier segment.
        if self.streamlink.poll() is None:
            self.streamlink.terminate()
        for p in (self.streamlink, self.ffmpeg):
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def cut_segments(segments: list[tuple[float, Path]], r0: float, r1: float, dest: Path) -> float | None:
    """Copie bout à bout les segments qui couvrent [r0, r1] (le MPEG-TS se concatène tel quel).

    Renvoie la position (dans la connexion) du début de `dest`, ou None si rien ne couvre r0-r1.
    """
    chosen = [(start, f) for i, (start, f) in enumerate(segments)
              if start < r1 and (i + 1 == len(segments) or segments[i + 1][0] > r0)]
    if not chosen:
        return None
    with dest.open("wb") as out:
        for _, f in chosen:
            with f.open("rb") as src:
                shutil.copyfileobj(src, out)
    return chosen[0][0]


def purge(root: Path, keep_seconds: float, active: Path | None) -> None:
    """Supprime les segments plus vieux que `keep_seconds` et les dossiers de connexion vides."""
    limit = time.time() - keep_seconds
    for f in root.glob("*/seg_*.ts"):
        if f.stat().st_mtime < limit:  # mtime = dernière écriture = fin du segment
            f.unlink()
    for d in root.iterdir():
        if d.is_dir() and d != active and not any(d.glob("seg_*.ts")):
            shutil.rmtree(d)


def ready_peaks(cfg: Config, messages: list[dict], now: float, last_clip: float) -> list[Peak]:
    """Pics à couper maintenant, en heure absolue (epoch) : leur fin (pic + post_roll) est passée
    et ils arrivent plus de `cooldown` secondes après le pic du clip précédent."""
    # Avec moins de `baseline_window` d'historique (juste après le lancement), la ligne de base
    # n'est pas fiable et du chat normal passe pour un pic. Les pics de ces premières minutes
    # sont repérés ensuite (ils sont encore dans le buffer).
    if not messages or now - messages[0]["ts"] < cfg.detection.baseline_window:
        return []
    # On ne remonte pas avant le 1er message reçu : sinon le « vide » avant la connexion du chat
    # fait passer le chat normal pour un pic.
    t0 = max(now - max(900.0, 3 * cfg.detection.baseline_window), messages[0]["ts"])
    out = []
    for p in detect_peaks(messages, t0, now - t0, cfg.detection):
        p = dataclasses.replace(p, t_peak=t0 + p.t_peak, start=t0 + p.start, end=t0 + p.end)
        if p.t_peak <= last_clip + cfg.live.cooldown or p.end + VIDEO_MARGIN > now:
            continue
        out.append(p)
        last_clip = p.t_peak
    return out


def chat_history_seconds(cfg: Config) -> float:
    """Durée de chat gardée en mémoire : au moins ce que regarde `ready_peaks` (sinon, avec un petit
    buffer vidéo, le 1er message a toujours moins de `baseline_window` et rien n'est jamais détecté)."""
    return max(cfg.live.buffer_minutes * 60, 900.0, 3 * cfg.detection.baseline_window) + 60


def _lower_priority() -> None:
    """Priorité basse pour le traitement des clips : Whisper et les ffmpeg lancés d'ici en héritent.

    Linux : nice 10 sur le thread courant seulement. Windows : une priorité de thread ne passe pas
    aux process enfants, donc tout autoclip passe en « inférieure à la normale » ; Streamlink et
    le ffmpeg du buffer restent en priorité normale (WIN_NORMAL dans Recording).
    """
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Sans ces types, ctypes tronque le handle à 32 bits et l'appel échoue.
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            if not kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x4000):  # BELOW_NORMAL
                raise OSError
        else:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError):
        log.warning("Impossible de baisser la priorité du traitement des clips.")


def _worker(jobs: queue.Queue) -> None:
    """Un seul traitement de clip à la fois, toutes chaînes confondues (un seul Whisper en mémoire)."""
    from autoclip.pipeline import process_peak, upload_clips

    _lower_priority()
    while True:
        job = jobs.get()
        if job is None:
            return
        cfg = job["cfg"]
        log.info("Traitement de %s (%d autre(s) en attente)", job["name"], jobs.qsize())
        try:
            session = {"video_path": job["raw"], "start_ts": job["start_ts"],
                       "channel": cfg.twitch.channel, "metadata": job["metadata"]}
            out = process_peak(cfg, session, video.probe(job["raw"]), job["peak"], 0,
                               name=job["name"], messages=job["chat"])
            if out and cfg.tiktok.auto_upload:
                upload_clips(cfg, [out])
        except Exception:  # noqa: BLE001 - un clip raté ne doit pas arrêter le mode continu
            log.exception("Clip %s raté", job["name"])
        finally:
            job["raw"].unlink(missing_ok=True)


def _cut(cfg: Config, rec: Recording, peak: Peak, queue_dir: Path, metadata: dict,
         messages: list[dict]) -> dict | None:
    """Coupe le pic (heures absolues) depuis le buffer ; renvoie la tâche à mettre en file."""
    name = f"{datetime.fromtimestamp(peak.t_peak):%Y-%m-%d_%Hh%Mm%Ss}_{cfg.twitch.channel}"
    r0, r1 = peak.start - rec.start_ts, peak.end - rec.start_ts  # secondes dans la connexion
    raw = queue_dir / f"{name}.ts"
    offset = cut_segments(rec.segments(), max(r0, 0.0), r1, raw)
    if offset is None:
        log.warning("Pic %s : rien dans le buffer pour ce moment, ignoré.", name)
        return None
    # Le pic en secondes depuis le début de `raw`.
    clip = dataclasses.replace(peak, start=max(r0 - offset, 0.0), end=r1 - offset,
                               t_peak=peak.t_peak - rec.start_ts - offset)
    if clip.end - clip.start < cfg.detection.min_duration:
        log.warning("Pic %s : trop peu de vidéo dans le buffer (reconnexion ?), ignoré.", name)
        raw.unlink(missing_ok=True)
        return None
    chat = [m for m in messages if peak.start - 5 <= m["ts"] <= peak.end + 5]
    return {"name": name, "raw": raw, "peak": clip, "start_ts": rec.start_ts + offset,
            "metadata": metadata, "chat": chat, "cfg": cfg}


def channel_config(cfg: Config, channel: str) -> Config:
    """Même config (mêmes règles), autre chaîne."""
    out = copy.deepcopy(cfg)
    out.twitch.channel = channel
    return out


def _watch(cfg: Config, jobs: queue.Queue, stop: threading.Event) -> None:
    """Surveille une chaîne : enregistrement en segments, chat, détection, découpe des pics."""
    ch = cfg.twitch.channel
    root = Path(cfg.paths.recordings) / f"{ch}_buffer"
    queue_dir = Path(cfg.paths.recordings) / f"{ch}_queue"
    root.mkdir(parents=True, exist_ok=True)
    queue_dir.mkdir(parents=True, exist_ok=True)
    keep = cfg.live.buffer_minutes * 60

    messages: list[dict] = []
    lock = threading.Lock()

    def on_message(msg: dict) -> None:
        with lock:
            messages.append(msg)

    chat = ChatLogger(ch, None, on_message=on_message)
    chat.start()
    rec: Recording | None = None
    metadata: dict = {}
    last_clip = 0.0
    next_live_check = next_detect = next_purge = 0.0
    try:
        while not stop.is_set():
            now = time.time()
            if rec and not rec.alive():
                log.warning("[%s] Enregistrement interrompu (coupure ou fin du live), on relance.", ch)
                rec.stop()
                rec = None
                next_live_check = now
            if rec is None and now >= next_live_check:
                live, meta = is_live(cfg)
                if live:
                    metadata = meta
                    log.info("[%s] En live : %s", ch, meta.get("title", ""))
                    rec = Recording(cfg, root)
                else:
                    log.info("[%s] Hors ligne, nouvel essai dans %ss.", ch, cfg.twitch.poll_interval)
                    next_live_check = now + cfg.twitch.poll_interval
            if rec:
                rec.tick()

            if now >= next_detect:
                next_detect = now + DETECT_EVERY
                with lock:
                    messages[:] = [m for m in messages if m["ts"] > now - chat_history_seconds(cfg)]
                    recent = list(messages)
                for peak in ready_peaks(cfg, recent, now, last_clip):
                    last_clip = peak.t_peak
                    log.info("[%s] Pic de chat à %s (score %.1f) : %s", ch,
                             datetime.fromtimestamp(peak.t_peak).strftime("%H:%M:%S"), peak.score,
                             " | ".join(peak.sample[:3])[:80])
                    if rec is None or rec.start_ts is None:
                        log.warning("[%s] Pas d'enregistrement en cours : pic ignoré.", ch)
                        continue
                    job = _cut(cfg, rec, peak, queue_dir, metadata, recent)
                    if job:
                        jobs.put(job)

            if now >= next_purge:
                next_purge = now + 60
                purge(root, keep, rec.dir if rec else None)
            stop.wait(1)
    except Exception:
        log.exception("[%s] Surveillance arrêtée sur une erreur", ch)
        raise
    finally:
        if rec:
            rec.stop()
        chat.stop()


def run_live(cfg: Config) -> int:
    """Une surveillance par chaîne (`twitch.channels`), une seule file de clips pour toutes."""
    channels = cfg.twitch.channels or [cfg.twitch.channel]
    jobs: queue.Queue = queue.Queue()
    worker = threading.Thread(target=_worker, args=(jobs,), daemon=True, name="clips")
    worker.start()
    stop = threading.Event()
    watchers = [threading.Thread(target=_watch, args=(channel_config(cfg, ch), jobs, stop),
                                 daemon=True, name=f"live-{ch}") for ch in channels]
    log.info("Mode continu sur : %s", ", ".join(channels))
    for w in watchers:
        w.start()
    code = 0
    try:
        while all(w.is_alive() for w in watchers):
            time.sleep(1)
        # Une chaîne a planté : on s'arrête, la tâche planifiée relance tout.
        log.error("Une surveillance s'est arrêtée sur une erreur (voir plus haut) : arrêt.")
        code = 1
    except KeyboardInterrupt:
        log.info("Arrêt demandé. Les clips déjà coupés vont être terminés (Ctrl+C à nouveau pour "
                 "quitter tout de suite).")
    stop.set()
    for w in watchers:
        w.join()
    jobs.put(None)
    worker.join()
    return code
