@echo off
if exist "%~dp0app.py" (
    cd /d "%~dp0"
) else if exist "C:\xampp\KotakAlgo\app.py" (
    cd /d "C:\xampp\KotakAlgo"
) else (
    echo ==========================================================
    echo  ERROR: app.py not found!
    echo  Please ensure the project is located in C:\xampp\KotakAlgo
    echo ==========================================================
    pause
    exit /b
)
title Kotak Securities Neo Algo Console Setup
echo ==========================================================
echo  Installing Python Dependencies for Kotak Neo Bot...
echo ==========================================================
echo.
pip install -r requirements.txt
echo.
echo ==========================================================
echo  Starting Algo Console Web Server on http://localhost:5000
echo  Please keep this window open while using the platform.
echo ==========================================================
echo.
python app.py
pause
