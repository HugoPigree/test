import json
import subprocess

from autoclip import context
from autoclip.config import ContextConfig

# Le vrai `describe` (conftest le remplace par défaut pour ne jamais lancer Claude Code).
REAL_DESCRIBE = context.describe


def fake_run(stdout):
    calls = {}

    def run(cmd, **kwargs):
        calls.update(cmd=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    return run, calls


def test_describe_parses_structured_output(monkeypatch, tmp_path):
    exe = tmp_path / "claude.exe"
    exe.write_text("")
    out = {"title": "JLTomy se fait recaler par le videur.", "caption": "Le videur a pas hésité",
           "hashtags": ["#Miami", "irl"], "fixes": [{"wrong": "walla", "right": "wallah"}]}
    run, calls = fake_run(json.dumps({"is_error": False, "structured_output": out}))
    monkeypatch.setattr(context.subprocess, "run", run)
    cfg = ContextConfig(claude_path=str(exe))
    res = REAL_DESCRIBE(cfg, "jltomy", "SUBATHON", "walla il m'a recalé",
                        [(12.0, "viewer", "KEKW")], ["jltomy"])
    assert res == {"title": "JLTomy se fait recaler par le videur", "caption": "Le videur a pas hésité",
                   "hashtags": ["miami", "irl"], "fixes": {"walla": "wallah"}}
    cmd = calls["cmd"]
    assert cmd[:2] == [str(exe), "-p"] and cmd[cmd.index("--tools") + 1] == ""
    assert "[ 12.0s] viewer: KEKW" in calls["input"]


def test_describe_returns_none_when_claude_fails(monkeypatch, tmp_path):
    exe = tmp_path / "claude.exe"
    exe.write_text("")
    cfg = ContextConfig(claude_path=str(exe))
    run, _ = fake_run(json.dumps({"is_error": True, "result": "Not logged in"}))
    monkeypatch.setattr(context.subprocess, "run", run)
    assert REAL_DESCRIBE(cfg, "jltomy", "", "", [], []) is None
    run, _ = fake_run("pas du json")
    monkeypatch.setattr(context.subprocess, "run", run)
    assert REAL_DESCRIBE(cfg, "jltomy", "", "", [], []) is None
    assert REAL_DESCRIBE(ContextConfig(claude_path=str(tmp_path / "absent")), "jltomy", "", "", [], []) is None
    assert REAL_DESCRIBE(ContextConfig(enabled=False), "jltomy", "", "", [], []) is None
