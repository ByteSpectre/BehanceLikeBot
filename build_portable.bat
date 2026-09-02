@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo UV is not installed or is not available in PATH.
    echo Install it from https://docs.astral.sh/uv/ and try again.
    pause
    exit /b 1
)

if not exist ".build-venv\Scripts\python.exe" (
    uv --cache-dir ".build-cache" venv --python 3.12 ".build-venv"
    if errorlevel 1 goto :error
)

uv --cache-dir ".build-cache" pip install --python ".build-venv\Scripts\python.exe" ^
    "telethon>=1.36,<2" "selenium>=4.22,<5" ^
    "PySide6-Essentials==6.8.3" "PyInstaller==6.22.2"
if errorlevel 1 goto :error

set PYTHONDONTWRITEBYTECODE=1
".build-venv\Scripts\python.exe" -m unittest discover -s tests
if errorlevel 1 goto :error

".build-venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm --clean --windowed --noupx ^
    --collect-submodules selenium.webdriver ^
    --name BehancerBot ^
    --distpath "release\dist" ^
    --workpath "release\build" ^
    --specpath "release" ^
    app.py
if errorlevel 1 goto :error

copy /y "PORTABLE_README.txt" "release\dist\BehancerBot\README.txt" >nul
powershell -NoProfile -Command ^
    "Compress-Archive -Path 'release\dist\BehancerBot\*' -DestinationPath 'release\BehancerBot-portable-v1.5.2.zip' -Force"
if errorlevel 1 goto :error

echo.
echo Build completed:
echo release\BehancerBot-portable-v1.5.2.zip
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
