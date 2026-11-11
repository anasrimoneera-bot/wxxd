@echo off
chcp 65001 >nul
title WeChat Store Assistant - Deploy Tool
color 0A
setlocal

rem ============================================================
rem  Connection settings (change here if the server moves)
rem ============================================================
set "SERVER=root@101.126.155.252"
set "APP_DIR=/opt/wxxd"
set "SITE=https://wxxd.b2bsxlj.com"
set "PKG=%TEMP%\wxxd-release.tar.gz"
set "SSHOPT=-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30"

cd /d "%~dp0"

rem Put the Windows built-in OpenSSH on PATH (some PCs cannot find ssh)
if exist "%SystemRoot%\System32\OpenSSH\ssh.exe" set "PATH=%SystemRoot%\System32\OpenSSH;%PATH%"

ssh -V >nul 2>&1
if errorlevel 1 (
  echo ============================================================
  echo  ERROR: ssh command not found
  echo ============================================================
  echo  Install it: Settings -^> Apps -^> Optional features -^>
  echo  Add a feature -^> "OpenSSH Client", then open this file again.
  pause
  exit /b 1
)
git --version >nul 2>&1
if errorlevel 1 (
  echo ============================================================
  echo  ERROR: git command not found
  echo ============================================================
  echo  Install Git for Windows, then open this file again.
  pause
  exit /b 1
)
if not exist "deploy\remote.sh" (
  echo ERROR: deploy\remote.sh not found. Run this file from the project folder.
  pause
  exit /b 1
)

:MENU
cls
color 0A
echo ============================================================
echo         WeChat Store Assistant - Deploy Tool
echo ============================================================
echo   Server:  %SERVER%
echo   Folder:  %APP_DIR%
echo   Site:    %SITE%
echo ============================================================
echo.
echo   [1] Full deploy: backup + upload code from this PC + rebuild  ^<recommended^>
echo   [2] Deploy via server git pull (only if the server can reach GitHub)
echo   [3] Quick restart (no code change)
echo   [4] Status + disk
echo   [5] Live logs (Ctrl+C to stop)
echo   [6] Test SSH connection + key files
echo.
echo   --- Database backups ---
echo   [7] Back up database now
echo   [8] List backups
echo   [9] Restore database from a backup
echo   [C] Delete backups older than 7 days
echo.
echo   --- Auto-Favorite (desktop tool) ---
echo   [B] Update code + build Auto-Favorite.exe on this PC
echo.
echo   [0] Run a custom command on the server
echo   [Q] Quit
echo.
set "CHOICE="
set /p "CHOICE=Choose: "

if /i "%CHOICE%"=="1" goto DEPLOY
if /i "%CHOICE%"=="2" goto PULLDEPLOY
if /i "%CHOICE%"=="3" goto RESTART
if /i "%CHOICE%"=="4" goto STATUS
if /i "%CHOICE%"=="5" goto LOGS
if /i "%CHOICE%"=="6" goto TEST
if /i "%CHOICE%"=="7" goto BACKUP
if /i "%CHOICE%"=="8" goto LISTBK
if /i "%CHOICE%"=="9" goto ROLLBACK
if /i "%CHOICE%"=="C" goto CLEANBK
if /i "%CHOICE%"=="B" goto BUILDAF
if /i "%CHOICE%"=="0" goto CUSTOM
if /i "%CHOICE%"=="Q" goto END
echo.
echo Invalid choice. Enter a number from the menu or Q.
timeout /t 2 >nul
goto MENU

rem ============================================================
:BUILDAF
rem ============================================================
cls
echo ============================================================
echo            BUILD AUTO-FAVORITE.EXE ON THIS PC
echo ============================================================
echo  1. git pull on this PC (get the latest code)
echo  2. Build tools\wx-shop-follow  -^>  Auto-Favorite\Auto-Favorite.exe
echo     (first time: installs a private Python for building)
echo.
echo [1/2] git pull ...
git pull --ff-only
if errorlevel 1 echo git pull failed - building the code currently in this folder.
echo.
echo [2/2] Building ...
set "AF_NOPAUSE=1"
call "%CD%\tools\wx-shop-follow\build.bat"
set "AF_NOPAUSE="
echo.
pause
goto MENU

rem ============================================================
:DEPLOY
rem ============================================================
cls
echo ============================================================
echo                       FULL DEPLOY
echo ============================================================
echo  1. git pull on this PC (get the latest code from GitHub)
echo  2. Pack the code and upload it to the server (no GitHub needed there)
echo  3. Back up the database on the server
echo  4. Replace the code, rebuild and restart, health check
echo.
echo  Never touched on the server: .env, data/ (database, backups)
echo.
set "GO="
set /p "GO=Start? [Y/N]: "
if /i not "%GO%"=="Y" goto MENU
echo.
echo [1/3] git pull on this PC ...
git pull --ff-only
if not errorlevel 1 goto PULLED
echo.
echo git pull failed - GitHub may be slow from this PC.
set "GO="
set /p "GO=Deploy the code currently in this folder anyway? [Y/N]: "
if /i not "%GO%"=="Y" goto MENU
:PULLED
for /f %%i in ('git rev-parse --short HEAD') do set "REV=%%i"
echo Version: %REV%
echo.
echo [2/3] Packing and uploading ...
if exist "%PKG%" del /q "%PKG%"
git archive --format=tar.gz -o "%PKG%" HEAD
if errorlevel 1 goto FAILED
scp %SSHOPT% -q "%PKG%" %SERVER%:/tmp/wxxd-release.tar.gz
if errorlevel 1 goto FAILED
del /q "%PKG%"
echo.
echo [3/3] Installing and restarting on the server ...
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- deploy %REV%" < "deploy\remote.sh"
if errorlevel 1 goto FAILED
goto SUCCESS

