"""Transcription Whisper (faster-whisper) et génération de sous-titres ASS façon TikTok."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from autoclip.config import SubtitlesConfig

log = logging.getLogger(__name__)

_MODEL = None
_MODEL_KEY = None


@dataclass
class Word:
    start: float
    end: float
    text: str


def _load_model(cfg: SubtitlesConfig):
    global _MODEL, _MODEL_KEY
    key = (cfg.model, cfg.device, cfg.compute_type)
    if _MODEL is None or _MODEL_KEY != key:
        from faster_whisper import WhisperModel  # import paresseux : dépendance lourde

        compute = cfg.compute_type
        if compute == "auto":
            compute = "default"
        log.info("Chargement du modèle Whisper %r (premier lancement = téléchargement)…", cfg.model)
        _MODEL = WhisperModel(cfg.model, device=cfg.device, compute_type=compute)
        _MODEL_KEY = key
    return _MODEL


def _read_wav(wav: Path):
    """Lit le wav 16 kHz mono 16 bits produit par ffmpeg (video.extract_audio).

    On ne laisse pas faster-whisper décoder le fichier : il passe par PyAV, et faster-whisper 1.2.1
    plante avec PyAV >= 15 (`open() got an unexpected keyword argument 'metadata_errors'`).
    """
    import wave

    import numpy as np

    with wave.open(str(wav), "rb") as fh:
        frames = fh.readframes(fh.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(wav: Path, cfg: SubtitlesConfig) -> list[Word]:
    model = _load_model(cfg)
    segments, _info = model.transcribe(
        _read_wav(wav), language=cfg.language or None, word_timestamps=True,
        vad_filter=True, beam_size=5, condition_on_previous_text=False,
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(float(w.start), float(w.end), text))
    return join_elisions(words)


def join_elisions(words: list[Word]) -> list[Word]:
    """Whisper coupe « c'est » en « c » + « 'est » (et « peut » + « -être ») : on recolle."""
    out: list[Word] = []
    for w in words:
        if out and w.text[0] in "'’-":
            prev = out[-1]
            out[-1] = Word(prev.start, w.end, prev.text + w.text)
        else:
            out.append(w)
    return out


def group_words(words: list[Word], per_line: int, max_chars: int) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        length = sum(len(x.text) + 1 for x in cur) + len(w.text)
        gap = (w.start - cur[-1].end) if cur else 0.0
        ends_sentence = bool(cur) and cur[-1].text[-1:] in ".?!…"
        if cur and (len(cur) >= per_line or length > max_chars or gap > 0.8 or ends_sentence):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000:d}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _bgr(color: str) -> str:
    """'&H00BBGGRR' (style) -> '&HBBGGRR&' (balise de surcharge)."""
    hexpart = color.upper().replace("&H", "").replace("&", "")
    return f"&H{hexpart[-6:]:0>6}&"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[Word], cfg: SubtitlesConfig, width: int = 1080, height: int = 1920) -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.font},{cfg.font_size},{cfg.color},{cfg.highlight_color},{cfg.outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{cfg.outline},2,2,60,60,{cfg.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    groups = group_words(words, cfg.words_per_line, cfg.max_chars)
    for g, group in enumerate(groups):
        texts = [(w.text.upper() if cfg.uppercase else w.text) for w in group]
        group_end = group[-1].end
        next_start = groups[g + 1][0].start if g + 1 < len(groups) else None
        for i, w in enumerate(group):
            # Chaque mot reste en surbrillance jusqu'au début du suivant.
            start = w.start
            end = group[i + 1].start if i + 1 < len(group) else max(group_end, w.start + 0.25)
            if next_start is not None:
                end = min(end, next_start)  # sinon deux lignes s'empilent un instant
            if end <= start:
                end = start + 0.05
            parts = []
            for j, t in enumerate(texts):
                t = _escape(t)
                if j == i:
                    parts.append(f"{{\\c{_bgr(cfg.highlight_color)}\\fscx108\\fscy108}}{t}{{\\r}}")
                else:
                    parts.append(t)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


def transcript_text(words: list[Word]) -> str:
    return " ".join(w.text for w in words).strip()
