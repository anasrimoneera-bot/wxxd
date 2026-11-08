@echo off
chcp 65001 >nul
setlocal
rem ================================================================
rem  One-click update. Double-click this file.
rem   1. Pull the latest code from GitHub into this folder.
rem   2. Pack it and upload it to the server over SSH.
rem      (The server never needs to reach GitHub.)
rem   3. Rebuild and restart the app on the server, then health check.
rem  Requires: Git for Windows, Windows OpenSSH client.
rem  Server state (.env, data/) is never touched; only code is replaced.
rem  Run setup-ssh-key.bat once first to avoid typing the password.
rem ================================================================
set "SERVER=root@101.126.155.252"
set "REMOTE_DIR=/opt/wxxd"
set "PKG=%TEMP%\wxxd-release.tar.gz"

cd /d "%~dp0"

echo.
echo [1/3] Updating local folder from GitHub ...
git pull --ff-only
if errorlevel 1 goto fail
for /f %%i in ('git rev-parse --short HEAD') do set "REV=%%i"
echo Version: %REV%

echo.
echo [2/3] Packing and uploading to %SERVER% ...
if exist "%PKG%" del /q "%PKG%"
git archive --format=tar.gz -o "%PKG%" HEAD
if errorlevel 1 goto fail
scp -q "%PKG%" %SERVER%:/tmp/wxxd-release.tar.gz
if errorlevel 1 goto fail
del /q "%PKG%"

echo.
echo [3/3] Installing and restarting on server ...
ssh -o ServerAliveInterval=30 %SERVER% "set -e; mkdir -p %REMOTE_DIR%; cd %REMOTE_DIR%; rm -rf backend frontend deploy; tar -xzf /tmp/wxxd-release.tar.gz -C %REMOTE_DIR%; rm -f /tmp/wxxd-release.tar.gz; bash %REMOTE_DIR%/deploy/update.sh --no-pull %REV%"
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
