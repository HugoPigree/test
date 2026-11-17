from autoclip.config import SubtitlesConfig
from autoclip.subtitles import Word, build_ass, group_words, join_elisions


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


def test_french_elisions_are_glued():
    # Découpage réel renvoyé par Whisper sur un live : « c » + « 'est », « peut » + « -être ».
    words = join_elisions([Word(0.0, 0.1, "c"), Word(0.1, 0.3, "'est"),
                           Word(0.3, 0.5, "peut"), Word(0.5, 0.8, "-être")])
    assert [(w.text, w.start, w.end) for w in words] == [("c'est", 0.0, 0.3), ("peut-être", 0.3, 0.8)]


def test_groups_never_overlap():
    # « là » finit tôt : sa durée minimale (0,25 s) ne doit pas déborder sur la ligne suivante.
    words = [Word(0.0, 0.36, "Oh"), Word(0.36, 0.74, "bah"), Word(0.74, 0.80, "là."),
             Word(0.82, 0.92, "c'est"), Word(0.94, 1.19, "numéro")]
    ass = build_ass(words, SubtitlesConfig())
    events = [l.split(",") for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert events[2][2] == "0:00:00.82" and events[3][1] == "0:00:00.82"


def test_zero_length_words_do_not_overlap():
    # Cas réel : Whisper répète « Alba Fuchs. » avec des mots de durée nulle.
    words = [Word(1.98, 2.18, "Alba"), Word(2.18, 2.18, "Fuchs."),
             Word(2.18, 2.18, "Alba"), Word(2.18, 2.42, "Fuchs.")]
    ass = build_ass(words, SubtitlesConfig())
    times = [l.split(",")[1:3] for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert all(start < end for start, end in times)
    assert all(times[k][1] <= times[k + 1][0] for k in range(len(times) - 1))
