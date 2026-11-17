"""Détection des pics d'activité du chat.

Principe :
1. chaque message vaut 1 point (+ bonus s'il contient un mot de hype : "mdr", "KEKW", "CLIP"…) ;
   un même viewer ne compte qu'une fois par tranche de 5 s (anti-spam) ;
2. on calcule l'activité sur une fenêtre glissante (10 s par défaut) ;
3. on la compare à une ligne de base robuste (médiane glissante sur 5 min + MAD) ;
4. les maxima locaux qui dépassent le seuil deviennent des pics, espacés d'au moins `min_gap` ;
5. le clip démarre avant le début de la montée du chat (le chat réagit APRÈS l'action).
"""

from __future__ import annotations

import bisect
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from statistics import median

from autoclip.config import DetectionConfig

_LAUGH_RE = re.compile(r"^(?:(?:ha|ah|he|eh|xd)+h?|x*p+t+d+r+|m+d+r+|l+o+l+|j+p+p+|\?{3,}|!{3,})$")
_TOKEN_RE = re.compile(r"[\w+?!]+", re.UNICODE)


@dataclass
class Peak:
    t_peak: float
    start: float
    end: float
    score: float
    activity: float
    baseline: float
    n_messages: int
    sample: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)


def message_weight(msg: str, keywords: set[str], keyword_weight: float) -> float:
    for tok in _TOKEN_RE.findall(msg.lower()):
        if tok in keywords or _LAUGH_RE.match(tok):
            return 1.0 + keyword_weight
    return 1.0


def _rolling_stat(values: list[float], half: int, step: int) -> tuple[list[float], list[float]]:
    """Médiane et MAD glissantes centrées, calculées tous les `step` points puis interpolées."""
    n = len(values)
    if n == 0:
        return [], []
    knots = list(range(0, n, step))
    if knots[-1] != n - 1:
        knots.append(n - 1)
    meds, mads = [], []
    for k in knots:
        win = values[max(0, k - half): min(n, k + half + 1)]
        m = median(win)
        meds.append(m)
        mads.append(median(abs(v - m) for v in win))

    def interp(knot_vals: list[float]) -> list[float]:
        out = [0.0] * n
        for i in range(len(knots) - 1):
            a, b = knots[i], knots[i + 1]
            va, vb = knot_vals[i], knot_vals[i + 1]
            span = max(1, b - a)
            for j in range(a, b + 1):
                out[j] = va + (vb - va) * (j - a) / span
        if len(knots) == 1:
            out = [knot_vals[0]] * n
        return out

    return interp(meds), interp(mads)


def activity_series(messages: list[dict], start_ts: float, duration: float,
                    cfg: DetectionConfig) -> tuple[list[float], list[float], list[float]]:
    """Renvoie (temps relatifs des messages, poids par seconde, activité lissée)."""
    n = max(1, int(math.ceil(duration)))
    keywords = {k.lower() for k in cfg.keywords}
    per_sec = [0.0] * n
    seen: set[tuple[str, int]] = set()
    times = []
    for m in messages:
        t = m["ts"] - start_ts - cfg.chat_offset
        if t < 0 or t >= n:
            continue
        times.append(t)
        key = (m.get("user", ""), int(t // 5))
        if key in seen:
            continue
        seen.add(key)
        per_sec[int(t)] += message_weight(m.get("msg", ""), keywords, cfg.keyword_weight)

    half_w = max(1, int(round(cfg.window / 2)))
    csum = [0.0]
    for v in per_sec:
        csum.append(csum[-1] + v)
    act = [csum[min(n, i + half_w)] - csum[max(0, i - half_w)] for i in range(n)]
    return times, per_sec, act


def detect_peaks(messages: list[dict], start_ts: float, duration: float,
                 cfg: DetectionConfig) -> list[Peak]:
    if duration <= 0 or not messages:
        return []
    times, _per_sec, act = activity_series(messages, start_ts, duration, cfg)
    n = len(act)
    half_b = max(5, int(cfg.baseline_window / 2))
    base, mad = _rolling_stat(act, half_b, step=5)

    def scale(i: int) -> float:
        # Plancher "Poisson" pour ne pas exploser quand le chat est très calme.
        return max(1.4826 * mad[i], math.sqrt(max(base[i], 0.0)) + 1.0)

    z = [(act[i] - base[i]) / scale(i) for i in range(n)]

    candidates = []
    for i in range(n):
        if z[i] < cfg.threshold or act[i] < cfg.min_activity:
            continue
        lo, hi = max(0, i - 3), min(n, i + 4)
        if act[i] == max(act[lo:hi]):
            candidates.append(i)

    chosen: list[int] = []
    for i in sorted(candidates, key=lambda k: z[k], reverse=True):
        if all(abs(i - j) >= cfg.min_gap for j in chosen):
            chosen.append(i)
        if len(chosen) >= cfg.max_clips:
            break

    msgs_rel = sorted(
        ((m["ts"] - start_ts - cfg.chat_offset, m.get("msg", "")) for m in messages),
        key=lambda x: x[0],
    )
    rel_times = [t for t, _ in msgs_rel]

    peaks = []
    for i in sorted(chosen):
        # Début de la montée : on remonte tant que l'activité reste nettement au-dessus de la base.
        level = base[i] + 0.3 * (act[i] - base[i])
        onset = i
        while onset > 0 and i - onset < 45 and act[onset - 1] > level:
            onset -= 1
        start = onset - cfg.pre_roll
        end = i + cfg.post_roll
        if end - start > cfg.max_duration:
            start = end - cfg.max_duration
        if end - start < cfg.min_duration:
            start = end - cfg.min_duration
        start = max(0.0, start)
        end = min(float(duration), end)
        if end - start < cfg.min_duration:
            end = min(float(duration), start + cfg.min_duration)

        a, b = bisect.bisect_left(rel_times, i - cfg.window), bisect.bisect_right(rel_times, i + cfg.window)
        window_msgs = [msg for _, msg in msgs_rel[a:b]]
        sample = [m for m, _ in Counter(window_msgs).most_common(5)]
        peaks.append(Peak(
            t_peak=float(i), start=round(start, 2), end=round(end, 2),
            score=round(z[i], 2), activity=round(act[i], 1), baseline=round(base[i], 1),
            n_messages=len(window_msgs), sample=sample,
        ))
    return peaks


def ascii_timeline(act: list[float], peaks: list[Peak], width: int = 100) -> str:
    """Petite visualisation texte de l'activité du chat (pour régler les seuils)."""
    if not act:
        return ""
    n = len(act)
    bucket = max(1, math.ceil(n / width))
    cols = [max(act[i:i + bucket]) for i in range(0, n, bucket)]
    top = max(cols) or 1
    bars = " ▁▂▃▄▅▆▇█"
    line = "".join(bars[min(8, int(c / top * 8))] for c in cols)
    marks = [" "] * len(cols)
    for p in peaks:
        marks[min(len(cols) - 1, int(p.t_peak) // bucket)] = "^"
    return line + "\n" + "".join(marks)


def fmt_time(t: float) -> str:
    t = int(t)
    return f"{t // 3600:d}:{t % 3600 // 60:02d}:{t % 60:02d}"
