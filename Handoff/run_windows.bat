@echo off
setlocal
cd /d "%~dp0"
echo.
echo  H A N D O F F
echo  Keep the context moving.
echo.
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py") else (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creating an isolated Python environment...
  %PY% -m venv .venv
  if errorlevel 1 goto error
)
echo [2/2] Checking dependencies...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
echo.
echo Starting Handoff. Your browser will open after the server starts.
echo Keep this window open while using the app.
.venv\Scripts\python.exe app.py
goto end
:error
echo.
echo Setup failed. Install Python 3.10+ from https://www.python.org/downloads/ and try again.
pause
:end
endlocal
