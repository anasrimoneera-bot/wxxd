@echo off
chcp 65001 >nul
setlocal
rem ================================================================
rem  One-time setup: create an SSH key on this PC and install it on
rem  the server, so deploy-update.bat runs without a password.
rem  You will be asked for the server root password ONE time.
rem ================================================================
set "SERVER=root@101.126.155.252"
set "KEY=%USERPROFILE%\.ssh\id_ed25519"

if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"
if not exist "%KEY%" (
  echo Creating SSH key ...
  ssh-keygen -t ed25519 -N "" -f "%KEY%"
  if errorlevel 1 goto fail
) else (
  echo SSH key already exists, reusing it.
)

echo.
echo Installing key on %SERVER% - enter the server password when asked ...
type "%KEY%.pub" | ssh %SERVER% "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
if errorlevel 1 goto fail

echo.
echo Testing password-free login ...
ssh -o BatchMode=yes %SERVER% "echo LOGIN-OK"
if errorlevel 1 goto fail

echo.
echo DONE. deploy-update.bat will now run without a password.
pause
exit /b 0

:fail
echo.
echo FAILED. Read the messages above.
pause
exit /b 1
