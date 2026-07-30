@echo off
setlocal
cd /d "%~dp0"
py -c "import numpy, matplotlib, olefile, PIL, imageio_ffmpeg, PySide6, pyqtgraph" >nul 2>&1
if errorlevel 1 py -m pip install --disable-pip-version-check -r requirements.txt
py main.py
