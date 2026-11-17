# twitch-autoclip

Enregistre le live Twitch de **JLTomy**, repère les moments où le chat s'emballe, découpe ces passages, les recadre en vertical 9:16, ajoute des sous-titres Whisper façon TikTok et range le tout dans `ready_to_post/`. L'upload TikTok est la dernière étape (optionnelle).

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
| `autoclip watch` | Attend que JLTomy lance son live, enregistre vidéo + chat, et génère les clips à la fin du live. Tourne en boucle. Ctrl+C = arrêt propre, la session en cours est quand même traitée. |
| `autoclip record --duration 600 --process` | Enregistre 10 min maintenant puis traite la session (pratique pour tester pendant un live). |
| `autoclip detect recordings/<session>` | Affiche la courbe du chat et les pics trouvés, sans rien encoder (pour régler les seuils). |
| `autoclip process recordings/<session>` | (Re)génère les clips d'une session. Les clips déjà exportés ne sont pas réencodés. |
| `autoclip tiktok-auth` | Connecte ton compte TikTok (une fois). |
| `autoclip upload` | Envoie sur TikTok tous les clips pas encore uploadés. |

`--channel autrechaine` permet de cibler une autre chaîne pour tester.

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
- **Clip qui commence trop tard ou trop tôt** : `detection.pre_roll` (20 s avant la réaction du chat) et `detection.chat_offset`.
- **Cadrage** : `vertical.layout`
  - `blur` (par défaut) : image entière sur fond flouté. Marche avec tout, rien n'est coupé.
  - `crop` : recadrage plein écran, centré par défaut (`crop_x` pour décaler).
  - `split` : facecam en haut, jeu en bas. Renseigne `facecam = [x, y, l, h]` en fractions de l'image (ex. webcam en bas à droite sur un quart d'écran : `[0.75, 0.70, 0.25, 0.30]`).
- **PC lent** : `subtitles.model = "base"`. **GPU NVIDIA** : `device = "cuda"`, `model = "medium"`.
- **Pubs Twitch** : sans compte sub/Turbo, Streamlink saute les pubs, ce qui laisse des trous dans la vidéo. Renseigne `twitch.oauth_token` (cookie `auth-token` de twitch.tv) pour les éviter.
- **Disque** : compte environ 2,5 à 3 Go par heure en 1080p60. `export.delete_raw = true` supprime l'enregistrement brut une fois tous les clips générés.

## Upload TikTok

Le script passe par l'API officielle *Content Posting*, ce qui demande une appli développeur TikTok (gratuite) :

1. Sur https://developers.tiktok.com, crée une appli et ajoute les produits **Login Kit** et **Content Posting API**.
2. Ajoute l'URI de redirection `http://localhost:8723/callback/` (plateforme Desktop) et les scopes `user.info.basic` et `video.upload` (plus `video.publish` pour le mode `direct`).
3. Mets `client_key` et `client_secret` dans `config.toml` (ou dans les variables d'environnement `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`), puis lance `autoclip tiktok-auth`.

Modes disponibles :
- `inbox` (par défaut) : chaque clip arrive en **brouillon** dans l'appli TikTok (notification). Tu choisis le son, tu colles `caption.txt` et tu postes. Ce mode marche sans audit TikTok.
- `direct` : publication directe. Tant que TikTok n'a pas audité ton appli, les vidéos sont forcément **privées** (`SELF_ONLY`).

Avec `tiktok.auto_upload = true`, l'upload se fait tout seul à la fin de chaque live.

## Droits

Ces clips utilisent le contenu de JLTomy. Avant de les publier, vérifie que tu as son accord ou qu'il autorise les comptes de clips. Sans ça, TikTok peut retirer les vidéos ou bloquer le compte pour violation de droits d'auteur.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Les tests génèrent un faux live de 4 min avec ffmpeg et un faux chat contenant 3 moments de hype. Ils vérifient la détection, l'export 1080x1920 dans les 3 layouts, les sous-titres, la reprise sans réencodage et l'enchaînement des appels à l'API TikTok (simulée).
