"""Détection du live et enregistrement via Streamlink (+ chat en parallèle)."""

from __future__ import annotations

import json
import logging
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from autoclip.chat import ChatLogger
from autoclip.config import Config

log = logging.getLogger(__name__)


def _streamlink_cmd() -> list[str]:
    exe = shutil.which("streamlink")
    return [exe] if exe else [sys.executable, "-m", "streamlink"]


def _twitch_args(cfg: Config) -> list[str]:
    args = ["--twitch-low-latency"]
    if cfg.twitch.oauth_token:
        args += ["--twitch-api-header", f"Authorization=OAuth {cfg.twitch.oauth_token}"]
    return args


def channel_url(cfg: Config) -> str:
    return f"https://www.twitch.tv/{cfg.twitch.channel}"


def is_live(cfg: Config) -> tuple[bool, dict]:
    """Interroge Streamlink en JSON : renvoie (en_live, métadonnées)."""
    cmd = _streamlink_cmd() + _twitch_args(cfg) + ["--json", channel_url(cfg)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        log.warning("Vérification du live impossible : %s", exc)
        return False, {}
    if data.get("streams"):
        return True, data.get("metadata") or {}
    return False, {}


def wait_until_live(cfg: Config) -> dict:
    log.info("En attente du live de %s (vérification toutes les %ss)…",
             cfg.twitch.channel, cfg.twitch.poll_interval)
    while True:
        live, meta = is_live(cfg)
        if live:
            log.info("%s est en live : %s", cfg.twitch.channel, meta.get("title", ""))
            return meta
        time.sleep(cfg.twitch.poll_interval)


def new_session_dir(cfg: Config) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    path = Path(cfg.paths.recordings) / f"{cfg.twitch.channel}_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record(cfg: Config, session_dir: Path | None = None, meta: dict | None = None,
           max_duration: float | None = None) -> Path:
    """Enregistre vidéo + chat jusqu'à la fin du live (ou Ctrl+C). Renvoie le dossier de session."""
    session_dir = session_dir or new_session_dir(cfg)
    video = session_dir / "stream.ts"
    chat_path = session_dir / "chat.jsonl"

    cmd = _streamlink_cmd() + _twitch_args(cfg) + [
        "--retry-open", "5",
        "--stream-segment-threads", "2",
        "--hls-live-edge", "3",
        "-o", str(video),
        "--force",
        channel_url(cfg), cfg.twitch.quality,
    ]

    chat = ChatLogger(cfg.twitch.channel, chat_path)
    chat.start()
    log.info("Enregistrement → %s", video)
    proc = subprocess.Popen(cmd)

    # L'horloge de référence = premier octet écrit sur disque.
    start_ts = None
    t0 = time.time()
    interrupted = False
    try:
        while proc.poll() is None:
            if start_ts is None and video.exists() and video.stat().st_size > 0:
                start_ts = time.time()
                _write_session(session_dir, cfg, start_ts, None, meta)
                log.info("Premières données reçues, synchro chat calée.")
            if max_duration and time.time() - t0 > max_duration:
                log.info("Durée max atteinte, arrêt de l'enregistrement.")
                _stop_proc(proc)
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        interrupted = True
        log.info("Ctrl+C : arrêt propre de l'enregistrement…")
        _stop_proc(proc)
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        chat.stop()
        chat.join(timeout=5)

    end_ts = time.time()
    if start_ts is None:
        log.warning("Aucune donnée vidéo reçue.")
        start_ts = t0
    _write_session(session_dir, cfg, start_ts, end_ts, meta)
    log.info("Session terminée : %d messages de chat, %s", chat.count, session_dir)
    if interrupted:
        raise KeyboardInterrupt
    return session_dir


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGINT)


def _write_session(session_dir: Path, cfg: Config, start_ts: float,
                   end_ts: float | None, meta: dict | None) -> None:
    info = {
        "channel": cfg.twitch.channel,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "video": "stream.ts",
        "chat": "chat.jsonl",
        "metadata": meta or {},
    }
    (session_dir / "session.json").write_text(json.dumps(info, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
