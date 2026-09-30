#!/bin/bash
# Lanceur Mac du POC Chiffrage IA : double-cliquez sur ce fichier dans le Finder.
# (La première fois : clic droit > Ouvrir, pour passer l'avertissement de sécurité de macOS.)

cd "$(dirname "$0")" || exit 1

# On cherche Python 3 : python3 d'abord (Python officiel, Homebrew, miniforge...), sinon python.
PY=""
for candidat in python3 python; do
  if command -v "$candidat" >/dev/null 2>&1 && "$candidat" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$candidat"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.9 ou plus récent est introuvable."
  echo "Installez-le depuis https://www.python.org/downloads/macos/ puis relancez ce fichier."
  read -r -p "Appuyez sur Entrée pour fermer."
  exit 1
fi

echo "Python utilisé : $("$PY" --version) ($(command -v "$PY"))"

# Dépendances : openpyxl (Excel) et pypdf (CCTP en PDF), installées si elles manquent (voir requirements.txt).
# Plusieurs tentatives, car selon l'origine de Python (python.org, Homebrew, miniforge...)
# l'installation normale ou « --user » peut être refusée.
installer() {
  "$PY" -c "import $1" 2>/dev/null && return 0
  echo "Installation de $1…"
  "$PY" -m pip install -q "$1" 2>/dev/null \
    || "$PY" -m pip install -q --user "$1" 2>/dev/null \
    || "$PY" -m pip install -q --user --break-system-packages "$1" \
    || echo "Impossible d'installer $1 automatiquement : tapez  $PY -m pip install $1"
}
installer openpyxl
installer pypdf
# pypdf < 6.19 : failles connues (PDF piégés) -> mise à jour automatique
if ! "$PY" -c "import lecture_cctp, sys; sys.exit(0 if lecture_cctp.pypdf_a_jour() else 1)" 2>/dev/null; then
  echo "Mise à jour de sécurité de pypdf…"
  "$PY" -m pip install -q --upgrade "pypdf>=6.19.0" 2>/dev/null \
    || "$PY" -m pip install -q --user --upgrade "pypdf>=6.19.0" 2>/dev/null \
    || "$PY" -m pip install -q --user --break-system-packages --upgrade "pypdf>=6.19.0" \
    || echo "Mise à jour impossible : tapez  $PY -m pip install --upgrade \"pypdf>=6.19.0\""
fi

"$PY" chiffrage_ia.py

echo
read -r -p "Application arrêtée. Appuyez sur Entrée pour fermer cette fenêtre."
