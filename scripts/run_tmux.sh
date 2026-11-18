#!/usr/bin/env bash
# Lance autoclip dans une session tmux, qui survit à la déconnexion (SSH, téléphone…).
# Usage : scripts/run_tmux.sh [commande autoclip]     (par défaut : live)
#   scripts/run_tmux.sh             → autoclip live (mode continu)
#   scripts/run_tmux.sh watch       → autoclip watch
# Voir ce qui se passe : tmux attach -t autoclip   (Ctrl+B puis D pour repartir sans l'arrêter)
# Arrêter : tmux attach -t autoclip, puis Ctrl+C.
set -eu
cd "$(dirname "$0")/.."
SESSION=${AUTOCLIP_SESSION:-autoclip}
AUTOCLIP="$PWD/.venv/bin/autoclip"

command -v tmux > /dev/null || { echo "tmux manquant : lance scripts/setup_server.sh"; exit 1; }
[ -x "$AUTOCLIP" ] || { echo "autoclip pas installé : lance scripts/setup_server.sh"; exit 1; }

# On cherche le processus python qui exécute autoclip (pas tmux, dont la commande contient le chemin).
if pgrep -f "^[^ ]*python[^ ]* $AUTOCLIP( |$)" > /dev/null; then
    echo "autoclip tourne déjà. Pour le voir : tmux attach -t $SESSION"
    exit 0
fi
tmux kill-session -t "=$SESSION" 2> /dev/null || true  # ancienne session arrêtée

[ $# -eq 0 ] && set -- live
cmd="$(printf '%q ' "$AUTOCLIP" "$@")"
# Après l'arrêt d'autoclip, la fenêtre reste ouverte pour pouvoir lire l'erreur.
tmux new-session -d -s "$SESSION" -c "$PWD" \
    "$cmd; echo; echo \">>> autoclip est arrêté (code \$?). Pour relancer : scripts/run_tmux.sh\"; exec bash"
echo "autoclip $* lancé dans tmux (session '$SESSION')."
echo "Voir : tmux attach -t $SESSION   |   journal : tail -f autoclip.log"
