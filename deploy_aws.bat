@echo off
title AWS Lightsail Deployer
echo ==========================================================
echo  🚀 AWS Lightsail Deployment Script for KotakAlgo
echo ==========================================================

:: Step 1: Detect PEM file
set "PEM_FILE="
if exist LightsailDefaultKey-ap-south-1.pem (
    set "PEM_FILE=LightsailDefaultKey-ap-south-1.pem"
) else (
    for %%f in (*.pem) do (
        set "PEM_FILE=%%f"
    )
)

if "%PEM_FILE%"=="" (
    echo [ERROR] No .pem private key file found in C:\xampp\KotakAlgo!
    echo Please copy your downloaded .pem key file here and run this script again.
    pause
    exit /b
)

echo [INFO] Detected private key file: %PEM_FILE%

:: Step 1.5: Fix PEM file permissions (Strict Windows permissions)
echo [INFO] Restricting permissions of %PEM_FILE%...
icacls "%PEM_FILE%" /inheritance:r >nul 2>&1
icacls "%PEM_FILE%" /grant:r "%username%":(R) >nul 2>&1

:: Step 2: Get Server IP
set "SERVER_IP=13.205.8.34"
echo [INFO] Target Server IP: %SERVER_IP%

:: Step 3: Compress project files (excluding venv, logs, pycache)
echo [INFO] Compressing project files...
if exist KotakAlgo.zip del /f /q KotakAlgo.zip
powershell -Command "Get-ChildItem -Path . -Exclude 'venv', '.git', '__pycache__', '*.zip', '*.pem', 'bot.log', 'database.db' | Compress-Archive -DestinationPath KotakAlgo.zip -Force"

if not exist KotakAlgo.zip (
    echo [ERROR] Failed to create KotakAlgo.zip archive!
    pause
    exit /b
)
echo [INFO] Created KotakAlgo.zip successfully.

:: Step 4: Upload zip file to AWS VPS
echo [INFO] Uploading project files to AWS Lightsail VPS...
scp -o StrictHostKeyChecking=no -i %PEM_FILE% KotakAlgo.zip ubuntu@%SERVER_IP%:/home/ubuntu/

if %ERRORLEVEL% neq 0 (
    echo.
    echo [WARNING] SCP Upload failed.
    echo If it failed due to wrong key, please check if your instance uses the Default SSH key.
    echo If so, download 'LightsailDefaultKey-ap-south-1.pem' from the Lightsail keys page,
    echo copy it into C:\xampp\KotakAlgo, and run this script again.
    echo.
    pause
    exit /b
)

:: Step 5: Automatically execute remote deployment
echo [INFO] Upload completed. Running remote deployment script...
ssh -o StrictHostKeyChecking=no -i %PEM_FILE% ubuntu@%SERVER_IP% "sudo apt update && sudo apt install -y unzip && (unzip -o /home/ubuntu/KotakAlgo.zip -d /home/ubuntu/KotakAlgo || true) && cd /home/ubuntu/KotakAlgo && chmod +x deploy_vps.sh && ./deploy_vps.sh"

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Remote deployment execution failed!
    echo.
    pause
    exit /b
)

echo ==========================================================
echo  🎉 DEPLOYMENT COMPLETED SUCCESSFULLY!
echo ==========================================================
echo  Your code changes have been deployed and the server is restarted.
echo  Access the dashboard using: http://%SERVER_IP%
echo ==========================================================
pause
