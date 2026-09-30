#!/bin/bash
# Vérifie l'installation du POC (Python, bibliothèques, fichiers de démo, connexion à DeepSeek), sans clé API.
cd "$(dirname "$0")" || exit 1
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
"$PY" chiffrage_ia.py --diagnostic
echo
read -r -p "Appuyez sur Entrée pour fermer cette fenêtre."
