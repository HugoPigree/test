"""Orchestration : session enregistrée -> pics -> clips verticaux sous-titrés -> dossier prêt à poster."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path

from autoclip import context, subtitles, video
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


def find_peaks(cfg: Config, session: dict) -> tuple[list[Peak], dict, list[dict]]:
    meta = video.probe(session["video_path"])
    messages = load_chat(session["chat_path"]) if session["chat_path"].exists() else []
    if not messages:
        log.warning("Aucun message de chat dans %s", session["chat_path"])
    peaks = detect_peaks(messages, session["start_ts"], meta["duration"], cfg.detection)
    return peaks, meta, messages


def _shorten(text: str, max_chars: int) -> str:
    text = text.strip().lstrip("-–— ")  # Whisper met parfois un tiret de dialogue devant
    return text if len(text) <= max_chars else text[:max_chars].rsplit(" ", 1)[0] + "…"


def make_hook(cfg: Config, words: list[subtitles.Word], peak: Peak, stream_title: str) -> str:
    """Accroche (titre incrusté + caption.txt) : la phrase dite au moment où le chat s'emballe,
    sinon le message le plus répété du chat à ce moment, sinon le titre du live."""
    sentences: list[list[subtitles.Word]] = [[]]
    for w in words:
        sentences[-1].append(w)
        if w.text[-1:] in ".?!…":
            sentences.append([])
    sentences = [s for s in sentences if s]
    if sentences:
        t = peak.t_peak - peak.start  # moment du pic, en secondes dans le clip
        # Phrases d'au moins 3 mots et terminées (pas « On a une... »), si possible.
        candidates = [s for s in sentences if len(s) >= 3 and not s[-1].text.endswith(("...", "…"))]
        candidates = candidates or sentences
        best = min(candidates, key=lambda s: max(s[0].start - t, t - s[-1].end, 0.0))
        return _shorten(" ".join(w.text for w in best), cfg.export.hook_max_chars)
    if peak.sample:
        return _shorten(peak.sample[0], cfg.export.hook_max_chars)
    return _shorten(stream_title, cfg.export.hook_max_chars)


def channel_tag(cfg: Config) -> str:
    """Hashtag de la chaîne : « etostark__ » -> « etostark »."""
    return cfg.twitch.channel.strip("_")


def fixed_hashtags(cfg: Config) -> list[str]:
    """Hashtags toujours mis par le modèle de légende (Claude ne doit pas les répéter)."""
    return re.findall(r"#(\w+)", cfg.export.caption_template.replace("{channel_tag}", channel_tag(cfg)))


def make_caption(cfg: Config, hook: str, stream_title: str, ctx: dict | None = None) -> str:
    tags = " ".join(f"#{h}" for h in ctx["hashtags"]) if ctx else ""
    text = cfg.export.caption_template.format(
        hook=hook, caption=ctx["caption"] if ctx else hook, hashtags=tags,
        channel=cfg.twitch.channel, channel_tag=channel_tag(cfg), title=stream_title)
    return " ".join(text.split())


def clip_chat(cfg: Config, session: dict, peak: Peak, messages: list[dict]) -> list[tuple[float, str, str]]:
    """Messages du chat pendant le clip : (secondes depuis le début du clip, pseudo, message)."""
    t0 = session["start_ts"] + cfg.detection.chat_offset + peak.start
    dur = peak.end - peak.start
    return [(m["ts"] - t0, m.get("user", ""), m.get("msg", "")) for m in messages
            if 0 <= m["ts"] - t0 <= dur]


def pick_music(cfg: Config) -> Path | None:
    folder = Path(cfg.music.dir)
    tracks = sorted(f for f in folder.glob("*")
                    if f.suffix.lower() in {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus"})
    if not tracks:
        log.warning("Musique activée mais aucun fichier audio dans %s/", folder)
        return None
    return random.choice(tracks)


def process_peak(cfg: Config, session: dict, meta: dict, peak: Peak, index: int,
                 name: str | None = None, messages: list[dict] | None = None) -> Path | None:
    """`messages` : chat en heure absolue (comme chat.jsonl), pour le contexte et l'incrustation."""
    start_dt = datetime.fromtimestamp(session["start_ts"])
    name = name or (f"{start_dt:%Y-%m-%d}_{session['channel']}_{index:02d}_"
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

    stream_title = (session.get("metadata") or {}).get("title") or ""
    words: list[subtitles.Word] = []
    if cfg.subtitles.enabled and meta["has_audio"]:
        wav = out_dir / "audio.wav"
        try:
            video.extract_audio(src, peak.start, dur, wav)
            words = subtitles.transcribe(wav, cfg.subtitles, title=stream_title)
        except Exception as exc:  # noqa: BLE001 - les sous-titres ne doivent jamais bloquer l'export
            log.warning("Sous-titres ignorés pour %s : %s", name, exc)
        finally:
            wav.unlink(missing_ok=True)

    chat = clip_chat(cfg, session, peak, messages or [])
    # Nom du streameur tel qu'il l'écrit (« JLTomy », « Etostark »), sinon le nom de la chaîne.
    streamer = ((session.get("metadata") or {}).get("author") or cfg.twitch.channel).strip("_")
    ctx = context.describe(cfg.context, streamer, stream_title,
                           subtitles.transcript_text(words), chat[:150], fixed_hashtags(cfg))
    if ctx:
        words = subtitles.apply_fixes(words, ctx["fixes"])
        hook = _shorten(ctx["title"], cfg.export.hook_max_chars)
    else:
        hook = make_hook(cfg, words, peak, stream_title)
    t_peak = peak.t_peak - peak.start  # moment du pic, en secondes dans le clip
    overlay = subtitles.pick_chat(chat, t_peak, cfg.export.chat_overlay)
    ass_path = None
    if words or overlay or (hook and cfg.export.hook_duration > 0):
        ass_path = out_dir / "subtitles.ass"
        ass_path.write_text(
            subtitles.build_ass(words, cfg.subtitles, cfg.vertical.width, cfg.vertical.height,
                                hook=hook, hook_duration=min(cfg.export.hook_duration, dur),
                                chat=overlay, chat_lines=cfg.export.chat_overlay,
                                chat_end=dur),
            encoding="utf-8")

    music = pick_music(cfg) if cfg.music.enabled else None
    tmp = out_dir / "video.part.mp4"
    video.render_clip(src, peak.start, dur, tmp, cfg.vertical, meta["width"], meta["height"],
                      ass_path=ass_path, has_audio=meta["has_audio"],
                      zoom_at=(peak.t_peak - peak.start) if cfg.vertical.peak_zoom else None,
                      music=music, music_level_db=cfg.music.level_db)
    tmp.replace(final)
    video.thumbnail(final, out_dir / "thumbnail.jpg", at=min(dur - 0.5, peak.t_peak - peak.start - 3))
    if cfg.export.keep_horizontal:
        video.render_horizontal(src, peak.start, dur, out_dir / "horizontal.mp4")

    transcript = subtitles.transcript_text(words)
    caption = make_caption(cfg, hook, stream_title, ctx)
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
        "hook": hook,
        "context": ctx,
        "music": music.name if music else None,
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
    peaks, meta, messages = find_peaks(cfg, session)
    log.info("%d pic(s) détecté(s) sur %s de live", len(peaks), fmt_time(meta["duration"]))
    outputs = []
    for i, peak in enumerate(peaks, 1):
        log.info("[%d/%d] %s → %s (score %.1f, %d msgs)", i, len(peaks), fmt_time(peak.start),
                 fmt_time(peak.end), peak.score, peak.n_messages)
        try:
            out = process_peak(cfg, session, meta, peak, i, messages=messages)
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
