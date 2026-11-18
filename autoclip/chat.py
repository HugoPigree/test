"""Enregistreur de chat Twitch (IRC anonyme, aucun compte requis).

Chaque message est écrit en JSONL : {"ts": <epoch secondes>, "user": ..., "msg": ...}.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import threading
import time
from contextlib import nullcontext
from pathlib import Path

log = logging.getLogger(__name__)

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6667


def parse_irc_line(line: str) -> dict | None:
    """Extrait un PRIVMSG d'une ligne IRC (avec ou sans tags IRCv3)."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        for item in raw_tags.split(";"):
            k, _, v = item.partition("=")
            tags[k] = v
    if " PRIVMSG " not in line:
        return None
    prefix, _, rest = line.partition(" PRIVMSG ")
    _channel, _, msg = rest.partition(" :")
    user = tags.get("display-name") or prefix.lstrip(":").split("!", 1)[0]
    ts = time.time()
    sent = tags.get("tmi-sent-ts")
    if sent and sent.isdigit():
        ts = int(sent) / 1000.0
    return {"ts": ts, "user": user, "msg": msg.strip()}


class ChatLogger(threading.Thread):
    def __init__(self, channel: str, out_path: Path | None, on_message=None):
        """Écrit les messages dans `out_path` (JSONL) et/ou les passe à `on_message(msg)`."""
        super().__init__(daemon=True, name=f"chat-{channel}")
        self.channel = channel.lower().lstrip("#")
        self.out_path = Path(out_path) if out_path else None
        self.on_message = on_message
        self._stop = threading.Event()
        self.count = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        backoff = 1.0
        with self.out_path.open("a", encoding="utf-8") if self.out_path else nullcontext() as out:
            while not self._stop.is_set():
                try:
                    self._session(out)
                    backoff = 1.0
                except OSError as exc:
                    if self._stop.is_set():
                        break
                    log.warning("Chat déconnecté (%s), reconnexion dans %.0fs", exc, backoff)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 60)

    def _session(self, out) -> None:
        nick = f"justinfan{random.randint(10000, 99999)}"
        with socket.create_connection((IRC_HOST, IRC_PORT), timeout=10) as sock:
            sock.settimeout(1.0)
            send = lambda s: sock.sendall((s + "\r\n").encode("utf-8"))  # noqa: E731
            send("CAP REQ :twitch.tv/tags")
            send("PASS SCHMOOPIIE")
            send(f"NICK {nick}")
            send(f"JOIN #{self.channel}")
            log.info("Chat connecté à #%s", self.channel)
            buf = b""
            last_rx = time.monotonic()
            while not self._stop.is_set():
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    if time.monotonic() - last_rx > 360:
                        raise OSError("aucune donnée depuis 6 min")
                    continue
                if not data:
                    raise OSError("connexion fermée par le serveur")
                last_rx = time.monotonic()
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", errors="replace")
                    if line.startswith("PING"):
                        send("PONG" + line[4:])
                        continue
                    if " RECONNECT" in line:
                        raise OSError("RECONNECT demandé par Twitch")
                    msg = parse_irc_line(line)
                    if msg:
                        if out:
                            out.write(json.dumps(msg, ensure_ascii=False) + "\n")
                        if self.on_message:
                            self.on_message(msg)
                        self.count += 1
                if out:
                    out.flush()


def load_chat(path: Path) -> list[dict]:
    messages = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # ligne tronquée (arrêt brutal)
    messages.sort(key=lambda m: m["ts"])
    return messages
