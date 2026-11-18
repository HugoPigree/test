# twitch-autoclip

Enregistre les lives Twitch de **JLTomy** et d'**Etostark** (`etostark__`), repère les moments où le chat s'emballe, découpe ces passages, les recadre en vertical 9:16, ajoute des sous-titres Whisper façon TikTok et range le tout dans `ready_to_post/`. L'upload TikTok est la dernière étape (optionnelle).

```
streamlink ──► recordings/<session>/stream.ts ─┐
chat IRC   ──► recordings/<session>/chat.jsonl ┴─► pics de chat ─► ffmpeg (découpe + 9:16 + sous-titres)
                                                                 └─► ready_to_post/<clip>/ ─► TikTok
```

## Installation (une fois)

1. **Python 3.11+** et **ffmpeg** dans le PATH
   - Windows : `winget install Gyan.FFmpeg` puis rouvrir le terminal
   - macOS : `brew install ffmpeg` · Linux : `sudo apt install ffmpeg`
2. Dans le dossier du projet :
   ```bash
   python -m venv .venv
   # Windows : .venv\Scripts\activate    |  macOS/Linux : source .venv/bin/activate
   pip install -e ".[whisper]"
   cp config.example.toml config.toml   # puis adapte si besoin
   autoclip check --live
   ```
   Le premier clip télécharge le modèle Whisper (~500 Mo pour `small`).

## Utilisation

| Commande | Effet |
|---|---|
| `autoclip live` | **Mode continu**, pour un live qui ne s'arrête jamais (subathon) : enregistre en segments de 5 min en ne gardant que les 45 dernières minutes, analyse le chat en continu et coupe chaque clip pendant le live. Les clips sont traités un par un en priorité basse. Se relance tout seul après une coupure du stream ou du chat. Ctrl+C = arrêt, les clips déjà coupés sont terminés. |
| `autoclip watch` | Attend que JLTomy lance son live, enregistre vidéo + chat, et génère les clips à la fin du live. Tourne en boucle. Ctrl+C = arrêt propre, la session en cours est quand même traitée. |
| `autoclip record --duration 600 --process` | Enregistre 10 min maintenant puis traite la session (pratique pour tester pendant un live). |
| `autoclip detect recordings/<session>` | Affiche la courbe du chat et les pics trouvés, sans rien encoder (pour régler les seuils). |
| `autoclip process recordings/<session>` | (Re)génère les clips d'une session. Les clips déjà exportés ne sont pas réencodés. |
| `autoclip tiktok-auth` | Connecte ton compte TikTok (une fois). |
| `autoclip upload` | Envoie sur TikTok tous les clips pas encore uploadés. |

**Plusieurs chaînes** : `autoclip live` surveille toutes les chaînes de `[twitch] channels` (par défaut `["jltomy", "etostark__"]`) avec les mêmes règles. Chaque chaîne a son enregistrement, son chat et son buffer (`recordings/<chaîne>_buffer/`, ~3 Go chacun), mais il n'y a **qu'une file de clips** : un seul clip traité à la fois et un seul Whisper en mémoire, pour ménager le PC. Les clips portent le nom de leur chaîne et le hashtag qui va avec (`#jltomy`, `#etostark`). Les autres commandes (`record`, `watch`…) prennent la première chaîne de la liste.

`--channel autrechaine` permet de cibler une seule autre chaîne pour tester.

### Ce qu'il y a dans chaque dossier de `ready_to_post/`

```
2026-11-19_jltomy_03_1h42m10s/
├── video.mp4        1080x1920, H.264/AAC, son normalisé, sous-titres incrustés
├── caption.txt      légende prête à coller (accroche + hashtags)
├── thumbnail.jpg
├── subtitles.ass    sous-titres (pour retoucher si besoin)
└── meta.json        horodatage dans le live, score, transcription, statut TikTok
```

## Réglages utiles (`config.toml`)

