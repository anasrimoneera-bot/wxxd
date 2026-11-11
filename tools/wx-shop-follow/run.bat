@echo off
rem wx-shop-follow: favorite new WeChat stores through the scrcpy window
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10-3.12 from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

echo Checking Python packages (first run downloads the OCR model, a few minutes)...
python -m pip install -q -r requirements.txt
if not exist keywords.txt copy keywords.example.txt keywords.txt >nul

:menu
echo.
echo ==========================================
echo  [1] Probe     - read the scrcpy window once
echo  [2] Keywords  - edit the search keywords (keywords.txt)
echo  [3] Dry run   - keywords, but never click favorite
echo  [4] Run       - keywords, 30 new stores each, then stop
echo  [5] Home feed - no keywords, browse the home list (max 30)
echo  [0] Exit
echo  Stop any time: Ctrl+C, or move the mouse into a screen corner
echo ==========================================
set CHOICE=
set /p CHOICE=Choose: 
if "%CHOICE%"=="1" goto probe
if "%CHOICE%"=="2" goto words
if "%CHOICE%"=="3" goto dry
if "%CHOICE%"=="4" goto run
if "%CHOICE%"=="5" goto home
if "%CHOICE%"=="0" exit /b 0
goto menu

:probe
python follow_new_shops.py --probe
goto menu

:words
notepad keywords.txt
goto menu

:dry
python follow_new_shops.py --dry-run
goto menu

:run
python follow_new_shops.py --per-keyword 30
goto menu

:home
python follow_new_shops.py --home --max 30
goto menu
