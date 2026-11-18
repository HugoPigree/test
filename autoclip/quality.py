"""Score de qualité (0 à 100) d'une candidate, avec le détail de chaque signal.

Signaux (chacun ramené entre 0 et 1, pondéré par `[quality] weight_*`) :
- chat     : force du pic de chat (score robuste de la détection) ;
- viewers  : nombre de viewers distincts qui écrivent autour du pic ;
- voix     : pic de volume de la voix (bande 300-3400 Hz, ffmpeg) autour du moment, par rapport
             au reste du clip ;
- parole   : densité de parole (mots par seconde, Whisper) ;
- phrase   : le clip commence au début d'une phrase (Whisper).
Pénalités (points retirés) : durée hors 15-60 s, trop de silence, début de live ou reprise après
coupure, pause (presque personne ne parle), proximité avec une meilleure candidate.

Point d'entrée pour une IA plus tard : `[quality] external = "module:fonction"`. La fonction reçoit
(candidate, mesures) et renvoie None ou {"points": 0..1, "detail": "texte"} ; elle compte avec le
poids `weight_external`. Rien d'externe n'est appelé par défaut.
"""

from __future__ import annotations

import importlib
import logging
import re
import subprocess
from pathlib import Path

from autoclip import subtitles, video
from autoclip.config import Config

log = logging.getLogger(__name__)

SIGNALS = ["chat", "viewers", "voix", "parole", "phrase"]


