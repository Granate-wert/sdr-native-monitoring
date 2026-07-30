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

"%PYTHON%" -c "import maturin" >nul 2>&1
if errorlevel 1 (
  echo Installing the Rust extension build frontend.
  "%PYTHON%" -m pip install --disable-pip-version-check "maturin>=1.7,<2.0"
  if errorlevel 1 exit /b 1
)

set "VSDEVCMD=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
if not exist "%VSDEVCMD%" (
  echo Visual Studio 2022 Build Tools with Desktop C++ are required.
  exit /b 1
)

call "%VSDEVCMD%" -arch=x64 -host_arch=x64 >nul
set "PATH=%PATH%;%USERPROFILE%\.cargo\bin"
where cargo >nul 2>&1
if errorlevel 1 (
  echo Rust x86_64-pc-windows-msvc toolchain was not found.
  exit /b 1
)

"%PYTHON%" -m maturin develop --manifest-path native\sgram_decoder\Cargo.toml --release
if errorlevel 1 exit /b 1
echo Native SgramLine decoder built for the active Python interpreter.

