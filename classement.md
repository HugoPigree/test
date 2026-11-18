# Classement des clips de `ready_to_post/`

Établi le 18/11/2026 à partir de `meta.json` (force du pic de chat), de la transcription Whisper,
de la durée et du début de chaque vidéo (premier sous-titre + détection d'image noire ou figée).
Aucun clip n'a été modifié.

Attention : les clips n°2, 3 et 4 sont **le même moment** de domingo en trois versions : il n'en
faut garder qu'un. Les clips `_test_live` viennent du test du mode continu sur le live de JLTomy.

| # | Score | Verdict | Vidéo | Pourquoi |
|---|------:|---------|-------|----------|
| 1 | 72/100 | Retravailler | `ready_to_post/_test_live/2026-11-18_17h22m56s_jltomy/video.mp4` | Le pic de chat le plus fort de tout le lot (45 messages, score 7,5), et le chat répète ce que dit JLTomy (« YA MIDAS ») : il croit voir Midas par la fenêtre, c'est une petite histoire compréhensible seule, avec un début propre (« Je crois, là je l'ai vu à la fenêtre »), mais l'accroche « Mais les mecs, les mecs, ils sont sans idées pour eux, mais… » ne veut rien dire. |
| 2 | 68/100 | Retravailler | `ready_to_post/2026-11-17_domingo_01_0h03m18s/video.mp4` | Pic de chat fort (25 messages, activité 7 fois la normale) sur une vraie réaction visible (carte de catch rare, « NONNN »), début propre sur « Oh bah là c'est numéro 10 », 35 s bien serrées, mais sans accroche on ne comprend pas qu'ils ouvrent des cartes de catch. |
| 3 | 60/100 | Retravailler | `ready_to_post/_test_options/2026-11-17_domingo_01_0h03m18s/video.mp4` | Même moment que le n°2, avec accroche, 30 s de contexte et zoom sur la réaction, mais la musique est mon accord de test synthétique (à ne pas poster), l'accroche commence par un tiret « – » (corrigé depuis dans le code) et Whisper a halluciné des « NON » à répétition dans les sous-titres. |
| 4 | 55/100 | Retravailler | `ready_to_post/_test_defaut/2026-11-17_domingo_01_0h03m18s/video.mp4` | Même moment encore, mais l'accroche « On a une... » ne veut rien dire (corrigé depuis : les phrases inachevées sont écartées) et les ~10 premières secondes sont du remplissage (« Tu peux croiser après… Tati Nati »). |
| 5 | 52/100 | Retravailler | `ready_to_post/_test_live/2026-11-18_17h37m33s_jltomy/video.mp4` | Le chat (score 7,0) réagit à l'arrivée d'un journaliste Konbini avec son micro, visible au moment du pic, mais JLTomy parle d'autre chose (horaires de GTA VI, VPN) sans chute, le clip commence en milieu de phrase et l'accroche « C'était dur de lâcher la mallette. » est hors sujet. |
| 6 | 35/100 | Jeter | `ready_to_post/2026-11-17_gotaga_03_0h06m54s/video.mp4` | Le chat rit vraiment (« LUL », « MDRRRR », « KEKW ») mais à cause d'une image du documentaire YouTube que Gotaga regarde. La transcription (agents Valorant, Neon, Chamber) n'a aucun intérêt seule et commence en milieu de phrase (« qui n'aiment pas forcément la cam »). |
| 7 | 30/100 | Jeter | `ready_to_post/2026-11-17_gotaga_02_0h02m13s/video.mp4` | Pic faible (14 messages). La blague (« bande de vermines » dans la traduction) est un sous-titre du documentaire, pas de Gotaga, qui parle d'autre chose (« j'en ai rencontré aucune »). Début en milieu de pensée. |
| 8 | 28/100 | Jeter | `ready_to_post/_test_live/2026-11-18_17h20m42s_jltomy/video.mp4` | Faux pic de démarrage (corrigé depuis) : détecté 34 s après le lancement de l'outil, sur une ligne de base de 30 s de chat. Les messages sont dispersés, sans réaction collective, la discussion (« un A dans son pseudo ») est incompréhensible seule et l'accroche « Pour vous, normalement. » ne dit rien. |
| 9 | 25/100 | Jeter | `ready_to_post/_test_live/2026-11-18_17h28m52s_jltomy/video.mp4` | Pic à peine au-dessus du seuil (score 3,6) sans réaction collective du chat, clip raccourci à 27 s parce qu'il commence pile à la reconnexion simulée du stream, et monologue décousu qui démarre en milieu de phrase (« mais tu me calcules pas »). |
| 10 | 15/100 | Jeter | `ready_to_post/2026-11-17_gotaga_01_0h00m11s/video.mp4` | Faux pic dans les 11 premières secondes de l'enregistrement : le chat ne réagit à rien (« la vie est belle », questions sur Rocket League). Discussion banale (« Merci Rebaphrom, moi ça va tranquille »), 17 s, début en milieu de phrase. |

## Remarques

- **Droits** : les clips n°2, 3, 4, 6, 7 et 10 viennent de domingo et de Gotaga, pas de JLTomy. Les clips gotaga
  montrent surtout un documentaire YouTube : contenu tiers, risque de retrait sur TikTok.
- **Pour retravailler le moment domingo (n°2)** : supprimer `ready_to_post/2026-11-17_domingo_01_0h03m18s/`
  puis relancer `autoclip process recordings/domingo_2026-11-17_17h55`. On obtient la version actuelle
  du code (accroche sans tiret, phrases inachevées écartées, pas de musique de test). Les « NON » en
  rafale viennent de Whisper et peuvent revenir.
- **Artefact de démarrage** (clips n°8 et 10) : juste après le lancement, la ligne de base du chat
  est calculée sur trop peu d'historique, et du chat normal passe pour un pic. En mode continu,
  c'est corrigé : rien n'est détecté avant 5 min de chat.
- **Accroches** : sur tous les clips JLTomy, l'accroche choisie automatiquement (une phrase
  de la transcription) n'explique pas la situation. C'est la limite d'une méthode sans modèle de
  langage : elle choisit une phrase, elle ne résume pas.
