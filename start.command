#!/bin/bash
# macOS: double-click to start (installs the packages first if they are missing)
cd "$(dirname "$0")"
python3 -c "import pystray, PIL" 2>/dev/null || python3 -m pip install -r requirements.txt
nohup python3 skill_viewer.py >/dev/null 2>&1 &
