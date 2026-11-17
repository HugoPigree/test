"""Chargement de la configuration (TOML) avec des valeurs par défaut raisonnables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class TwitchConfig:
    channel: str = "jltomy"
    # Liste de qualités par ordre de préférence (syntaxe Streamlink).
    quality: str = "1080p60,1080p,720p60,720p,best"
    # Token OAuth Twitch optionnel (compte sub/Turbo => pas de pubs, donc pas de trous).
    oauth_token: str = ""
    # Délai entre deux vérifications "est-ce qu'il est en live ?" (secondes).
    poll_interval: int = 60


@dataclass
class PathsConfig:
    recordings: str = "recordings"
    export: str = "ready_to_post"


@dataclass
class DetectionConfig:
    # Taille de la fenêtre glissante d'activité du chat (secondes).
    window: float = 10.0
    # Fenêtre de la ligne de base (médiane glissante, secondes).
    baseline_window: float = 300.0
    # Seuil de détection en "z-score robuste" (écart à la médiane / MAD).
    threshold: float = 3.5
    # Activité minimale absolue (score cumulé sur `window`) pour être un pic.
    min_activity: float = 15.0
    # Écart minimal entre deux pics (secondes).
    min_gap: float = 90.0
    # Nombre maximal de clips par session (les meilleurs scores sont gardés).
    max_clips: int = 12
    # Le chat réagit après l'action : on remonte avant le début de la hausse.
    pre_roll: float = 20.0
    post_roll: float = 6.0
    min_duration: float = 15.0
    max_duration: float = 60.0
    # Décalage chat -> vidéo (secondes, positif = le chat est en retard sur la vidéo).
    chat_offset: float = 0.0
    # Bonus par mot-clé de hype (en plus du poids 1 par message).
    keyword_weight: float = 1.5
    keywords: list[str] = field(default_factory=lambda: [
        "mdr", "ptdr", "xptdr", "jpp", "lol", "lmao", "kekw", "omegalul", "lul",
        "ahah", "haha", "hahaha", "pog", "poggers", "pogchamp", "pogu", "wtf",
        "omg", "clip", "clipped", "+1", "gg", "énorme", "enorme", "incroyable",
        "aled", "sheesh", "noway", "monkas", "pepelaugh", "icant", "w",
        "dinguerie", "masterclass", "chokbar", "ratio",
    ])


@dataclass
class VerticalConfig:
    # "blur" (vidéo entière sur fond flouté), "crop" (recadrage centré) ou
    # "split" (facecam en haut + jeu en bas).
    layout: str = "blur"
    width: int = 1080
    height: int = 1920
    # blur : zoom du plan principal (1.0 = vidéo entière, 1.3 = coupe les côtés).
    blur_zoom: float = 1.15
    # crop : position horizontale du recadrage (0 = gauche, 0.5 = centre, 1 = droite).
    crop_x: float = 0.5
    # split : rectangle de la facecam dans la source, en fractions (x, y, w, h).
    facecam: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.25, 0.30])
    # split : part de la hauteur finale occupée par la facecam.
    facecam_ratio: float = 0.40
    # split : position horizontale du recadrage du jeu.
    game_x: float = 0.5
    crf: int = 20
    preset: str = "veryfast"
    fps: int = 30


@dataclass
class SubtitlesConfig:
    enabled: bool = True
    # tiny / base / small / medium / large-v3 (small = bon compromis CPU).
    model: str = "small"
    language: str = "fr"
    device: str = "auto"
    compute_type: str = "auto"
    words_per_line: int = 3
    max_chars: int = 20
    font: str = "Arial Black"
    font_size: int = 88
    uppercase: bool = True
    # Couleurs ASS (&HAABBGGRR) : texte, mot en cours, contour.
    color: str = "&H00FFFFFF"
    highlight_color: str = "&H0000E5FF"
    outline_color: str = "&H00000000"
    outline: int = 6
    # Distance depuis le bas de l'image (pixels sur 1920).
    margin_v: int = 560


@dataclass
class ExportConfig:
    caption_template: str = "{hook} #jltomy #twitchfr #twitch #clip #fyp #pourtoi"
    hook_max_chars: int = 90
    keep_horizontal: bool = False
    # Supprimer l'enregistrement brut après traitement (gros fichiers !).
    delete_raw: bool = False


@dataclass
class TikTokConfig:
    # Upload automatique à la fin du traitement (sinon : `autoclip upload`).
    auto_upload: bool = False
    # "inbox" = brouillon dans l'appli TikTok (scope video.upload, fonctionne sans audit)
    # "direct" = publication directe (scope video.publish ; SELF_ONLY tant que l'appli n'est pas auditée)
    mode: str = "inbox"
    client_key: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8723/callback/"
    token_file: str = ".tiktok_token.json"
    privacy_level: str = "SELF_ONLY"
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False


@dataclass
class Config:
    twitch: TwitchConfig = field(default_factory=TwitchConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    vertical: VerticalConfig = field(default_factory=VerticalConfig)
    subtitles: SubtitlesConfig = field(default_factory=SubtitlesConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    tiktok: TikTokConfig = field(default_factory=TikTokConfig)


def _merge(obj: Any, data: dict[str, Any], prefix: str = "") -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Clé de configuration inconnue : {prefix}{key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"[{prefix}{key}] doit être une section")
            _merge(current, value, prefix=f"{prefix}{key}.")
        else:
            setattr(obj, key, value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    candidate = Path(path) if path else Path("config.toml")
    if candidate.exists():
        with candidate.open("rb") as fh:
            _merge(cfg, tomllib.load(fh))
    elif path:
        raise FileNotFoundError(f"Configuration introuvable : {candidate}")

    # Les secrets peuvent venir de l'environnement plutôt que du fichier.
    cfg.tiktok.client_key = os.environ.get("TIKTOK_CLIENT_KEY", cfg.tiktok.client_key)
    cfg.tiktok.client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", cfg.tiktok.client_secret)
    cfg.twitch.oauth_token = os.environ.get("TWITCH_OAUTH_TOKEN", cfg.twitch.oauth_token)
    cfg.twitch.channel = cfg.twitch.channel.lower().lstrip("#")
    return cfg
