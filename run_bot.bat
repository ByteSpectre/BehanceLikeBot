@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found: .venv\Scripts\python.exe
    echo Follow the installation steps in README.md first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
