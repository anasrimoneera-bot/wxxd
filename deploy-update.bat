@echo off
chcp 65001 >nul
setlocal
rem ================================================================
rem  One-click update: pull latest code locally, then deploy on server.
rem  Double-click this file. Requires: Git for Windows, OpenSSH client.
rem  Run setup-ssh-key.bat once first to avoid typing the password.
rem ================================================================
set "SERVER=root@101.126.155.252"
set "REMOTE_DIR=/opt/wxxd"

cd /d "%~dp0"

echo.
echo [1/2] Updating local folder from GitHub ...
git pull --ff-only
if errorlevel 1 goto fail

echo.
echo [2/2] Deploying on server %SERVER% ...
ssh -o ServerAliveInterval=30 %SERVER% "bash %REMOTE_DIR%/deploy/update.sh"
if errorlevel 1 goto fail

echo.
echo ==============================================
echo  SUCCESS. Open https://wxxd.b2bsxlj.com
echo ==============================================
pause
exit /b 0

:fail
echo.
echo ==============================================
echo  FAILED. Read the messages above.
echo ==============================================
pause
exit /b 1