rem ============================================================
:PULLDEPLOY
rem ============================================================
cls
echo ============================================================
echo              DEPLOY VIA SERVER GIT PULL
echo ============================================================
echo  The server fetches the code from GitHub itself. If it hangs or
echo  times out, GitHub is unreachable from the server: use [1].
echo.
for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set "BRANCH=%%b"
echo  Branch: %BRANCH%
echo.
set "GO="
set /p "GO=Start? [Y/N]: "
if /i not "%GO%"=="Y" goto MENU
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- pull-deploy %BRANCH%" < "deploy\remote.sh"
if errorlevel 1 goto FAILED
goto SUCCESS

rem ============================================================
:RESTART
rem ============================================================
cls
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- restart" < "deploy\remote.sh"
pause
goto MENU

rem ============================================================
:STATUS
rem ============================================================
cls
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- status" < "deploy\remote.sh"
echo.
pause
goto MENU

rem ============================================================
:LOGS
rem ============================================================
cls
echo === Live logs (Ctrl+C to stop; the app keeps running) ===
ssh %SSHOPT% -t %SERVER% "cd %APP_DIR% && docker compose logs -f --tail=40 api worker"
echo.
pause
goto MENU

rem ============================================================
:TEST
rem ============================================================
cls
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- test" < "deploy\remote.sh"
echo.
pause
goto MENU

rem ============================================================
:BACKUP
rem ============================================================
cls
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- backup" < "deploy\remote.sh"
echo.
pause
goto MENU

rem ============================================================
:LISTBK
rem ============================================================
cls
echo === Backups (newest 20) ===
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- list-backups" < "deploy\remote.sh"
echo.
pause
goto MENU

rem ============================================================
:ROLLBACK
rem ============================================================
cls
echo ============================================================
echo               RESTORE DATABASE FROM A BACKUP
echo ============================================================
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- list-backups" < "deploy\remote.sh"
echo.
echo Enter the backup file name to restore, e.g. wxxd-20261108-150000-before-deploy.sql.gz
set "BAKFILE="
set /p "BAKFILE=> "
if "%BAKFILE%"=="" goto MENU
echo.
echo  1. The current database is backed up first (so this can be undone)
echo  2. API and workers stop
echo  3. %BAKFILE% replaces the current database
echo  4. API and workers start again
echo.
set "GO="
set /p "GO=Restore now? [Y/N]: "
if /i not "%GO%"=="Y" goto MENU
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- rollback %BAKFILE%" < "deploy\remote.sh"
echo.
pause
goto MENU

rem ============================================================
:CLEANBK
rem ============================================================
cls
echo === Backups older than 7 days ===
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- clean-backups" < "deploy\remote.sh"
echo.
set "GO="
set /p "GO=Delete them? [Y/N]: "
if /i not "%GO%"=="Y" goto MENU
ssh %SSHOPT% %SERVER% "APP_DIR=%APP_DIR% bash -s -- clean-backups --yes" < "deploy\remote.sh"
echo.
pause
goto MENU

rem ============================================================
:CUSTOM
rem ============================================================
cls
echo === Run a custom command on the server ===
echo Examples:
echo   cd %APP_DIR% ^&^& docker compose ps
echo   cd %APP_DIR% ^&^& docker compose logs --tail=50 worker
echo   df -h /
echo.
set "CMD="
set /p "CMD=Command (Enter to cancel): "
if "%CMD%"=="" goto MENU
echo.
ssh %SSHOPT% %SERVER% "%CMD%"
echo.
pause
goto MENU

rem ============================================================
:SUCCESS
rem ============================================================
color 0A
echo.
echo ============================================================
echo  [SUCCESS] Deployed. Open %SITE%
echo  Press Ctrl+Shift+R in the browser to load the new version.
echo ============================================================
pause
goto MENU

rem ============================================================
:FAILED
rem ============================================================
color 0C
echo.
echo ============================================================
echo  [FAILED] Read the messages above. Common causes:
echo   - upload or ssh failed: check the network, run [6]
echo   - build failed while downloading: mirror slow, retry [1]
echo   - app does not start: run [5] for logs
echo   - data problem after deploy: [9] restores the last backup
echo ============================================================
pause
goto MENU

:END
endlocal
exit /b 0
