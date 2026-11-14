@echo off
rem V-Concept Studio - Windows build script (messages are ASCII on purpose: cp949/UTF-8 safe)
rem Requires: Python 3.11+ (3.12 or 3.13 recommended) from python.org, with the "py" launcher.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.12+ from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
  )
  set "PY=python"
) else (
  set "PY=py -3"
)

echo [1/4] Creating build virtualenv (.venv-build)...
if not exist ".venv-build\Scripts\python.exe" (
  %PY% -m venv .venv-build
  if errorlevel 1 goto :fail
)
set "VPY=.venv-build\Scripts\python.exe"

echo [2/4] Installing dependencies...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements-dev.txt
if errorlevel 1 goto :fail

echo [3/4] Running tests (no network, no API cost)...
set QT_QPA_PLATFORM=offscreen
"%VPY%" -m pytest tests -q
if errorlevel 1 (
  echo [WARN] Some tests failed. The build continues, but please check the output above.
)
set QT_QPA_PLATFORM=

echo [4/4] Building EXE with PyInstaller...
"%VPY%" -m PyInstaller crs.spec --noconfirm --clean
if errorlevel 1 goto :fail

echo.
echo [OK] Build finished:
echo      %~dp0dist\VConceptStudio\VConceptStudio.exe
echo      Distribute the whole "dist\VConceptStudio" folder (zip it).
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed. See the messages above.
pause
exit /b 1
