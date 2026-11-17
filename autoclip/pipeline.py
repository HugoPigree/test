"""Orchestration : session enregistrée -> pics -> clips verticaux sous-titrés -> dossier prêt à poster."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from autoclip import subtitles, video
from autoclip.chat import load_chat
from autoclip.config import Config
from autoclip.peaks import Peak, detect_peaks, fmt_time

log = logging.getLogger(__name__)


def load_session(session_dir: Path) -> dict:
    path = Path(session_dir) / "session.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} introuvable (dossier de session invalide)")
    info = json.loads(path.read_text(encoding="utf-8"))
    info["dir"] = Path(session_dir)
    info["video_path"] = Path(session_dir) / info.get("video", "stream.ts")
    info["chat_path"] = Path(session_dir) / info.get("chat", "chat.jsonl")
    return info


def find_peaks(cfg: Config, session: dict) -> tuple[list[Peak], dict]:
    meta = video.probe(session["video_path"])
    messages = load_chat(session["chat_path"]) if session["chat_path"].exists() else []
    if not messages:
        log.warning("Aucun message de chat dans %s", session["chat_path"])
    peaks = detect_peaks(messages, session["start_ts"], meta["duration"], cfg.detection)
    return peaks, meta


def make_caption(cfg: Config, transcript: str, peak: Peak, stream_title: str) -> str:
    hook = ""
    if transcript:
        first = re.split(r"(?<=[.!?…])\s+", transcript.strip())[0]
        hook = first if len(first) <= cfg.export.hook_max_chars else (
            first[: cfg.export.hook_max_chars].rsplit(" ", 1)[0] + "…")
    if not hook:
        hook = stream_title[: cfg.export.hook_max_chars] or (peak.sample[0] if peak.sample else "")
    return cfg.export.caption_template.format(
        hook=hook, channel=cfg.twitch.channel, title=stream_title).strip()


def process_peak(cfg: Config, session: dict, meta: dict, peak: Peak, index: int) -> Path | None:
    start_dt = datetime.fromtimestamp(session["start_ts"])
    name = (f"{start_dt:%Y-%m-%d}_{session['channel']}_{index:02d}_"
            f"{fmt_time(peak.t_peak).replace(':', 'h', 1).replace(':', 'm')}s")
    out_dir = Path(cfg.paths.export) / name
    final = out_dir / "video.mp4"
    if final.exists():
        log.info("Déjà exporté : %s", out_dir)
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    src = session["video_path"]
    dur = peak.end - peak.start
    t0 = time.time()

    words: list[subtitles.Word] = []
    ass_path = None
    if cfg.subtitles.enabled and meta["has_audio"]:
        wav = out_dir / "audio.wav"
        try:
            video.extract_audio(src, peak.start, dur, wav)
            words = subtitles.transcribe(wav, cfg.subtitles)
            if words:
                ass_path = out_dir / "subtitles.ass"
                ass_path.write_text(
                    subtitles.build_ass(words, cfg.subtitles, cfg.vertical.width, cfg.vertical.height),
                    encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - les sous-titres ne doivent jamais bloquer l'export
            log.warning("Sous-titres ignorés pour %s : %s", name, exc)
        finally:
            wav.unlink(missing_ok=True)

    tmp = out_dir / "video.part.mp4"
    video.render_clip(src, peak.start, dur, tmp, cfg.vertical, meta["width"], meta["height"],
                      ass_path=ass_path, has_audio=meta["has_audio"])
    tmp.replace(final)
    video.thumbnail(final, out_dir / "thumbnail.jpg", at=min(dur - 0.5, peak.t_peak - peak.start - 3))
    if cfg.export.keep_horizontal:
        video.render_horizontal(src, peak.start, dur, out_dir / "horizontal.mp4")

    transcript = subtitles.transcript_text(words)
    stream_title = (session.get("metadata") or {}).get("title") or ""
    caption = make_caption(cfg, transcript, peak, stream_title)
    (out_dir / "caption.txt").write_text(caption + "\n", encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps({
        "channel": session["channel"],
        "stream_title": stream_title,
        "stream_started_at": start_dt.isoformat(timespec="seconds"),
        "source": str(src),
        "clip_start": peak.start,
        "clip_end": peak.end,
        "stream_time": fmt_time(peak.start),
        "peak": peak.to_dict(),
        "transcript": transcript,
        "caption": caption,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "tiktok": None,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("✔ %s (%.0fs, score %.1f) en %.0fs", out_dir, dur, peak.score, time.time() - t0)
    return out_dir


def process_session(cfg: Config, session_dir: Path) -> list[Path]:
    session = load_session(session_dir)
    if not session["video_path"].exists():
        raise FileNotFoundError(session["video_path"])
    peaks, meta = find_peaks(cfg, session)
    log.info("%d pic(s) détecté(s) sur %s de live", len(peaks), fmt_time(meta["duration"]))
    outputs = []
    for i, peak in enumerate(peaks, 1):
        log.info("[%d/%d] %s → %s (score %.1f, %d msgs)", i, len(peaks), fmt_time(peak.start),
                 fmt_time(peak.end), peak.score, peak.n_messages)
        try:
            out = process_peak(cfg, session, meta, peak, i)
            if out:
                outputs.append(out)
        except Exception as exc:  # noqa: BLE001 - un clip raté ne doit pas tuer les autres
            log.error("Clip %d raté : %s", i, exc)
    if cfg.export.delete_raw and outputs and len(outputs) == len(peaks):
        session["video_path"].unlink(missing_ok=True)
        log.info("Enregistrement brut supprimé.")
    return outputs


def pending_clips(cfg: Config) -> list[Path]:
    root = Path(cfg.paths.export)
    if not root.exists():
        return []
    out = []
    for meta_path in sorted(root.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not meta.get("tiktok") and (meta_path.parent / "video.mp4").exists():
            out.append(meta_path.parent)
    return out


def upload_clips(cfg: Config, clip_dirs: list[Path] | None = None) -> int:
    from autoclip import tiktok

    dirs = clip_dirs if clip_dirs else pending_clips(cfg)
    ok = 0
    for d in dirs:
        d = Path(d)
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("tiktok"):
            log.info("Déjà uploadé : %s", d)
            continue
        caption = (d / "caption.txt").read_text(encoding="utf-8").strip()
        try:
            result = tiktok.upload_video(cfg.tiktok, d / "video.mp4", caption)
        except Exception as exc:  # noqa: BLE001
            log.error("Upload TikTok raté pour %s : %s", d.name, exc)
            continue
        result["uploaded_at"] = datetime.now().isoformat(timespec="seconds")
        meta["tiktok"] = result
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("✔ TikTok %s : %s", d.name, result.get("status"))
        ok += 1
    return ok
