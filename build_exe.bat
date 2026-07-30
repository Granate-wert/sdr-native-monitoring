@echo off
setlocal
cd /d "%~dp0"
if exist "%CD%\.venv\Scripts\python.exe" (
  set "PYTHON=%CD%\.venv\Scripts\python.exe"
) else (
  set "PYTHON=py"
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python launcher and project virtual environment were not found.
    exit /b 1
  )
)
call build_native_decoder.bat
if errorlevel 1 exit /b 1
set "NATIVE_DECODER="
for %%F in ("%CD%\esw_dfl\_sgram_native*.pyd") do set "NATIVE_DECODER=%%~fF"
if not defined NATIVE_DECODER (
  echo Native SgramLine decoder was not found after build.
  exit /b 1
)
"%PYTHON%" -c "import numpy, matplotlib, olefile, PIL, imageio_ffmpeg, PySide6, pyqtgraph, PyInstaller" >nul 2>&1
if errorlevel 1 (
  echo Installing missing build dependencies. Slow links are supported; already installed packages are reused.
  "%PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
  if errorlevel 1 exit /b 1
)
"%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name ESW_DFL_Analyzer ^
  --add-binary "%NATIVE_DECODER%;esw_dfl" ^
  --hidden-import esw_dfl._sgram_native ^
  --collect-all imageio_ffmpeg ^
  --exclude-module scipy ^
  --exclude-module pytest ^
  --exclude-module pyqtgraph.examples ^
  --exclude-module pyqtgraph.opengl ^
  main.py
if errorlevel 1 exit /b 1
echo.
echo Built: "%CD%\dist\ESW_DFL_Analyzer.exe"



