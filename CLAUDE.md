# twitch-autoclip : contexte pour Claude Code

## But

Surveiller **une seule** chaîne Twitch (`jltomy`), détecter les pics d'activité du chat, et en faire
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

## Décisions prises

- Une seule chaîne pour l'instant : JLTomy.
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
| `autoclip/subtitles.py` | faster-whisper, recollage des élisions, ASS (mot surligné + accroche en haut). |
| `autoclip/live.py` | Mode continu : `streamlink --stdout` → `ffmpeg -f segment` (MPEG-TS), purge du buffer, détection toutes les 5 s, découpe depuis les segments, file + thread en nice 10. |
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
- `autoclip check --live` : ffmpeg et Streamlink OK, **Whisper plante** tant que le runtime
  Visual C++ n'est pas installé (l'invite admin a été refusée lors du premier essai).
- Pas encore lancé : `install_task.ps1`, `autoclip live`, un vrai clip sous Windows.

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
- Windows : `os.setpriority` n'existe pas ; via ctypes, il faut déclarer les types du handle
  (sinon il est tronqué à 32 bits et `SetPriorityClass` échoue sans rien dire).

## Performance

Mesuré sur le PC de l'école :
- Whisper `small` : **22,5 s pour 60 s d'audio**. (`base` en int8 : 12,2 s mais nettement moins bon ;
  `small` en int8 : 28,2 s, plus lent.)
- **65 à 80 s par clip de 35 s** avec `process` (l'encodage domine), **~15 min pour 12 clips**.
  En mode `live` : 26 à 42 s par clip de 30–40 s.
- Disque : 2,4 Go/h (domingo, 1080p60) à 3,5 Go/h (gotaga). Buffer `live` de 45 min : ~3 Go.

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
- **Disque** : 2,5 à 3,5 Go par heure pour `record` / `watch`. Le mode `live` reste borné (~3 Go).
