@echo off
cd /d "%~dp0"
python -c "import pystray, PIL" 2>nul || python -m pip install -r requirements.txt
start "" pythonw skill_viewer.py
