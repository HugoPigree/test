# twitch-autoclip : contexte pour Claude Code

## But

Surveiller les chaînes Twitch `jltomy` et `etostark__` (Etostark, ajoutée le 18/11/2026, « mêmes
règles »), détecter les pics d'activité du chat, et en faire
des clips verticaux 1080x1920 sous-titrés (Whisper en français, mot courant surligné), rangés dans
`ready_to_post/<clip>/` pour TikTok. L'upload TikTok se fait **en brouillon** via l'API officielle
(Content Posting API, mode `inbox`) : c'est la dernière étape. L'utilisateur poste ensuite les
brouillons depuis son téléphone.

## Préférences de l'utilisateur

- Français décontracté, réponses courtes et directes.
- Code simple et lisible. Ne rien ajouter qui n'a pas été demandé.
- Toujours dire clairement ce qui a été testé pour de vrai et ce qui ne l'a pas été.
- Un commit par correction utile.
- **Le dépôt GitHub est public** : jamais de token, mot de passe ou donnée personnelle dans le repo
  (ni dans les commits). Les secrets vont dans `config.toml` (ignoré par git) ou dans les variables
  d'environnement `TWITCH_OAUTH_TOKEN`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`.

## Travailler sur ce dépôt

- `main` et `claude/affectionate-faraday-vx9g05` sont identiques depuis le 18/11/2026 : travailler
  sur `main`.
- Dépôt public : avant de commiter sur une nouvelle machine, régler l'e-mail masqué de GitHub
  (`git config user.name "HugoPigree"` et
  `git config user.email "116163096+HugoPigree@users.noreply.github.com"`), jamais un e-mail perso.
- Pousser demande un token GitHub que l'utilisateur tape lui-même dans un vrai terminal (git ne
  peut pas le demander depuis Claude Code). `git config credential.helper 'cache --timeout=3600'`
  le garde 1 h en mémoire, ce qui permet ensuite à Claude de pousser. Ne jamais écrire le token dans
  un fichier, une URL ou le chat.
- Les enregistrements et les clips de test (domingo, gotaga, JLTomy) sont restés sur le PC de
  l'école : sur une nouvelle machine, `recordings/` et `ready_to_post/` sont vides. `classement.md`
  est un historique.

## Retours de l'utilisateur sur les clips

- Après le premier test : « sous-titres bien, mais ils manquent de contexte et de vie ». D'où
  l'accroche incrustée, le `pre_roll` à 30 s, la musique et le zoom (ces deux derniers en option).
- Critères de classement demandés : force du pic de chat, transcription intéressante sans
  contexte, durée, début propre (voir `classement.md`).
- **18/11/2026, après avoir vu les clips du PC de l'école** : « pas mal, mais on comprend pas ce
  qui se passe », « les descriptions c'est des mots au hasard, ça se voit que c'est une IA »,
  « pas de musique, on se fait vite chier », « clip brut, pas de valeur ajoutée », sous-titres bien
  mais faux sur les anglicismes et les mots arabes. Demandé : contexte précis, petite animation,
  musique de fond. D'où `context.py` (Claude), le vocabulaire Whisper, le chat incrusté, les
  animations, et musique + zoom activés par défaut. L'utilisateur fournit lui-même les musiques
  libres de droits (dossier `music/`).
- Outils existants regardés (18/11/2026) : OpenShorts, AI-Youtube-Shorts-Generator et clones
  choisissent les moments eux-mêmes avec une IA (on perdrait la détection par le chat) ; pycaps
  (sous-titres animés) est en alpha et rend via Chromium, trop lourd ici ; Submagic / OpusClip
  sont payants (15 à 69 $/mois). Décision : tout faire dans notre pipeline, en ASS (libass).

## Décisions prises

- Deux chaînes depuis le 18/11/2026 : JLTomy et Etostark (`etostark__` ; `etostark` n'existe
  pas). Mêmes règles pour les deux (`[twitch] channels`). Un seul process `autoclip live` : un
  thread par chaîne (`live._watch` : enregistrement, chat, détection, découpe) et **une seule file
  de clips** (`live._worker`) pour ne charger qu'un Whisper et n'encoder qu'un clip à la fois.
  Si une chaîne plante, `run_live` s'arrête (code 1) et la tâche planifiée relance tout.