- **Trop ou pas assez de clips** : `detection.threshold` (3.5 par défaut, baisser à 2.5 donne plus de clips) et `detection.max_clips`.
- **Clip qui commence trop tard ou trop tôt** : `detection.pre_roll` (30 s avant la réaction du chat) et `detection.chat_offset`.
- **Contexte (Claude)** : pour chaque clip, Claude lit la transcription, le chat au moment du pic et le titre du live, puis écrit un **titre qui explique ce qui se passe** (incrusté en haut pendant tout le clip, `export.hook_duration`), la **légende** de `caption.txt` avec des hashtags liés au contenu, et corrige les mots mal compris par Whisper. Ça passe par Claude Code (`claude -p`, modèle `haiku`) avec ton abonnement Claude, sans clé API : installe Claude Code, lance `claude` une fois et fais `/login`. Sans Claude, l'accroche reste la phrase dite au moment du pic. Réglages : `[context]`.
- **Sous-titres argot / mots arabes / anglais** : `subtitles.vocabulary` (mots que Whisper doit savoir écrire, le titre du live est ajouté tout seul) et `[subtitles.fixes]` (corrections forcées, ex. `walla = "wallah"`).
- **Animations** : le mot prononcé « saute » (85 → 120 → 108 %), le titre arrive en rebondissant, et le chat défile en bas à gauche à partir du pic (`export.chat_overlay`, 4 messages, 0 = aucun ; pseudos colorés comme sur Twitch, sans bots ni commandes).
- **Musique de fond** (activée) : mets des morceaux libres de droits dans `music/` (sinon pas de musique, juste un avertissement dans le journal). Volume ~10 %, baissé automatiquement quand le son du live est fort dans la bande de la voix.
- **Zoom au pic** (activé) : `vertical.peak_zoom` (zoom de 8 % pendant ~2 s).
- **Cadrage** : `vertical.layout`
  - `blur` (par défaut) : image entière sur fond flouté. Marche avec tout, rien n'est coupé.
  - `crop` : recadrage plein écran, centré par défaut (`crop_x` pour décaler).
  - `split` : facecam en haut, jeu en bas. Renseigne `facecam = [x, y, l, h]` en fractions de l'image (ex. webcam en bas à droite sur un quart d'écran : `[0.75, 0.70, 0.25, 0.30]`).
- **PC lent** : `subtitles.model = "base"`. **GPU NVIDIA** : `device = "cuda"`, `model = "medium"`.
- **Pubs Twitch** : sans compte sub/Turbo, Streamlink saute les pubs, ce qui laisse des trous dans la vidéo. Renseigne `twitch.oauth_token` (cookie `auth-token` de twitch.tv) pour les éviter.
- **Disque** : compte environ 2,5 à 3,5 Go par heure en 1080p60. En mode `live`, le buffer reste autour de 2 à 3 Go (`[live] buffer_minutes`, `segment_seconds`, et `cooldown` entre deux clips). `export.delete_raw = true` supprime l'enregistrement brut une fois tous les clips générés.

## Upload TikTok

Le script passe par l'API officielle *Content Posting*, ce qui demande une appli développeur TikTok (gratuite) :

