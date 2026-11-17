"""Découpe ffmpeg, recadrage vertical 9:16 et incrustation des sous-titres."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from autoclip.config import VerticalConfig

log = logging.getLogger(__name__)


def _bin(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(f"{name} introuvable dans le PATH (installe ffmpeg).")
    return exe


def run(cmd: list[str], cwd: Path | None = None) -> None:
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg a échoué ({proc.returncode}) :\n{tail}")


def probe(path: Path) -> dict:
    cmd = [_bin("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if "mp4" in fmt.get("format_name", ""):
        # Certaines chaînes Twitch sont en fMP4 : Streamlink écrit alors des fragments MP4 qui ne
        # commencent pas à 0, et ffprobe compte la durée depuis 0 (pas depuis le début du fichier).
        # Le MPEG-TS n'a pas ce problème. (`-ss` de ffmpeg, lui, est bien relatif au début.)
        duration -= float(fmt.get("start_time") or 0)
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def vertical_filter(cfg: VerticalConfig, src_w: int, src_h: int) -> str:
    """Construit le filtergraph qui produit [v] en cfg.width x cfg.height."""
    W, H = cfg.width, cfg.height
    if cfg.layout == "crop":
        scaled_w = _even(src_w * H / src_h)
        x = f"(iw-{W})*{cfg.crop_x:.3f}"
        return f"[0:v]scale={scaled_w}:{H},crop={W}:{H}:{x}:0,setsar=1[v]"

    if cfg.layout == "split":
        fx, fy, fw, fh = cfg.facecam
        cam_h = _even(H * cfg.facecam_ratio)
        game_h = H - cam_h
        # Facecam : on recadre la zone indiquée au ratio cible puis on scale.
        cam = (f"[c]crop=iw*{fw:.4f}:ih*{fh:.4f}:iw*{fx:.4f}:ih*{fy:.4f},"
               f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam]")
        game_scaled_w = _even(src_w * game_h / src_h)
        if game_scaled_w < W:
            game = f"[g]scale={W}:{game_h}:force_original_aspect_ratio=increase,crop={W}:{game_h}[game]"
        else:
            game = (f"[g]scale={game_scaled_w}:{game_h},"
                    f"crop={W}:{game_h}:(iw-{W})*{cfg.game_x:.3f}:0[game]")
        return (f"[0:v]split=2[c][g];{cam};{game};"
                f"[cam][game]vstack=inputs=2,setsar=1[v]")

    if cfg.layout != "blur":
        raise ValueError(f"layout inconnu : {cfg.layout!r} (blur, crop ou split)")
    # Fond : vidéo agrandie + floutée ; devant : vidéo entière (légèrement zoomée).
    fg_w = _even(W * cfg.blur_zoom)
    fg_h = _even(fg_w * src_h / src_w)
    if fg_h > H:
        fg_h = H
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
        f"crop={W // 4}:{H // 4},boxblur=10:2,scale={W}:{H},eq=brightness=-0.08[bgb];"
        f"[fg]scale={fg_w}:{fg_h},crop=min({W}\\,iw):ih[fgc];"
        f"[bgb][fgc]overlay=(W-w)/2:(H-h)/2*0.85,setsar=1[v]"
    )


def extract_audio(src: Path, start: float, duration: float, out_wav: Path) -> None:
    run([_bin("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src),
         "-t", f"{duration:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
         str(out_wav)])


def render_clip(src: Path, start: float, duration: float, out: Path, cfg: VerticalConfig,
                src_w: int, src_h: int, ass_path: Path | None = None,
                has_audio: bool = True) -> None:
    """Un seul encodage : découpe précise + 9:16 + sous-titres incrustés."""
    graph = vertical_filter(cfg, src_w, src_h)
    assert graph.endswith("[v]")
    tail = f",fps={cfg.fps}"
    if ass_path is not None:
        # Nom relatif + cwd => pas de souci d'échappement des chemins Windows (C:\...).
        tail += f",ass={ass_path.name}"
    graph = f"{graph[:-3]}{tail}[v]"

    cmd = [_bin("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(Path(src).resolve()),
           "-t", f"{duration:.3f}", "-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
            str(Path(out).resolve())]
    run(cmd, cwd=ass_path.parent if ass_path is not None else None)


def render_horizontal(src: Path, start: float, duration: float, out: Path) -> None:
    run([_bin("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src),
         "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac",
         "-movflags", "+faststart", str(out)])


def thumbnail(video: Path, out: Path, at: float) -> None:
    run([_bin("ffmpeg"), "-y", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "3", str(out)])
