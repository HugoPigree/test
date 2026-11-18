"""Contexte du clip écrit par Claude (Claude Code en mode `claude -p`, avec l'abonnement de
l'utilisateur, sans clé API) : un titre qui explique la situation, une légende TikTok qui
sonne humain, et la correction des mots mal compris par Whisper.

Si Claude Code est absent, pas connecté ou trop lent, on renvoie None et le pipeline reprend
l'ancienne accroche (phrase dite au moment du pic).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from autoclip.config import ContextConfig

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "fixes": {"type": "array", "items": {
            "type": "object",
            "properties": {"wrong": {"type": "string"}, "right": {"type": "string"}},
            "required": ["wrong", "right"]}},
    },
    "required": ["title", "caption", "hashtags", "fixes"],
}

SYSTEM = """Tu montes des clips TikTok à partir du live Twitch du streameur français {channel}.
On te donne le titre du live, la transcription automatique (Whisper) du clip et les messages
du chat au moment où il s'emballe. Tu dois faire comprendre en une seconde ce qui se passe à
quelqu'un qui ne regarde pas le live.

- title : ce qui se passe, au présent, comme un clippeur humain (« {channel} se fait recaler par
  le videur », « Le chat lui fait croire qu'il a perdu 10 000 $ »). {title_max} caractères maximum,
  pas d'emoji, pas de guillemets, pas de point final. Factuel : n'invente rien que la
  transcription ou le chat ne montre pas. Pas de mots creux (« incroyable », « épique »,
  « moment fou », « vous n'allez pas y croire »).
- caption : une phrase courte, ton familier d'un vrai compte de clips français, 0 ou 1 emoji.
  Pas de hashtags dedans.
- hashtags : 3 à 5 hashtags liés au contenu précis (jeu, lieu, situation), sans #, en minuscules,
  sans ceux-ci : {fixed_tags}.
- fixes : les mots de la transcription mal écrits par Whisper, avec la bonne écriture. Surtout
  l'argot, les mots arabes (wallah, starfoullah, hamdoulah…), l'anglais, les noms propres.
  « wrong » est un mot exact de la transcription, « right » un seul mot. Seulement si tu es sûr ;
  sinon liste vide."""


def find_claude(cfg: ContextConfig) -> str | None:
    if cfg.claude_path:
        return cfg.claude_path if Path(cfg.claude_path).exists() else None
    found = shutil.which("claude")
    if found:
        return found
    # Installeur officiel sous Windows : ~/.local/bin n'est pas toujours dans le PATH.
    for name in ("claude.exe", "claude"):
        local = Path.home() / ".local" / "bin" / name
        if local.exists():
            return str(local)
    return None


def build_prompt(channel: str, stream_title: str, transcript: str, chat: list[tuple[float, str, str]]) -> str:
    lines = [f"Chaîne : {channel}", f"Titre du live : {stream_title or '(inconnu)'}", "",
             "Transcription du clip :", transcript or "(personne ne parle)", "",
             "Chat pendant le clip (secondes depuis le début du clip, pseudo, message) :"]
    lines += [f"[{t:5.1f}s] {user}: {msg}" for t, user, msg in chat] or ["(rien)"]
    return "\n".join(lines)


def describe(cfg: ContextConfig, channel: str, stream_title: str, transcript: str,
             chat: list[tuple[float, str, str]], fixed_tags: list[str]) -> dict | None:
    """{"title", "caption", "hashtags", "fixes": {mauvais: bon}} ou None si Claude ne répond pas."""
    if not cfg.enabled:
        return None
    exe = find_claude(cfg)
    if not exe:
        log.warning("Claude Code introuvable : accroche sans contexte (installe-le, voir README).")
        return None
    system = SYSTEM.format(channel=channel, title_max=cfg.title_max_chars,
                           fixed_tags=", ".join(fixed_tags) or "aucun")
    cmd = [exe, "-p", "--model", cfg.model, "--tools", "", "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA), "--system-prompt", system,
           "--no-session-persistence", "--strict-mcp-config"]
    prompt = build_prompt(channel, stream_title, transcript, chat)
    try:
        # Dossier vide : Claude Code ne lit pas le CLAUDE.md du projet.
        with tempfile.TemporaryDirectory() as empty:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=cfg.timeout, cwd=empty)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Claude Code n'a pas répondu (%s) : accroche sans contexte.", exc)
        return None
    try:
        data = json.loads(proc.stdout)
        if data.get("is_error"):
            raise ValueError(data.get("result"))
        out = data.get("structured_output") or json.loads(data["result"])
        result = {
            "title": out["title"].strip().rstrip("."),
            "caption": out["caption"].strip(),
            "hashtags": [h.strip().lstrip("#").lower() for h in out["hashtags"] if h.strip()],
            "fixes": {f["wrong"]: f["right"] for f in out["fixes"] if f["wrong"] and f["right"]},
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        detail = (proc.stdout or proc.stderr).strip()[-300:]
        log.warning("Réponse de Claude inutilisable (%s) : %s", exc, detail)
        return None
    if not result["title"]:
        return None
    return result