def voice_loudness(raw: Path, start: float, dur: float) -> list[float]:
    """Loudness « momentary » (0,4 s) de la bande de la voix, une valeur toutes les 0,1 s."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(start, 0.0):.3f}", "-t", f"{dur:.3f}", "-i", str(raw),
         "-vn", "-af", "highpass=f=300,lowpass=f=3400,ebur128=metadata=1,"
                       "ametadata=print:key=lavfi.r128.M:file=-", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [max(float(v), -70.0) for v in re.findall(r"lavfi\.r128\.M=(-?[\d.]+|-inf)", proc.stdout)
            if v != "-inf"]


def measure(cfg: Config, c: dict) -> dict:
    """Mesures brutes d'une candidate (ffmpeg + Whisper), gardées dans son .json."""
    q = cfg.quality
    raw = c["_json"].parent / c["raw"]
    p = c["peak"]
    start, end, t_peak = p["start"], p["end"], p["t_peak"]
    dur = end - start
    out: dict = {"duree": round(dur, 1)}

    # Voix : pic autour du moment (15 s avant le pic de chat -> 2 s après) contre la médiane du clip.
    lv = voice_loudness(raw, start, dur)
    if lv:
        a, b = int(max(0.0, t_peak - start - 15) * 10), int(min(dur, t_peak - start + 2) * 10)
        around = lv[a:b] or lv
        out["voix_db"] = round(max(around) - sorted(lv)[len(lv) // 2], 1)
        out["silence"] = round(sum(v < q.silence_lufs for v in lv) / len(lv), 2)

    # Parole : on transcrit 5 s de plus avant le début pour savoir si le clip coupe une phrase.
    words: list[subtitles.Word] = []
    if cfg.subtitles.enabled:
        wav = raw.with_suffix(".qualite.wav")
        lead = min(5.0, start)
        try:
            video.extract_audio(raw, start - lead, dur + lead, wav)
            words = subtitles.transcribe(wav, cfg.subtitles, title=(c.get("metadata") or {}).get("title", ""))
        except Exception as exc:  # noqa: BLE001 - un signal manquant ne bloque pas le score
            log.warning("Whisper indisponible pour %s : %s", c["name"], exc)
            words = None
        finally:
            wav.unlink(missing_ok=True)
        if words is not None:
            inside = [w for w in words if w.start >= lead - 0.05]
            out["mots"] = len(inside)
            out["mots_par_s"] = round(len(inside) / max(dur, 1.0), 2)
            out["debut_phrase"] = starts_sentence(words, lead)
            out["debut"] = " ".join(w.text for w in inside[:8])
    return out


def starts_sentence(words: list[subtitles.Word], cut: float, pause: float = 0.7) -> bool:
    """Le clip (qui commence à `cut` s dans la transcription) démarre-t-il sur une phrase ?

    Oui si la coupe tombe dans un silence d'au moins `pause` s, ou juste après une fin de phrase
    (. ? ! …), sans couper de mot. Non si elle tombe au milieu d'un mot ou d'une phrase.
    """
    before = [w for w in words if w.end <= cut + 0.05]
    after = [w for w in words if w.start >= cut - 0.05]
    if any(w.start < cut - 0.05 < w.end - 0.05 for w in words):
        return False  # mot coupé en deux
    if not before:
        return True
    if not after:
        return False
    return after[0].start - before[-1].end >= pause or before[-1].text[-1:] in ".?!…"


def distinct_viewers(c: dict, window: float = 15.0) -> int:
    t = c["t_peak_abs"]
    return len({m.get("user") for m in c.get("chat", []) if abs(m["ts"] - t) <= window})


def _external(cfg: Config, c: dict, mesures: dict) -> dict | None:
    if not cfg.quality.external:
        return None
    module, _, func = cfg.quality.external.partition(":")
    try:
        res = getattr(importlib.import_module(module), func)(c, mesures)
    except Exception as exc:  # noqa: BLE001 - une IA en panne ne bloque pas le tri
        log.warning("Score externe %s indisponible : %s", cfg.quality.external, exc)
        return None
    return res if isinstance(res, dict) and "points" in res else None


def score(cfg: Config, c: dict, others: list[dict] | None = None) -> dict:
    """{"total": 0-100, "signaux": {...}, "penalites": {...}} à partir de c["mesures"]."""
    q = cfg.quality
    m = c.get("mesures") or {}
    sig: dict = {}

    def add(name: str, weight: float, ratio: float | None, valeur: str) -> None:
        r = 0.0 if ratio is None else max(0.0, min(1.0, ratio))
        sig[name] = {"valeur": valeur, "points": round(weight * r, 1), "max": weight}

    viewers = distinct_viewers(c)
    add("chat", q.weight_chat, c["score"] / q.chat_score_full, f"pic {c['score']:.1f}")
    add("viewers", q.weight_viewers, viewers / q.viewers_full, f"{viewers} viewers distincts")
    vdb = m.get("voix_db")
    add("voix", q.weight_voice, None if vdb is None else vdb / q.voice_db_full,
        "inconnu" if vdb is None else f"+{vdb:.1f} dB au moment fort")
    wps = m.get("mots_par_s")
    add("parole", q.weight_speech, None if wps is None else wps / q.speech_wps_full,
        "inconnu" if wps is None else f"{wps:.1f} mots/s")
    ds = m.get("debut_phrase")
    add("phrase", q.weight_sentence, None if ds is None else float(ds),
        "inconnu" if ds is None else ("début de phrase" if ds else "coupe une phrase"))
    ext = _external(cfg, c, m)
    if ext is not None:
        add("externe", q.weight_external, float(ext["points"]), str(ext.get("detail", "")))

    pen: dict = {}
    dur = m.get("duree", c.get("duration", 0.0))
    if not 15 <= dur <= 60:
        pen["duree"] = {"valeur": f"{dur:.0f} s (hors 15-60 s)", "points": -q.penalty_duration}
    if m.get("silence", 0.0) > q.silence_max:
        pen["silence"] = {"valeur": f"{m['silence']:.0%} sans voix", "points": -q.penalty_silence}
    since = c["t_peak_abs"] - (c.get("connexion_debut") or 0.0)
    if c.get("connexion_debut") and since < q.live_start_minutes * 60:
        pen["debut_live"] = {"valeur": f"{since / 60:.1f} min après le début du live ou une coupure",
                             "points": -q.penalty_live_start}
    if m.get("mots") is not None and m["mots"] < q.pause_max_words:
        pen["pause"] = {"valeur": f"{m['mots']} mots en {dur:.0f} s", "points": -q.penalty_pause}
    gap = cfg.daily.min_gap_minutes * 60
    better = [o for o in others or [] if o is not c and o["channel"] == c["channel"]
              and abs(o["t_peak_abs"] - c["t_peak_abs"]) < gap and base(cfg, o) > base_of(sig)]
    if better:
        near = min(better, key=lambda o: abs(o["t_peak_abs"] - c["t_peak_abs"]))
        pen["proximite"] = {"valeur": f"à {abs(near['t_peak_abs'] - c['t_peak_abs']) / 60:.0f} min "
                                      f"d'une meilleure candidate ({near['name']})",
                            "points": -q.penalty_nearby}
    total = base_of(sig) + sum(p["points"] for p in pen.values())
    return {"total": int(round(max(0.0, min(100.0, total)))), "base": round(base_of(sig), 1),
            "signaux": sig, "penalites": pen}


def base_of(sig: dict) -> float:
    """Somme des points ramenée sur 100 (les poids n'ont pas besoin de faire 100)."""
    wmax = sum(s["max"] for s in sig.values()) or 1.0
    return 100.0 * sum(s["points"] for s in sig.values()) / wmax


def base(cfg: Config, c: dict) -> float:
    """Score avant pénalités (sans la proximité, pour comparer deux candidates)."""
    return score(cfg, c, None)["base"]


def summary(q: dict) -> str:
    """Une ligne lisible : « chat 25/25 · viewers 9/15 · … · durée -20 »."""
    parts = [f"{k} {v['points']:g}/{v['max']:g} ({v['valeur']})" for k, v in q["signaux"].items()]
    parts += [f"{k} {v['points']:g} ({v['valeur']})" for k, v in q["penalites"].items()]
    return " · ".join(parts)
