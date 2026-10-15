@echo off
rem One-command setup: create the virtual env, install dependencies, start the tray app.
cd /d "%~dp0"
if not exist .venv python -m venv .venv || exit /b 1
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt || exit /b 1
start "" .venv\Scripts\pythonw.exe app.pyw
echo Owl Cam Control is running - look for the blue camera icon in the taskbar tray.
