"""Transcription Whisper (faster-whisper) et génération de sous-titres ASS façon TikTok."""

from __future__ import annotations

import logging
import re
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


def _clean_title(title: str) -> str:
    """Titre du live sans les commandes (« !subathon ») ni les crochets : des noms propres pour Whisper."""
    title = re.sub(r"[\[\]()|]", " ", title)
    return " ".join(w for w in title.split() if not w.startswith("!"))


def transcribe(wav: Path, cfg: SubtitlesConfig, title: str = "") -> list[Word]:
    model = _load_model(cfg)
    hotwords = " ".join(x for x in (cfg.vocabulary, _clean_title(title)) if x) or None
    segments, _info = model.transcribe(
        _read_wav(wav), language=cfg.language or None, word_timestamps=True,
        vad_filter=True, beam_size=5, condition_on_previous_text=False, hotwords=hotwords,
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(float(w.start), float(w.end), text))
    return apply_fixes(join_elisions(words), cfg.fixes)


def apply_fixes(words: list[Word], fixes: dict[str, str]) -> list[Word]:
    """Remplace des mots entiers (sans tenir compte des majuscules), ponctuation gardée."""
    table = {k.lower(): v for k, v in fixes.items()}
    out = []
    for w in words:
        m = re.fullmatch(r"(\W*)(.*?)(\W*)", w.text)
        lead, core, trail = m.groups()
        if core.lower() in table:
            w = Word(w.start, w.end, lead + table[core.lower()] + trail)
        out.append(w)
    return out


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


BOTS = {"nightbot", "streamelements", "wizebot", "moobot", "streamlabs", "fossabot", "sery_bot"}
# Couleurs de pseudo façon Twitch (ASS : &HBBGGRR&).
CHAT_COLORS = ["&H5A5AFF&", "&HFF9E1E&", "&H32CD32&", "&H00A5FF&", "&HFF69B4&", "&HFFD700&",
               "&H7FFF00&", "&H1E90FF&"]
_NO_GLYPH = re.compile("[\U00010000-\U0010FFFF☀-➿️‍]")  # emojis : pas de glyphe


def pick_chat(chat: list[tuple[float, str, str]], t_peak: float, n: int,
              max_len: int = 34, spacing: float = 0.6) -> list[tuple[float, str, str]]:
    """Messages à incruster : à partir de ~8 s avant le pic, courts, sans bots ni commandes,
    espacés d'au moins `spacing` secondes (sinon on ne peut pas les lire)."""
    if n <= 0:
        return []
    out: list[tuple[float, str, str]] = []
    for t, user, msg in chat:
        msg = " ".join(_NO_GLYPH.sub("", msg).split())
        if (t < t_peak - 8 or not msg or msg.startswith("!") or "http" in msg
                or len(msg) > max_len or user.lower() in BOTS):
            continue
        if out and t - out[-1][0] < spacing:
            continue
        out.append((t, user, msg))
    return out[:14]


def _chat_events(chat: list[tuple[float, str, str]], lines: int, end: float,
                 height: int) -> list[str]:
    """Le chat défile en bas à gauche : le nouveau message glisse depuis la gauche, les autres
    montent d'un cran."""
    x, bottom, step = 60, height - 110, 70
    out = []
    for k, (t, _u, _m) in enumerate(chat):
        t_next = chat[k + 1][0] if k + 1 < len(chat) else end
        if t_next <= t:
            continue
        shown = chat[max(0, k - lines + 1):k + 1]
        for slot, (_t, user, msg) in enumerate(reversed(shown)):  # slot 0 = en bas (le plus récent)
            y = bottom - slot * step
            color = CHAT_COLORS[sum(map(ord, user)) % len(CHAT_COLORS)]
            if slot == 0:
                anim = f"\\move({x - 40},{y},{x},{y},0,150)\\fad(150,0)"
            else:
                anim = f"\\move({x},{y + step},{x},{y},0,150)"
            out.append(f"Dialogue: 2,{_ts(t)},{_ts(t_next)},Chat,,0,0,0,,"
                       f"{{\\an1{anim}}}{{\\c{color}}}{_escape(user)}{{\\c&HFFFFFF&}}: {_escape(msg)}")
    return out


def build_ass(words: list[Word], cfg: SubtitlesConfig, width: int = 1080, height: int = 1920,
              hook: str = "", hook_duration: float = 0.0,
              chat: list[tuple[float, str, str]] | None = None, chat_lines: int = 4,
              chat_end: float = 0.0) -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.font},{cfg.font_size},{cfg.color},{cfg.highlight_color},{cfg.outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{cfg.outline},2,2,60,60,{cfg.margin_v},1
Style: Hook,{cfg.font},74,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,260,1
Style: Chat,Arial,44,&H00FFFFFF,&H00FFFFFF,&H40101010,&H00000000,-1,0,0,0,100,100,0,0,3,10,0,1,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook and hook_duration > 0:
        # Titre : texte noir sur bandeau blanc, en haut ; arrive en rebondissant, part en fondu.
        pop = "\\fscx60\\fscy60\\t(0,180,\\fscx106\\fscy106)\\t(180,300,\\fscx100\\fscy100)"
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_duration)},Hook,,0,0,0,,"
                     f"{{\\fad(0,250){pop}}}{_escape(hook)}")
    if chat:
        lines += _chat_events(chat, chat_lines, chat_end, height)
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
                continue  # mot de durée nulle (Whisper tasse les répétitions) : rien à afficher
            parts = []
            for j, t in enumerate(texts):
                t = _escape(t)
                if j == i:
                    # Le mot prononcé « saute » : 85 % -> 120 % -> 108 %.
                    pop = "\\fscx85\\fscy85\\t(0,80,\\fscx120\\fscy120)\\t(80,160,\\fscx108\\fscy108)"
                    parts.append(f"{{\\c{_bgr(cfg.highlight_color)}{pop}}}{t}{{\\r}}")
                else:
                    parts.append(t)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


def transcript_text(words: list[Word]) -> str:
    return " ".join(w.text for w in words).strip()
