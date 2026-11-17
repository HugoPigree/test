from autoclip.config import SubtitlesConfig
from autoclip.subtitles import Word, build_ass, group_words


WORDS = [Word(0.0, 0.3, "Non"), Word(0.3, 0.6, "mais"), Word(0.6, 0.9, "attends"),
         Word(0.9, 1.2, "regarde"), Word(2.5, 2.8, "{incroyable}.")]


def test_grouping_respects_limits_and_pauses():
    groups = group_words(WORDS, per_line=3, max_chars=20)
    assert [len(g) for g in groups] == [3, 1, 1]


def test_ass_has_one_event_per_word_and_highlight():
    ass = build_ass(WORDS, SubtitlesConfig())
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == len(WORDS)
    assert "PlayResY: 1920" in ass
    assert "\\c&H00E5FF&" in events[0] and "NON" in events[0]
    assert "{INCROYABLE}" not in ass  # accolades neutralisées
    assert events[0].split(",")[1] == "0:00:00.00"
