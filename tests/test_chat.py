from autoclip.chat import parse_irc_line


def test_parse_tagged_privmsg():
    line = ("@badge-info=;display-name=Toto;tmi-sent-ts=1700000000123 "
            ":toto!toto@toto.tmi.twitch.tv PRIVMSG #jltomy :MDR le mec")
    m = parse_irc_line(line)
    assert m == {"ts": 1700000000.123, "user": "Toto", "msg": "MDR le mec"}


def test_parse_untagged_and_ignore_other():
    m = parse_irc_line(":bob!bob@bob.tmi.twitch.tv PRIVMSG #jltomy :salut : ça va")
    assert m["user"] == "bob" and m["msg"] == "salut : ça va"
    assert parse_irc_line(":tmi.twitch.tv 001 justinfan123 :Welcome") is None
