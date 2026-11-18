from autoclip.config import DetectionConfig
from autoclip.peaks import detect_peaks, message_weight

from conftest import BURSTS, START_TS, synthetic_chat


def test_detects_each_burst_and_starts_before_it():
    cfg = DetectionConfig()
    peaks = detect_peaks(synthetic_chat(), START_TS, 240, cfg)
    assert len(peaks) == len(BURSTS)
    for p, b in zip(peaks, BURSTS):
        assert b + 3 <= p.t_peak <= b + 16
        assert p.start <= b, "le clip doit commencer avant l'action"
        assert cfg.min_duration <= p.duration <= cfg.max_duration
        assert p.end <= 240


def test_quiet_chat_gives_no_peak():
    peaks = detect_peaks(synthetic_chat(bursts=[]), START_TS, 240, DetectionConfig())
    assert peaks == []


def test_max_clips_keeps_best():
    cfg = DetectionConfig(max_clips=1)
    assert len(detect_peaks(synthetic_chat(), START_TS, 240, cfg)) == 1


def test_spammer_counts_once():
    msgs = [{"ts": START_TS + 100 + i * 0.05, "user": "spam", "msg": "KEKW"} for i in range(80)]
    msgs += synthetic_chat(bursts=[])
    msgs.sort(key=lambda m: m["ts"])
    assert detect_peaks(msgs, START_TS, 240, DetectionConfig()) == []


def test_keyword_weight():
    kw = {"mdr", "kekw"}
    assert message_weight("MDR", kw, 1.5) == 2.5
    assert message_weight("ahahahah", kw, 1.5) == 2.5
    assert message_weight("ptdrrrr", kw, 1.5) == 2.5
    assert message_weight("bonsoir", kw, 1.5) == 1.0


def test_clip_duration_stays_between_min_and_max():
    # pre_roll de 30 s, y compris pour des réactions collées au début ou à la fin du live.
    cfg = DetectionConfig()
    assert cfg.pre_roll == 30
    for seed in range(5):
        for bursts in ([2, 100, 222], [40, 140], [10, 230]):
            for p in detect_peaks(synthetic_chat(seed=seed, bursts=bursts), START_TS, 240, cfg):
                assert cfg.min_duration <= p.duration <= cfg.max_duration
                assert 0 <= p.start <= p.t_peak <= p.end <= 240
