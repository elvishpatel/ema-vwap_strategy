@echo off
cd /d "%~dp0"
if not exist .venv (
  py -3.11 -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt >nul 2>&1
if not exist .env copy .env.example .env
python live_scanner.py --market SENSEX --filter trend
pause
