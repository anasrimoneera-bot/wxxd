@echo off
rem ============================================================
rem  Build Auto-Favorite.exe on this PC
rem  - Uses its own private Python 3.11 (installed on first run,
rem    no admin rights, not added to PATH).
rem  - Builds in C:\AutoFavoriteBuild (English-only path), then copies
rem    the exe to: <project folder>\Auto-Favorite\Auto-Favorite.exe
rem    Settings, keywords and logs next to the exe are kept.
rem  - Full log: C:\AutoFavoriteBuild\build.log (opens on failure).
rem  This file is pure English (ASCII) on purpose.
rem ============================================================
setlocal
rem Read this file's folder once, before any cd: %~dp0 can change after cd
rem when this file is started with a quoted relative path (a cmd quirk).
set "HERE=%~dp0"
rem Windows' own tools first (some PCs have a PATH without System32: curl, tasklist, powershell...)
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\WindowsPowerShell\v1.0;%PATH%"
set "PYVER=3.11.9"
rem pip mirrors in China, tried in turn (a slow or broken download moves on to the next one)
set "PIPMIRRORS=https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.aliyun.com/pypi/simple/ https://mirrors.cloud.tencent.com/pypi/simple https://pypi.mirrors.ustc.edu.cn/simple"
set "PIPOPTS=--disable-pip-version-check --timeout 60 --retries 5"
if defined AF_PIP_MIRRORS set "PIPMIRRORS=%AF_PIP_MIRRORS%"
set "OUT=%HERE%..\..\Auto-Favorite"
set "RC=0"

rem English-only work folder (the project path may contain Chinese characters)
set "AFROOT=%SystemDrive%\AutoFavoriteBuild"
if defined AF_ROOT set "AFROOT=%AF_ROOT%"
if not exist "%AFROOT%" mkdir "%AFROOT%" 2>nul
if not exist "%AFROOT%" set "AFROOT=%LOCALAPPDATA%\AutoFavoriteBuild"
if not exist "%AFROOT%" mkdir "%AFROOT%"
set "PYDIR=%AFROOT%\python311"
set "PY=%PYDIR%\python.exe"
set "SRC=%AFROOT%\src"
set "LOG=%AFROOT%\build.log"

echo ============================================================
echo   Build Auto-Favorite.exe
echo   Work folder: %AFROOT%
echo ============================================================
echo Build started %DATE% %TIME% > "%LOG%"

if exist "%PY%" goto HAVEPY
echo.
echo [1/4] Installing a private Python %PYVER% for building (one time, about 25 MB)...
set "PYEXE=%AFROOT%\python-%PYVER%-amd64.exe"
if exist "%PYEXE%" goto INSTALLPY
set "DL_URL=https://registry.npmmirror.com/-/binary/python/%PYVER%/python-%PYVER%-amd64.exe"
call :DOWNLOAD
if errorlevel 1 set "DL_URL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
if errorlevel 1 call :DOWNLOAD
if errorlevel 1 goto NOPY
if not exist "%PYEXE%" goto NOPY
:INSTALLPY
start "" /wait "%PYEXE%" /quiet InstallAllUsers=0 TargetDir="%PYDIR%" Include_launcher=0 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Include_tcltk=1 Include_pip=1 PrependPath=0 Shortcuts=0 AssociateFiles=0 /log "%AFROOT%\python-install.log"
if not exist "%PY%" goto NOPY

:HAVEPY
echo [1/4] Python: %PY%
rem Copy the sources to the English-only work folder; everything below runs there.
if exist "%SRC%" rmdir /s /q "%SRC%"
mkdir "%SRC%"
copy /y "%HERE%*.py" "%SRC%\" >nul
if errorlevel 1 goto FAILED
copy /y "%HERE%requirements.txt" "%SRC%\" >nul
if errorlevel 1 goto FAILED
echo.
echo [2/4] Installing build packages (first time takes a few minutes)...
set "PIPDONE="
for %%M in (%PIPMIRRORS%) do call :PIPTRY %%M
if not defined PIPDONE call :PIPTRY https://pypi.org/simple
if not defined PIPDONE goto FAILED

