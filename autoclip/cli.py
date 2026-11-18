"""Ligne de commande : `autoclip live | watch | record | process | detect | upload | tiktok-auth | check`."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import shutil
import sys
import threading
from pathlib import Path

from autoclip.config import Config, load_config, set_channels

log = logging.getLogger("autoclip")


def _process_and_upload(cfg: Config, session_dir: Path) -> None:
    from autoclip.pipeline import process_session, upload_clips

    try:
        clips = process_session(cfg, session_dir)
        log.info("%d clip(s) prêts dans %s", len(clips), cfg.paths.export)
        if clips and cfg.tiktok.auto_upload:
            upload_clips(cfg, clips)
    except Exception:  # noqa: BLE001
        log.exception("Traitement de %s raté", session_dir)


def cmd_watch(cfg: Config, args) -> int:
    from autoclip.recorder import record, wait_until_live

    workers: list[threading.Thread] = []
    recording = False
    try:
        while True:
            meta = wait_until_live(cfg)
            recording = True
            session = record(cfg, meta=meta)
            recording = False
            # Traitement en arrière-plan : si le live reprend (coupure), on réenregistre tout de suite.
            t = threading.Thread(target=_process_and_upload, args=(cfg, session))
            t.start()
            workers.append(t)
            if args.once:
                break
    except KeyboardInterrupt:
        log.info("Arrêt demandé.")
        last = _latest_session(cfg)
        if recording and last and not args.no_process_on_exit:
            log.info("Traitement de la session interrompue…")
            _process_and_upload(cfg, last)
    for t in workers:
        t.join()
    return 0


def cmd_live(cfg: Config, args) -> int:
    from autoclip.live import run_live

    return run_live(cfg)


def _latest_session(cfg: Config) -> Path | None:
    root = Path(cfg.paths.recordings)
    sessions = sorted(root.glob("*/session.json"), key=lambda p: p.stat().st_mtime)
    return sessions[-1].parent if sessions else None


def cmd_record(cfg: Config, args) -> int:
    from autoclip.recorder import is_live, record

    live, meta = is_live(cfg)
    if not live:
        log.error("%s n'est pas en live.", cfg.twitch.channel)
        return 1
    try:
        session = record(cfg, meta=meta, max_duration=args.duration)
    except KeyboardInterrupt:
        session = _latest_session(cfg)
    print(session)
    if args.process and session:
        _process_and_upload(cfg, session)
    return 0


def cmd_process(cfg: Config, args) -> int:
    from autoclip.pipeline import process_session, upload_clips

    clips = process_session(cfg, Path(args.session))
    for c in clips:
        print(c)
    if args.upload and clips:
        upload_clips(cfg, clips)
    return 0 if clips else 1


def cmd_top(cfg: Config, args) -> int:
    from autoclip.top import run_top

    clips = run_top(cfg, args.lang or cfg.top.lang, args.count or cfg.top.count)
    for c in clips:
        print(c)
    return 0 if clips else 1


def cmd_detect(cfg: Config, args) -> int:
    from autoclip.chat import load_chat
    from autoclip.peaks import activity_series, ascii_timeline, fmt_time
    from autoclip.pipeline import find_peaks, load_session

    session = load_session(Path(args.session))
    peaks, meta, _messages = find_peaks(cfg, session)
    _, _, act = activity_series(load_chat(session["chat_path"]), session["start_ts"],
                                meta["duration"], cfg.detection)
    print(ascii_timeline(act, peaks))
    print(f"\n{len(peaks)} pic(s) sur {fmt_time(meta['duration'])} :")
    for i, p in enumerate(peaks, 1):
        sample = " | ".join(p.sample[:3])
        print(f"  {i:2d}. {fmt_time(p.start)} → {fmt_time(p.end)}  score={p.score:5.1f}  "
              f"activité={p.activity:.0f} (base {p.baseline:.0f})  « {sample[:70]} »")
    return 0


def cmd_upload(cfg: Config, args) -> int:
    from autoclip.pipeline import pending_clips, upload_clips

    dirs = [Path(d) for d in args.clips] or pending_clips(cfg)
    if not dirs:
        print("Rien à uploader.")
        return 0
    n = upload_clips(cfg, dirs)
    print(f"{n}/{len(dirs)} clip(s) envoyé(s) sur TikTok.")
    return 0 if n == len(dirs) else 1


def cmd_tiktok_auth(cfg: Config, args) -> int:
    from autoclip.tiktok import authorize

    authorize(cfg.tiktok)
    print("OK, token enregistré.")
    return 0


def cmd_check(cfg: Config, args) -> int:
    ok = True

    def line(good: bool, label: str, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'OK' if good else '!!'}] {label}" + (f"  → {hint}" if hint and not good else ""))

    print(f"Chaîne(s) : {', '.join(cfg.twitch.channels or [cfg.twitch.channel])}")
    line(bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe")), "ffmpeg / ffprobe",
         "installe ffmpeg et ajoute-le au PATH")
    try:
        import streamlink
        line(True, f"streamlink {streamlink.__version__}")
    except ImportError:
        line(False, "streamlink", "pip install streamlink")
    if cfg.subtitles.enabled:
        try:
            import faster_whisper
            line(True, f"faster-whisper {faster_whisper.__version__} (modèle {cfg.subtitles.model})")
        except ImportError:
            line(False, "faster-whisper", "pip install -e .[whisper]  (ou subtitles.enabled = false)")
    if cfg.tiktok.auto_upload or args.tiktok:
        line(bool(cfg.tiktok.client_key and cfg.tiktok.client_secret), "identifiants TikTok",
             "renseigne [tiktok] client_key / client_secret")
        line(Path(cfg.tiktok.token_file).exists(), "token TikTok", "autoclip tiktok-auth")
    if args.live:
        from autoclip.live import channel_config
        from autoclip.recorder import is_live
        for ch in cfg.twitch.channels or [cfg.twitch.channel]:
            live, meta = is_live(channel_config(cfg, ch))
            print(f"  [..] {ch} est {'EN LIVE : ' + meta.get('title', '') if live else 'hors ligne'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoclip", description=__doc__)
    parser.add_argument("-c", "--config", help="fichier config TOML (défaut : ./config.toml)")
    parser.add_argument("--channel", help="surcharge la chaîne Twitch")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("watch", help="attend le live, enregistre, clippe (et upload) en boucle")
    p.add_argument("--once", action="store_true", help="s'arrête après un live")
    p.add_argument("--no-process-on-exit", action="store_true")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("live", help="mode continu : buffer glissant, clips coupés pendant le live")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("record", help="enregistre maintenant (vidéo + chat)")
    p.add_argument("--duration", type=float, help="durée max en secondes (test)")
    p.add_argument("--process", action="store_true", help="traite la session à la fin")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("process", help="traite une session enregistrée")
    p.add_argument("session", help="dossier recordings/<session>")
    p.add_argument("--upload", action="store_true", help="upload TikTok ensuite")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("top", help="top des clips Twitch du jour (API Twitch), prêts à poster")
    p.add_argument("--lang", help="langue des clips (défaut : [top] lang, fr)")
    p.add_argument("--count", type=int, help="nombre de clips (défaut : [top] count, 3)")
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("detect", help="affiche les pics détectés (réglage des seuils)")
    p.add_argument("session")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("upload", help="upload TikTok des clips pas encore envoyés")
    p.add_argument("clips", nargs="*", help="dossiers de clips (défaut : tous les non-uploadés)")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("tiktok-auth", help="connecte ton compte TikTok (OAuth)")
    p.set_defaults(func=cmd_tiktok_auth)

    p = sub.add_parser("check", help="vérifie l'installation")
    p.add_argument("--live", action="store_true", help="vérifie aussi si la chaîne est en live")
    p.add_argument("--tiktok", action="store_true", help="vérifie aussi la config TikTok")
    p.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    # Journal tournant : 5 Mo par fichier, 5 anciens gardés (autoclip.log.1 … .5).
    rotating = logging.handlers.RotatingFileHandler("autoclip.log", maxBytes=5_000_000,
                                                    backupCount=5, encoding="utf-8")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), rotating],
    )
    cfg = load_config(args.config)
    if args.channel:
        set_channels(cfg, [args.channel])
    return args.func(cfg, args)
