"""Chargement de la configuration (TOML) avec des valeurs par défaut raisonnables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class TwitchConfig:
    # Chaînes surveillées par `autoclip live` (mêmes règles pour toutes). La première sert aussi
    # aux commandes à une seule chaîne (record, watch, check…). Vide = [channel].
    channels: list[str] = field(default_factory=list)
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
    candidates: str = "candidates"  # mode continu : clips bruts en attente du tri quotidien


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
    pre_roll: float = 30.0
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
    # Léger zoom (8 %) pendant ~2 s au moment du pic de chat.
    peak_zoom: bool = True
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
    # Mots que Whisper doit savoir écrire (argot, mots arabes, anglicismes, noms) : passés en
    # « hotwords » à chaque fenêtre de 30 s. Le titre du live est ajouté automatiquement.
    vocabulary: str = ("JLTomy Etostark Stark wesh wallah starfoullah hamdoulah inchallah mashallah wallahi "
                       "frérot le sang zehef tiep bsahtek miskine hagra chouf sah wsh gros "
                       "frr bro chill flex clutch rage stream subathon donation sub follow "
                       "GTA Miami IRL")
    # Corrections forcées après Whisper, mot entier, sans tenir compte des majuscules.
    fixes: dict[str, str] = field(default_factory=lambda: {
        "walla": "wallah", "wala": "wallah", "starfoulah": "starfoullah",
        "astaghfirullah": "starfoullah", "hamdoulilah": "hamdoulah", "inshallah": "inchallah",
        "frero": "frérot", "jltommy": "JLTomy", "gilectomie": "JLTomy",
    })


@dataclass
class ContextConfig:
    # Titre, légende et corrections de sous-titres écrits par Claude (`claude -p`, abonnement
    # Claude, pas de clé API). Si Claude Code manque, retour à l'accroche simple.
    enabled: bool = True
    model: str = "haiku"
    claude_path: str = ""   # vide = cherché dans le PATH puis dans ~/.local/bin
    timeout: float = 120.0
    title_max_chars: int = 50


@dataclass
class ExportConfig:
    # {caption} et {hashtags} viennent de Claude ; sans Claude, {caption} = l'accroche et
    # {hashtags} est vide. {channel_tag} = la chaîne sans « _ » au bout (#etostark).
    # Aussi disponibles : {hook}, {channel}, {title} (titre du live).
    caption_template: str = "{caption} {hashtags} #{channel_tag} #twitchfr #fyp #pourtoi"
    hook_max_chars: int = 60
    # Titre incrusté en haut pendant les N premières secondes (0 = aucun, 60 = tout le clip).
    hook_duration: float = 60.0
    # Messages du chat incrustés à partir du moment où il s'emballe (0 = aucun).
    chat_overlay: int = 4
    keep_horizontal: bool = False
    # Supprimer l'enregistrement brut après traitement (gros fichiers !).
    delete_raw: bool = False


@dataclass
class LiveConfig:
    # Mode continu (`autoclip live`) : segments vidéo de N secondes, gardés `buffer_minutes`.
    segment_seconds: int = 300
    buffer_minutes: float = 45.0
    # Pas de nouveau clip moins de N secondes après le pic du clip précédent (anti-doublon).
    cooldown: float = 90.0


@dataclass
class DailyConfig:
    # Mode continu : les pics deviennent des candidates ; une fois par jour, à `selection_time`,
    # seules les meilleures sont rendues dans ready_to_post/AAAA-MM-JJ/.
    max_clips_per_day: int = 5
    min_gap_minutes: float = 20.0   # entre deux clips retenus d'une même chaîne
    selection_time: str = "23:00"   # heure du tri (HH:MM, heure du PC)
    retention_hours: float = 48.0   # candidates supprimées après ce délai
    max_disk_gb: float = 20.0       # taille max de candidates/ (les moins bonnes partent d'abord)


@dataclass
class QualityConfig:
    # Score de qualité des candidates (0-100) : somme pondérée des signaux (ramenée sur 100),
    # moins les pénalités. Chaque signal vaut ses points pleins à la valeur « *_full ».
    weight_chat: float = 25      # force du pic de chat
    weight_viewers: float = 15   # viewers distincts qui écrivent autour du pic
    weight_voice: float = 20     # pic de volume de la voix autour du moment (ffmpeg)
    weight_speech: float = 20    # densité de parole (Whisper)
    weight_sentence: float = 20  # le clip commence sur une phrase complète (Whisper)
    chat_score_full: float = 15.0
    viewers_full: int = 250       # JLTomy le 18/11 : 38 à 355 viewers distincts autour d'un pic
    voice_db_full: float = 12.0  # dB au-dessus de la médiane du clip
    speech_wps_full: float = 2.5  # mots par seconde
    penalty_duration: float = 20  # durée hors 15-60 s
    penalty_silence: float = 15   # plus de `silence_max` du clip sans voix
    silence_max: float = 0.5
    silence_lufs: float = -45.0   # en dessous (bande de la voix) = silence
    penalty_live_start: float = 15  # pic dans les premières minutes du live ou après une coupure
    live_start_minutes: float = 5.0
    penalty_pause: float = 25     # moins de `pause_max_words` mots dans le clip
    pause_max_words: int = 5
    penalty_nearby: float = 10    # une meilleure candidate de la même chaîne à moins de min_gap_minutes
    # Plus tard : une IA. "module:fonction" appelée avec (candidate, mesures), qui renvoie None ou
    # {"points": 0..1, "detail": "..."}, comptée avec `weight_external`. Vide = rien d'externe.
    external: str = ""
    weight_external: float = 0


@dataclass
class MusicConfig:
    # Musique de fond prise au hasard dans `dir`, un peu baissée quand quelqu'un parle.
    enabled: bool = True
    dir: str = "music"
    # Niveau de la musique sous le son du live, en dB (les deux sont normalisés à -14 LUFS avant).
    # -24 : bien présente ; -26 : fond discret (défaut) ; -28 : à peine audible sur un téléphone.
    level_db: float = -26.0


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
class TopConfig:
    # `autoclip top` : on cherche dans les `top_games` catégories les plus regardées du moment
    # (0 = aucune) et, en plus, dans `categories` (noms exacts Twitch).
    top_games: int = 100
    top_games_pages: int = 1  # pages de 100 clips pour chaque catégorie du top
    categories: list[str] = field(default_factory=lambda: [
        "Just Chatting", "Grand Theft Auto V", "League of Legends", "VALORANT"])
    lang: str = "fr"
    count: int = 3
    pages: int = 5          # pages de 100 clips pour chaque catégorie de `categories`
    # Appli Twitch (dev.twitch.tv/console). Secrets : config.toml ou TWITCH_CLIENT_ID /
    # TWITCH_CLIENT_SECRET, jamais dans le dépôt.
    client_id: str = ""
    client_secret: str = ""


@dataclass
class RadarConfig:
    # `autoclip top --radar` : radar GTA 6 (Twitch, YouTube, Reddit), rien n'est téléchargé.
    count: int = 20
    twitch_game: str = "Grand Theft Auto VI"
    twitch_pages: int = 5           # pages de 100 clips (l'API ne les trie pas strictement)
    youtube_query: str = "GTA 6"
    youtube_api_key: str = ""       # secret : config.toml ou YOUTUBE_API_KEY, jamais dans Git
    youtube_max_searches_per_day: int = 20  # 100 unités par recherche, 10 000 gratuites par jour
    reddit_subreddit: str = "GTA6"
    reddit_client_id: str = ""      # facultatif (sinon adresse .json publique) ; REDDIT_CLIENT_ID
    reddit_client_secret: str = ""  # REDDIT_CLIENT_SECRET
    reddit_user_agent: str = "autoclip-radar/0.1"


@dataclass
class Config:
    twitch: TwitchConfig = field(default_factory=TwitchConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    vertical: VerticalConfig = field(default_factory=VerticalConfig)
    subtitles: SubtitlesConfig = field(default_factory=SubtitlesConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    tiktok: TikTokConfig = field(default_factory=TikTokConfig)
    top: TopConfig = field(default_factory=TopConfig)
    radar: RadarConfig = field(default_factory=RadarConfig)


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
    cfg.top.client_id = os.environ.get("TWITCH_CLIENT_ID", cfg.top.client_id)
    cfg.top.client_secret = os.environ.get("TWITCH_CLIENT_SECRET", cfg.top.client_secret)
    cfg.radar.youtube_api_key = os.environ.get("YOUTUBE_API_KEY", cfg.radar.youtube_api_key)
    cfg.radar.reddit_client_id = os.environ.get("REDDIT_CLIENT_ID", cfg.radar.reddit_client_id)
    cfg.radar.reddit_client_secret = os.environ.get("REDDIT_CLIENT_SECRET", cfg.radar.reddit_client_secret)
    set_channels(cfg, cfg.twitch.channels or [cfg.twitch.channel])
    return cfg


def set_channels(cfg: Config, channels: list[str]) -> None:
    cfg.twitch.channels = [c.lower().lstrip("#") for c in channels]
    cfg.twitch.channel = cfg.twitch.channels[0]