echo.
echo [3/4] Building Auto-Favorite.exe (1-3 minutes)...
if exist "%AFROOT%\dist\Auto-Favorite.exe" del /f /q "%AFROOT%\dist\Auto-Favorite.exe"
pushd "%SRC%"
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name Auto-Favorite --collect-all rapidocr_onnxruntime --collect-binaries onnxruntime --distpath "%AFROOT%\dist" --workpath "%AFROOT%\work" --specpath "%AFROOT%\work" app.py >> "%LOG%" 2>&1
set "BUILDRC=%ERRORLEVEL%"
popd
if not "%BUILDRC%"=="0" goto FAILED
if not exist "%AFROOT%\dist\Auto-Favorite.exe" goto FAILED

echo.
echo [4/4] Copying to the Auto-Favorite folder...
if not exist "%OUT%" mkdir "%OUT%"
tasklist /fi "imagename eq Auto-Favorite.exe" 2>nul | find /i "Auto-Favorite.exe" >nul
if not errorlevel 1 goto RUNNING
copy /y "%AFROOT%\dist\Auto-Favorite.exe" "%OUT%\Auto-Favorite.exe" >nul
if errorlevel 1 goto FAILED
for %%I in ("%OUT%") do set "OUTFULL=%%~fI"
echo Build finished %DATE% %TIME% >> "%LOG%"
echo.
echo ============================================================
echo   Done: %OUTFULL%\Auto-Favorite.exe
echo ============================================================
if not defined AF_NOPAUSE start "" explorer "%OUTFULL%"
goto END

:RUNNING
echo.
echo Auto-Favorite.exe is still running. Close it, then run this build again.
echo (The new version is ready in: %AFROOT%\dist\Auto-Favorite.exe)
set "RC=1"
goto END

:NOPY
echo.
echo Could not install the private Python automatically (download blocked?).
echo You can download python-%PYVER%-amd64.exe yourself and put it in %AFROOT%, then run this again.
set "RC=1"
goto END

:FAILED
set "RC=1"
echo.
echo ============================================================
echo   Build failed. Last lines of the log:
echo ============================================================
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 25"
echo.
echo Full log: %LOG%
echo Please copy the log and send it for help.
if not defined CI start "" notepad "%LOG%"

:END
if not defined AF_NOPAUSE pause
exit /b %RC%

rem ------------------------------------------------------------
rem pip install from one mirror; sets PIPDONE on success. Already installed packages are kept,
rem so each try only downloads what is still missing. A broken download (hash mismatch) is
rem not reused: --no-cache-dir.
:PIPTRY
if defined PIPDONE exit /b 0
echo     trying %1
echo ---- pip install from %1 >> "%LOG%"
"%PY%" -m pip install %PIPOPTS% --no-cache-dir -i %1 -r "%SRC%\requirements.txt" pyinstaller >> "%LOG%" 2>&1
if not errorlevel 1 set "PIPDONE=1"
exit /b 0

rem ------------------------------------------------------------
rem Download %DL_URL% to %PYEXE%: curl.exe if Windows has it, else PowerShell.
:DOWNLOAD
echo Downloading %DL_URL%
if exist "%PYEXE%" del /f /q "%PYEXE%"
if defined AF_NO_CURL goto DL_PS
if not exist "%SystemRoot%\System32\curl.exe" goto DL_PS
"%SystemRoot%\System32\curl.exe" -fL --retry 3 -o "%PYEXE%" "%DL_URL%"
if not errorlevel 1 exit /b 0
:DL_PS
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12 } catch {}; Invoke-WebRequest -UseBasicParsing -Uri $env:DL_URL -OutFile $env:PYEXE"
if errorlevel 1 exit /b 1
if not exist "%PYEXE%" exit /b 1
exit /b 0
