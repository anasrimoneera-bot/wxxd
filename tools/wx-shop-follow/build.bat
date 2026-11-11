@echo off
rem ============================================================
rem  Build Auto-Favorite.exe on this PC
rem  - First run: installs a private Python 3.11 only for building
rem    (no admin rights, not added to PATH), then builds.
rem  - Output: <project folder>\Auto-Favorite\Auto-Favorite.exe
rem    Settings, keywords and logs next to the exe are kept.
rem  This file is pure English (ASCII) on purpose.
rem ============================================================
setlocal
cd /d "%~dp0"
set "PYVER=3.11.9"
set "PYDIR=%LOCALAPPDATA%\AutoFavorite\python311"
set "PIPMIRROR=https://mirrors.aliyun.com/pypi/simple/"
set "OUT=%~dp0..\..\Auto-Favorite"
set "PY="
set "RC=0"

echo ============================================================
echo   Build Auto-Favorite.exe
echo ============================================================

if defined AF_FORCE_PRIVATE goto PRIVATE
rem A suitable Python (3.10 - 3.12 with tkinter) already installed?
python -c "import sys,tkinter; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto HAVEPY

:PRIVATE
if exist "%PYDIR%\python.exe" set "PY=%PYDIR%\python.exe"
if defined PY goto HAVEPY
echo.
echo [1/4] Installing a private Python %PYVER% for building (one time, about 25 MB)...
set "PYEXE=%TEMP%\python-%PYVER%-amd64.exe"
curl.exe -fL --retry 3 -o "%PYEXE%" "https://registry.npmmirror.com/-/binary/python/%PYVER%/python-%PYVER%-amd64.exe"
if errorlevel 1 curl.exe -fL --retry 3 -o "%PYEXE%" "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
if errorlevel 1 goto NOPY
start "" /wait "%PYEXE%" /quiet InstallAllUsers=0 TargetDir="%PYDIR%" Include_launcher=0 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Include_tcltk=1 Include_pip=1 PrependPath=0 Shortcuts=0 AssociateFiles=0
if not exist "%PYDIR%\python.exe" goto NOPY
set "PY=%PYDIR%\python.exe"

:HAVEPY
echo Python: %PY%
echo.
echo [2/4] Installing build packages (first time takes a few minutes)...
"%PY%" -m pip install -q --disable-pip-version-check -i %PIPMIRROR% -r requirements.txt pyinstaller
if errorlevel 1 "%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 goto FAILED

echo.
echo [3/4] Building Auto-Favorite.exe (1-3 minutes)...
"%PY%" -m PyInstaller --noconfirm --log-level WARN --onefile --windowed --name Auto-Favorite --collect-all rapidocr_onnxruntime --collect-binaries onnxruntime --distpath "%~dp0dist" --workpath "%TEMP%\af-build" --specpath "%TEMP%\af-build" "%~dp0app.py"
if errorlevel 1 goto FAILED
if not exist "%~dp0dist\Auto-Favorite.exe" goto FAILED

echo.
echo [4/4] Copying to the Auto-Favorite folder...
if not exist "%OUT%" mkdir "%OUT%"
tasklist /fi "imagename eq Auto-Favorite.exe" 2>nul | find /i "Auto-Favorite.exe" >nul
if not errorlevel 1 goto RUNNING
copy /y "%~dp0dist\Auto-Favorite.exe" "%OUT%\Auto-Favorite.exe" >nul
if errorlevel 1 goto FAILED
for %%I in ("%OUT%") do set "OUTFULL=%%~fI"
echo.
echo ============================================================
echo   Done: %OUTFULL%\Auto-Favorite.exe
echo ============================================================
if not defined AF_NOPAUSE start "" explorer "%OUTFULL%"
goto END

:RUNNING
echo.
echo Auto-Favorite.exe is still running. Close it, then run this build again.
echo (The new version is ready in: %~dp0dist\Auto-Favorite.exe)
set "RC=1"
goto END

:NOPY
echo.
echo Could not install Python automatically (download blocked?).
echo Install Python 3.11 from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.
set "RC=1"
goto END

:FAILED
echo.
echo Build failed. See the messages above.
set "RC=1"

:END
if not defined AF_NOPAUSE pause
exit /b %RC%