1. Sur https://developers.tiktok.com, crée une appli et ajoute les produits **Login Kit** et **Content Posting API**.
2. Ajoute l'URI de redirection `http://localhost:8723/callback/` (plateforme Desktop) et les scopes `user.info.basic` et `video.upload` (plus `video.publish` pour le mode `direct`).
3. Mets `client_key` et `client_secret` dans `config.toml` (ou dans les variables d'environnement `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`), puis lance `autoclip tiktok-auth`.

Modes disponibles :
- `inbox` (par défaut) : chaque clip arrive en **brouillon** dans l'appli TikTok (notification). Tu choisis le son, tu colles `caption.txt` et tu postes. Ce mode marche sans audit TikTok.
- `direct` : publication directe. Tant que TikTok n'a pas audité ton appli, les vidéos sont forcément **privées** (`SELF_ONLY`).

Avec `tiktok.auto_upload = true`, l'upload se fait tout seul à la fin de chaque live.

## Déploiement sur le serveur

Pour une machine Linux toujours allumée, pilotée depuis le téléphone avec Claude Code (Remote Control).
Le contexte du projet pour Claude est dans `CLAUDE.md` : une nouvelle session repart de là.

**1. Installer** (une fois ; `setup_server.sh` est relançable et dit ce qui manque, sans sudo) :

```bash
git clone https://github.com/HugoPigree/test.git twitch-autoclip
cd twitch-autoclip
git checkout claude/affectionate-faraday-vx9g05
scripts/setup_server.sh
```

**2. Ouvrir Claude Code dans tmux** (pour qu'il survive à la déconnexion SSH) :

```bash
tmux new -s claude        # dans le dossier twitch-autoclip
claude                    # si absent : curl -fsSL https://claude.ai/install.sh | bash
```

Dans Claude Code : `/rename autoclip-serveur`, puis `/remote-control` et suivre les instructions
affichées pour se connecter depuis le téléphone. Ensuite `Ctrl+B` puis `D` pour quitter tmux sans
arrêter Claude.

**3. Lancer l'outil** (ou le demander à Claude depuis le téléphone) :

```bash
scripts/run_tmux.sh       # autoclip live dans la session tmux « autoclip »
```

Le buffer vidéo est dans `recordings/jltomy_buffer/` (~3 Go max), les clips dans `ready_to_post/`,
le journal dans `autoclip.log`.

**Si ça s'arrête :**

- Voir l'erreur : `tmux attach -t autoclip` (la fenêtre reste ouverte après un arrêt) ou
  `tail -n 50 autoclip.log`.
- Relancer : `scripts/run_tmux.sh` (ne fait rien si autoclip tourne déjà).
- Après un redémarrage du serveur, les sessions tmux sont perdues : refaire `tmux new -s claude`,
  `claude --continue` (reprend la dernière conversation), `/remote-control`, puis `scripts/run_tmux.sh`.
- Après une mise à jour du code : `git pull`, `scripts/setup_server.sh`, puis arrêter autoclip
  (`tmux attach -t autoclip`, Ctrl+C) et relancer `scripts/run_tmux.sh`.

## Déploiement sur un serveur Windows

Windows natif, sans WSL (une VM Linux en plus consomme trop pour un petit serveur). Mettre le
dépôt dans un dossier à chemin court (ex. `C:\Users\<toi>\autoclip`) et activer les chemins longs
pour git : `git -c core.longpaths=true clone …`.

**1. Installer** (relançable, installe ffmpeg avec winget s'il manque, le reste dans `.venv`) :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

**2. Relance automatique** (remplace tmux) : dans un PowerShell **en administrateur**,

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
```

Ça crée la tâche planifiée `autoclip` : `autoclip live` démarre avec le PC, même sans session
ouverte, et repart dans les 5 min s'il s'arrête. Le traitement des clips tourne en priorité
« inférieure à la normale », Streamlink et l'enregistrement en priorité normale.

- Voir : `Get-ScheduledTask autoclip | Get-ScheduledTaskInfo`, journal `autoclip.log`, plantages
  dans `autoclip-erreurs.log`.
- Arrêter : `Disable-ScheduledTask autoclip; Stop-ScheduledTask autoclip`. C'est un arrêt sec :
  les clips en file d'attente ne sont pas terminés. Pour un arrêt propre, lancer plutôt
  `.venv\Scripts\autoclip live` à la main dans un terminal et faire Ctrl+C.
- Relancer : `Enable-ScheduledTask autoclip; Start-ScheduledTask autoclip`.
- Après une mise à jour du code : `git pull`, `scripts\setup_windows.ps1`, puis arrêter et relancer.

## Droits

Ces clips utilisent le contenu de JLTomy. Avant de les publier, vérifie que tu as son accord ou qu'il autorise les comptes de clips. Sans ça, TikTok peut retirer les vidéos ou bloquer le compte pour violation de droits d'auteur.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Les tests génèrent un faux live de 4 min avec ffmpeg et un faux chat contenant 3 moments de hype. Ils vérifient la détection, l'export 1080x1920 dans les 3 layouts, les sous-titres, la reprise sans réencodage et l'enchaînement des appels à l'API TikTok (simulée).
