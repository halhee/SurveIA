@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import openpyxl" 2>nul || python -m pip install openpyxl
python -c "import lecture_cctp, sys; sys.exit(0 if lecture_cctp.pypdf_a_jour() else 1)" 2>nul || python -m pip install --upgrade "pypdf>=6.19.0"
python chiffrage_ia.py
pause
