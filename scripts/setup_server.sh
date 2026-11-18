#!/usr/bin/env bash
# Installe twitch-autoclip sur cette machine, sans sudo. Relançable sans risque (idempotent).
# Usage : scripts/setup_server.sh      (variable PYTHON=python3.12 pour choisir l'interpréteur)
set -u
cd "$(dirname "$0")/.."

missing=()
ok()   { echo "  [OK] $1"; }
miss() { echo "  [!!] $1"; echo "       → $2"; missing+=("$1"); }

echo "== Outils système"
PY=${PYTHON:-python3}
if command -v "$PY" > /dev/null && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    ok "$("$PY" --version) ($PY)"
else
    miss "Python 3.11 ou plus" "installe-le, ou relance avec PYTHON=python3.12 scripts/setup_server.sh"
fi
if command -v ffmpeg > /dev/null && command -v ffprobe > /dev/null; then
    ok "$(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
    if ffmpeg -hide_banner -filters 2> /dev/null | grep -q " ass "; then
        ok "ffmpeg sait incruster les sous-titres (libass)"
    else
        miss "ffmpeg avec libass" "il faut un ffmpeg compilé avec libass (les builds statiques l'ont)"
    fi
else
    miss "ffmpeg / ffprobe" "avec sudo : sudo apt install ffmpeg ; sans sudo : build statique \
(https://johnvansickle.com/ffmpeg/), copie ffmpeg et ffprobe dans ~/.local/bin (dans le PATH)"
fi
if command -v tmux > /dev/null; then
    ok "tmux $(tmux -V | cut -d' ' -f2)"
else
    miss "tmux" "nécessaire pour scripts/run_tmux.sh (sudo apt install tmux, ou demande à l'admin)"
fi
echo "  [..] espace libre ici : $(df -h . | awk 'NR == 2 {print $4}') (compter ~3 Go pour le buffer du mode live)"

echo "== Environnement Python (.venv)"
if [ ! -x .venv/bin/python ]; then
    if ! "$PY" -m venv .venv; then
        miss "création du venv" "sur Debian/Ubuntu il manque le paquet python3-venv (sudo apt install python3-venv)"
    fi
fi
if [ -x .venv/bin/python ]; then
    if .venv/bin/python -m pip install -q -e ".[whisper]"; then
        ok "dépendances installées (pip install -e \".[whisper]\")"
    else
        miss "dépendances Python" "regarde l'erreur pip ci-dessus"
    fi
fi

echo "== Configuration"
if [ -f config.toml ]; then
    ok "config.toml existe déjà (non modifié)"
else
    cp config.example.toml config.toml && ok "config.toml créé depuis config.example.toml"
fi

if [ -x .venv/bin/autoclip ]; then
    echo "== Modèle Whisper"
    model=$(.venv/bin/python -c "from autoclip.config import load_config; print(load_config().subtitles.model)")
    if .venv/bin/python -c "import sys; from faster_whisper import download_model; download_model(sys.argv[1])" "$model"; then
        ok "modèle Whisper '$model' téléchargé (cache ~/.cache/huggingface)"
    else
        miss "modèle Whisper '$model'" "vérifie l'accès réseau à huggingface.co puis relance ce script"
    fi

    echo "== autoclip check --live"
    if ! .venv/bin/autoclip check --live; then
        miss "autoclip check" "voir les lignes [!!] juste au-dessus"
    fi
fi

echo
if [ ${#missing[@]} -eq 0 ]; then
    echo "Tout est prêt. Lancement : scripts/run_tmux.sh"
else
    echo "Il manque ${#missing[@]} chose(s) :"
    printf '  - %s\n' "${missing[@]}"
    exit 1
fi
