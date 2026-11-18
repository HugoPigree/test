import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

START_TS = 1_763_400_000.0
BURSTS = [30, 125, 215]  # secondes dans le live où "il se passe un truc"


def synthetic_chat(duration=240, seed=1, bursts=BURSTS):
    rng = random.Random(seed)
    msgs = []
    users = [f"viewer{i}" for i in range(400)]
    normal = ["salut", "ça va ?", "gg", "tu joues à quoi", "bonsoir tomy", "first", "c'est qui lui"]
    hype = ["MDR", "KEKW", "PTDRRR", "CLIP CLIP", "ahahahah", "OMEGALUL", "jpp", "????"]
    t = 0.0
    while t < duration:
        t += rng.expovariate(1.0)  # ~1 msg/s en fond
        msgs.append({"ts": START_TS + t, "user": rng.choice(users), "msg": rng.choice(normal)})
    for b in bursts:
        # le chat réagit ~3 s après l'action, pendant ~12 s, ~8 msg/s
        for _ in range(100):
            dt = 3 + rng.random() * 12
            msgs.append({"ts": START_TS + b + dt, "user": rng.choice(users), "msg": rng.choice(hype)})
    msgs.sort(key=lambda m: m["ts"])
    return msgs


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    """Les tests n'appellent jamais le vrai Claude Code (lent, et demande un compte)."""
    from autoclip import context
    monkeypatch.setattr(context, "describe", lambda *a, **k: None)


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg requis")
    d = tmp_path / "recordings" / "jltomy_test"
    d.mkdir(parents=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=240",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=240",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-f", "mpegts",
        str(d / "stream.ts")], check=True)
    with (d / "chat.jsonl").open("w", encoding="utf-8") as fh:
        for m in synthetic_chat():
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        fh.write('{"ts": 17634')  # ligne tronquée (arrêt brutal) : doit être ignorée
    (d / "session.json").write_text(json.dumps({
        "channel": "jltomy", "start_ts": START_TS, "end_ts": START_TS + 240,
        "video": "stream.ts", "chat": "chat.jsonl", "metadata": {"title": "SOIRÉE CHILL"},
    }))
    return d
