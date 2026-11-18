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


def peak_zoom_filter(width: int, height: int, at: float, amount: float = 0.08) -> str:
    """Zoom de `amount` centré : monte en 0,3 s jusqu'à `at`, tient ~2 s, redescend en 0,3 s."""
    a, b = at - 0.3, at + 2.0
    z = f"(1+{amount}*clip((t-{a:.3f})/0.3\\,0\\,1)*clip(({b:.3f}-t)/0.3\\,0\\,1))"
    return (f"scale=w=trunc({width}*{z}/2)*2:h=trunc({height}*{z}/2)*2:eval=frame,"
            f"crop={width}:{height}")


# LRA=5 (au lieu de 11) : son du live plus égal. Sur un clip Etostark avec de longs passages
# calmes, la musique s'approchait à moins de 10 dB du live 15 s sur 38 ; avec LRA=5 : 1 s.
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=5"
STEREO = "aformat=sample_rates=48000:channel_layouts=stereo"


def music_mix_filter(live: str, music: str, level_db: float) -> str:
    """Son du live ([live]) + musique ([music]) -> [a].

    Chaque morceau est d'abord normalisé à -14 LUFS comme le live, puis baissé de `level_db` :
    le niveau ne dépend plus du mastering du morceau. Le ducking est léger (ratio 3) : avec
    l'ancien réglage (ratio 8), la musique remontait dès que le live se calmait et passait
    par moments devant (mesuré sur un clip Etostark : jusqu'à 3 dB au-dessus du live).
    """
    return (f"[{live}]{LOUDNORM},{STEREO},asplit=2[voice][sc0]"
            f";[sc0]highpass=f=300,lowpass=f=3400[sc]"
            f";[{music}]loudnorm=I=-14:TP=-1.5:LRA=7,{STEREO},volume={level_db}dB[mus]"
            f";[mus][sc]sidechaincompress=threshold=0.05:ratio=3:attack=50:release=800[duck]"
            f";[voice][duck]amix=inputs=2:duration=first:normalize=0[a]")


def render_clip(src: Path, start: float, duration: float, out: Path, cfg: VerticalConfig,
                src_w: int, src_h: int, ass_path: Path | None = None,
                has_audio: bool = True, zoom_at: float | None = None,
                music: Path | None = None, music_level_db: float = -26.0) -> None:
    """Un seul encodage : découpe précise + 9:16 (+ zoom au pic) + sous-titres (+ musique)."""
    graph = vertical_filter(cfg, src_w, src_h)
    assert graph.endswith("[v]")
    tail = f",fps={cfg.fps}"
    if zoom_at is not None:
        tail += "," + peak_zoom_filter(cfg.width, cfg.height, zoom_at)
    if ass_path is not None:
        # Nom relatif + cwd => pas de souci d'échappement des chemins Windows (C:\...).
        tail += f",ass={ass_path.name}"
    graph = f"{graph[:-3]}{tail}[v]"

    loudnorm = LOUDNORM
    cmd = [_bin("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(Path(src).resolve())]
    if has_audio and music is not None:
        # Musique en boucle, un peu baissée quand la bande de la voix (300-3400 Hz) du live est forte.
        cmd += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        graph += ";" + music_mix_filter("0:a:0", "1:a:0", music_level_db)
    # -t après toutes les entrées : il s'applique à la sortie (la musique boucle à l'infini).
    cmd += ["-t", f"{duration:.3f}", "-filter_complex", graph, "-map", "[v]"]
    if has_audio and music is not None:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    elif has_audio:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-af", loudnorm]
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
