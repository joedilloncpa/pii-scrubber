@echo off
REM Double-click launcher for Windows. First run installs what it needs (a minute or two).
cd /d "%~dp0"
title PII Scrubber
echo PII Scrubber
echo ------------

set PY=
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1 && set PY=py -3
if "%PY%"=="" python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1 && set PY=python
if "%PY%"=="" (
  echo.
  echo Python 3 isn't installed yet. Opening the download page...
  echo Install it, and on the first screen tick "Add python.exe to PATH". Then double-click this file again.
  start https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

if not exist ".venv" (
  echo First run: setting things up. This takes a minute or two and only happens once.
  %PY% -m venv .venv
  if errorlevel 1 ( echo Could not create the Python environment. & pause & exit /b 1 )
)

if not exist ".venv\.installed" (
  echo Installing components...
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
  if errorlevel 1 ( echo Install failed. Check your internet connection and try again. & pause & exit /b 1 )
  type nul > ".venv\.installed"
)

REM Skip Streamlit's one-time "enter your email" prompt
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
  echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
)
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

echo.
echo Starting. Your browser will open in a moment.
echo Leave this window open while you use the scrubber. Close it when you're done.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