- Son stream ne s'arrête jamais (subathon). Le traitement « à la fin du live » (`watch`) ne sert donc
  à rien : on utilise le **mode continu** `autoclip live` (segments, buffer de 45 min max, file
  d'attente traitée en `nice`).
- Les clips arrivent en brouillons TikTok, postés à la main depuis le téléphone. Au début, 1 à 2
  clips postés par jour.
- Machine cible (depuis le 18/11/2026) : un **serveur Windows** peu puissant, toujours allumé,
  piloté à distance (`/remote-control`). **Windows natif, pas WSL2** (la VM consomme trop). Dépôt
  dans `C:\Users\hpigr\autoclip\twitch-autoclip` (chemin court, `core.longpaths = true`).
  Installation : `scripts\setup_windows.ps1`. Relance automatique : tâche planifiée `autoclip`
  créée par `scripts\install_task.ps1` (au démarrage + toutes les 5 min, ignorée si déjà lancée).
  Les scripts Linux (`setup_server.sh`, `run_tmux.sh`) restent pour un éventuel serveur Linux.

## Architecture

| Fichier | Rôle |
|---|---|
| `autoclip/cli.py` | Commandes : `check [--live] [--tiktok]`, `live`, `watch [--once]`, `record --duration N --process`, `process <session>`, `detect <session>`, `upload`, `tiktok-auth`. Options globales : `-c config.toml`, `--channel autre`. |
| `autoclip/config.py` | Config en dataclasses. Lit `config.toml` (local, ignoré par git). `config.example.toml` (versionné) contient **toutes** les clés avec leurs valeurs par défaut. |
| `autoclip/recorder.py` | Streamlink : `is_live`, enregistrement en un seul fichier `stream.ts` (pour `record` / `watch`). |
| `autoclip/chat.py` | Chat IRC anonyme (`justinfan…`), reconnexion automatique. Écrit du JSONL ou appelle une fonction. |
| `autoclip/peaks.py` | Détection des pics : activité sur 10 s, médiane glissante 5 min + MAD, z-score, `pre_roll` / `post_roll`. |
| `autoclip/pipeline.py` | Session → pics → `process_peak` (Whisper, accroche, ASS, rendu, `caption.txt`, `meta.json`) ; upload. |
| `autoclip/video.py` | ffprobe / ffmpeg : 9:16 (`blur` / `crop` / `split`), zoom au pic, musique (sidechaincompress), miniature. |
| `autoclip/subtitles.py` | faster-whisper (`hotwords` = vocabulaire + titre du live, puis `fixes`), recollage des élisions, ASS : mot qui « saute », titre qui rebondit, chat qui défile en bas à gauche (`pick_chat`). |
| `autoclip/context.py` | Titre, légende, hashtags et corrections de mots écrits par Claude via `claude -p --model haiku --tools "" --json-schema …` (abonnement Claude de l'utilisateur, pas de clé API), lancé dans un dossier vide. Si Claude manque ou échoue : `None`, et le pipeline reprend l'ancienne accroche. |
| `autoclip/live.py` | Mode continu : un thread `_watch` par chaîne (`streamlink --stdout` → `ffmpeg -f segment` en MPEG-TS, purge du buffer, détection toutes les 5 s, découpe depuis les segments) et un seul `_worker` pour toutes les chaînes, en priorité basse. Chaque tâche de la file emporte la config de sa chaîne (`channel_config`). |
| `autoclip/top.py` | `autoclip top --lang fr --count 3` (demandé le 18/11/2026) : API Helix (jeton d'appli, Get Games puis Get Clips 24 h par catégorie de `[top] categories`), filtre langue, doublons (id, ou même VOD à < 60 s), tri par vues ; yt-dlp ; `process_peak` sur le clip entier (60 s max, accroche 3 s, pas de zoom ni de chat) ; `ready_to_post/top_AAAA-MM-JJ_NN/` avec crédit streamer + clippeur + lien dans `caption.txt`, vues / lien / chaîne dans `meta.json`. Jamais d'upload TikTok. Identifiants : `[top] client_id / client_secret` ou `TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET`. |
| `autoclip/tiktok.py` | OAuth + Content Posting API (`inbox` / `direct`). **Jamais testé en vrai.** |
| `scripts/setup_server.sh` | Installation idempotente, sans sudo (venv, dépendances, modèle Whisper, config, check). |
| `scripts/run_tmux.sh` | Lance `autoclip live` (par défaut) dans la session tmux `autoclip`. |
| `scripts/setup_windows.ps1` | Équivalent Windows de `setup_server.sh` (installe ffmpeg et le runtime Visual C++ via winget s'ils manquent ; sans ce runtime, `ctranslate2.dll` ne se charge pas et Whisper plante). |
| `scripts/install_task.ps1` | Tâche planifiée `autoclip` (S4U, sans session ouverte) qui remplace tmux. À lancer en admin. |

Priorité basse sous Windows : `live._lower_priority` passe tout le process autoclip en
« inférieure à la normale » (une priorité de thread ne passe pas aux enfants), et `Recording`
lance Streamlink et le ffmpeg du buffer avec `NORMAL_PRIORITY_CLASS` (`WIN_NORMAL`).

Fichiers produits (tous ignorés par git) :
- `recordings/<chaîne>_<date>/` : `stream.ts`, `chat.jsonl`, `session.json` (`record` / `watch`).
- `recordings/<chaîne>_buffer/<connexion>/seg_XXXXX.ts` : buffer du mode `live`.
  `recordings/<chaîne>_queue/` : clips coupés en attente de traitement.
- `ready_to_post/<clip>/` : `video.mp4`, `caption.txt`, `thumbnail.jpg`, `subtitles.ass`, `meta.json`.
- `autoclip.log` : journal.

Tests : `pip install -e ".[dev]"` puis `pytest` (31 tests, ~75 s, ffmpeg requis ; Whisper et
l'API TikTok sont simulés).

## État réel

### Testé pour de vrai

Sur le PC de l'école (Intel Core Ultra 5 125U, 16 Go, sans GPU, Arch Linux, Python 3.14,
ffmpeg 9.0.2, Streamlink 8.6.3, faster-whisper 1.2.1) :

- **17/11/2026** : `record --duration 600 --process` sur domingo (flux MPEG-TS) et sur gotaga
  (flux fMP4), 10 min chacun : Streamlink, chat IRC, détection, Whisper et ffmpeg fonctionnent,
  clips 1080x1920 H.264/AAC corrects. Synchro chat/vidéo vérifiée à la main sur 3 pics : le
  déclencheur tombe 4 à 15 s après le début du clip et le chat a 0 à 3 s de retard, donc
  `chat_offset = 0`.
- **18/11/2026** : accroche, `pre_roll` 30 s, musique et zoom rendus sur l'enregistrement domingo.
  Musique testée avec un morceau synthétique seulement ; baisse mesurée : −37 dB sans voix,
  −44 dB pendant la voix.
- **18/11/2026** : `autoclip live` sur le live de JLTomy pendant 21 min (segments 60 s, buffer
  5 min pour le test). Le buffer plafonne à 300–380 Mo. Les segments sont continus, sans trou,
  même pendant l'encodage des clips. Streamlink tué volontairement à 8 min : relance en 2 s
  (~8 s de vidéo perdues). 4 clips coupés pendant le live (26 à 42 s de traitement chacun).
  ffmpeg et les threads Whisper tournent bien en nice 10. Reconnexion du chat testée en fermant
  de force la socket IRC.
- **18/11/2026** : nouvel essai de 9 min de `autoclip live` sur JLTomy après les corrections de
  démarrage et d'arrêt. Aucun faux pic au démarrage, arrêt propre (dernier segment complet et
  lisible). Le chat est resté calme, donc aucun clip pendant cet essai : avec ce code, la détection
  d'un vrai pic après 5 min n'est couverte que par les tests unitaires.

Sur le serveur Windows (Windows 11 Pro, Python 3.12.10, ffmpeg 9.0.2 Gyan, Streamlink 8.6.3,
faster-whisper 1.2.1), **18/11/2026** :

- `pytest` : 31/31 en ~110 s (après correction du test fMP4, voir plus bas).
- Priorités vérifiées avec `Get-Process … .PriorityClass` : autoclip `BelowNormal`, un ffmpeg lancé
  avec `WIN_NORMAL` en `Normal`, un ffmpeg lancé sans (comme l'encodage des clips) en `BelowNormal`.
- `setup_windows.ps1` complet : runtime Visual C++ installé via winget (invite admin), modèle
  Whisper `small` téléchargé, `autoclip check --live` tout OK (JLTomy vu en live). Sans ce
  runtime, `ctranslate2.dll` ne se charge pas et Whisper plante.
- `record --duration 600` sur JLTomy (IRL en voiture à Miami) : 10 min, 541 Mo, chat OK.
  `detect` : 3 pics. `process` **sans Claude** (pas encore connecté) : 3 clips en 52 à 62 s chacun
  (Whisper `small` ~30-40 s). Vérifié sur une planche d'images : titre en haut, mot qui saute,
  zoom au pic, chat qui défile en bas à gauche. Constat : sans Claude, l'accroche n'a pas de sens
  (« j'ai même pas dépassé l'induit économe… ») et Whisper se trompe sur l'audio de voiture
  (« weepinnt », « l'induit »).
- Mêmes 3 clips **avec Claude** (connecté, `haiku`) : 61 à 64 s par clip, appel à Claude compris.
  Titres obtenus : « JLTomy accélère à fond, le chat craint la panne », « JLTomy vante un des
  trois ronds-points de Miami », « JLTomy répond au chat qui attend du happening ». Légendes au
  ton naturel, hashtags liés au contenu (#miami, #rondpoint…), corrections « appending →
  happening », « fritz → frites ». À surveiller : une légende a écrit « Toma » au lieu de Tomy.
- Pas encore lancé : `install_task.ps1`.
- `autoclip top` : étapes 2 à 4 testées sur un vrai clip Etostark (« pauvre Fousseyd », 17 s,
  URL `clips.twitch.tv/…` comme l'API la renvoie) : yt-dlp OK, clip entier en 1080x1920, titre de
  Claude 3 s, légende avec crédit + lien, 33 s de traitement. **L'étape 1 (API Twitch) n'a pas
  tourné pour de vrai** : pas encore d'appli Twitch (client_id / secret) ; testée avec une fausse
  API seulement. Constat : si le clip a déjà des sous-titres incrustés par le streamer, ils
  apparaissent en double ; la musique de fond s'ajoute aussi (réglage `[music]` commun).

Méthodes de vérification utilisées (à refaire de la même façon) :
- Synchro chat/vidéo : transcrire avec Whisper une minute autour d'un pic (`video.extract_audio` +
  `subtitles.transcribe`), puis afficher seconde par seconde les mots prononcés face aux messages
  de `chat.jsonl`. Un écho direct (mot dit puis repris par le chat) donne le décalage.
- Mode continu sans attendre 45 min : une copie de la config avec `[live] segment_seconds = 60`,
  `buffer_minutes = 5` et `[paths] export = "ready_to_post/_test_live"`, lancée avec
  `timeout -s INT 1260 autoclip -c <copie> live`.

### Pas testé

- `watch` sur un live complet (détection de fin de live, enregistrement de plusieurs heures).
- Mode continu sur une longue durée (heures, jours) avec les vrais réglages (segments 5 min,
  buffer 45 min). Le passage à zéro des horodatages MPEG-TS (toutes les ~26,5 h) n'est couvert que
  par un test unitaire.
- **Pubs Twitch** : aucune pub vue en ~50 min d'enregistrement. On ne sait pas si les trous laissés
  par les pubs sautées décalent la synchro chat/vidéo.
- Vraies coupures réseau (seulement simulées : kill de Streamlink, fermeture de la socket du chat).
- **TikTok** : rien. L'appli développeur n'est pas créée ; OAuth et upload n'ont jamais tourné.
- Layouts `crop` et `split` sur un vrai stream (seul `blur` l'a été), musique avec un vrai morceau.
- Le serveur lui-même : `setup_server.sh` n'a tourné que sur le PC de l'école.

## Bugs déjà corrigés (ne pas les réintroduire)

- **PyAV + faster-whisper** : faster-whisper 1.2.1 appelle `av.open(metadata_errors=...)`, option
  supprimée de PyAV ≥ 15. La transcription plantait, l'exception était avalée par le pipeline, et
  les clips sortaient sans sous-titres. Correctif : `subtitles._read_wav` lit le wav (16 kHz mono,
  produit par ffmpeg) avec `wave` + numpy et passe le tableau à Whisper. Ne pas repasser un chemin
  de fichier à `model.transcribe`.
- **Élisions coupées** : Whisper renvoie « c » + « 'est », « peut » + « -être ». `join_elisions`
  les recolle.
- **Sous-titres empilés** : la fin du dernier mot d'une ligne est plafonnée au début de la ligne
  suivante, et les mots de durée nulle (répétitions tassées par Whisper) sont ignorés.
- **Durée fausse en fMP4** : certaines chaînes (gotaga) sont servies en fMP4 et ffprobe compte alors
  la durée depuis 0. `video.probe` soustrait `start_time` pour les formats mp4. Le `-ss` de ffmpeg
  est bien relatif au début du fichier.
- **Accroche** : le tiret de dialogue « – » ajouté par Whisper est retiré, et les phrases
  inachevées (« ... ») sont écartées.
- **Mode continu** : il y avait de faux pics juste après le lancement (ligne de base calculée sur
  30 s de chat). Désormais rien n'est détecté avant `baseline_window` (5 min) de chat. À l'arrêt, on
  coupe Streamlink d'abord pour que ffmpeg ferme proprement son dernier segment.

Autres pièges connus :
- Whisper n'est pas déterministe d'un lancement à l'autre : les sous-titres et l'accroche peuvent
  changer. Il hallucine parfois des répétitions (« non non non… »).
- L'accroche est la phrase dite au moment du pic, pas un résumé : elle n'explique pas toujours la
  situation.
- `process` ne réencode pas un clip dont `video.mp4` existe déjà : supprimer son dossier pour le
  régénérer.
- La police « Arial Black » est souvent absente sous Linux : libass prend une police grasse proche.
- Windows : le muxer HLS de ffmpeg n'écrit pas `init.mp4` à côté de la playlist quand on lui donne
  un chemin avec des `\` (il l'écrit dans le dossier courant). Le test fMP4 lance donc ffmpeg
  depuis le dossier `hls`.
- **Python 3.12** : `ChatLogger._stop` masquait `Thread._stop()` et `join()` plantait à la fin de
  `record` (pas vu sous 3.14). Renommé `_stop_event`.
- Windows : sans `encoding="utf-8"`, le JSON de Streamlink est lu en cp1252 (titre « Ã  »).
- Windows PowerShell 5.1 : `Get-Content` / `Set-Content` lisent et écrivent en ANSI par défaut et
  abîment les accents des fichiers UTF-8. Éditer les fichiers avec Python ou l'outil Edit.
- Windows : `os.setpriority` n'existe pas ; via ctypes, il faut déclarer les types du handle
  (sinon il est tronqué à 32 bits et `SetPriorityClass` échoue sans rien dire).

## Performance

Mesuré sur le PC de l'école :
- Whisper `small` : **22,5 s pour 60 s d'audio**. (`base` en int8 : 12,2 s mais nettement moins bon ;
  `small` en int8 : 28,2 s, plus lent.)
- **65 à 80 s par clip de 35 s** avec `process` (l'encodage domine), **~15 min pour 12 clips**.
  En mode `live` : 26 à 42 s par clip de 30–40 s.
- Disque : 2,4 Go/h (domingo, 1080p60) à 3,5 Go/h (gotaga). Buffer `live` de 45 min : ~3 Go.

Mesuré sur le serveur Windows (AMD Ryzen 5 5500U, 10 Go) : **52 à 62 s par clip de 31 à 43 s**
avec `process`, Whisper `small` compris (avant l'appel à Claude, non mesuré).

**Le serveur est moins puissant.** Consigne : d'abord mesurer le temps par clip (lignes
`✔ … en XXs` dans `autoclip.log`). Si c'est trop lent (la file du mode `live` grossit, ou plus de
~3 min par clip), passer `subtitles.model = "base"`, et garder `vertical.preset = "veryfast"`
(c'est déjà la valeur par défaut ; `"ultrafast"` en dernier recours).

## À faire, par priorité

1. ~~Mode continu~~ : fait (`autoclip live`). Reste à le valider sur une longue durée avec les vrais
   réglages.
2. ~~Titre d'accroche incrusté~~ : fait (limite : phrase choisie, pas un résumé).
3. ~~`pre_roll` à 30 s~~ : fait.
4. ~~Classement des clips~~ : fait une fois, à la main, dans `classement.md` (18/11/2026). Pas automatisé.
5. ~~Option musique de fond à faible volume~~ : faite (désactivée par défaut, dossier `music/`).
6. **Upload TikTok** : le code existe (`tiktok-auth`, `upload`, mode `inbox`) mais n'a jamais
   tourné. Il faut d'abord créer l'appli développeur TikTok (voir README).

Plus tard seulement : source YouTube via yt-dlp ; séparation en « sources » + pipeline commun.

## Risques à rappeler

- **Droits** : vérifier que JLTomy autorise les comptes de clips avant de poster.
- **Pubs Twitch** : sans compte sub/Turbo, Streamlink saute les pubs et ça laisse des trous dans la
  vidéo. Renseigner `twitch.oauth_token` dans `config.toml` (jamais dans le repo).
- **Disque** : 2,5 à 3,5 Go par heure pour `record` / `watch`. Le mode `live` reste borné (~3 Go
  par chaîne, donc ~6 Go avec JLTomy + Etostark).
- **Deux chaînes en même temps** : deux flux 1080p60 téléchargés en continu (débit réseau non
  mesuré) ; si les deux chats s'emballent ensemble, la file de clips grossit (un clip à la fois).
  `autoclip live` à deux chaînes n'a **pas encore tourné pour de vrai** (seulement les tests).
