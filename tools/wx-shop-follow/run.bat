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

:menu
echo.
echo ==========================================
echo  [1] Probe    - read the scrcpy window once
echo  [2] Dry run  - everything except clicking favorite
echo  [3] Run      - favorite new stores (max 30)
echo  [0] Exit
echo  Stop any time: Ctrl+C, or move the mouse into a screen corner
echo ==========================================
set CHOICE=
set /p CHOICE=Choose: 
if "%CHOICE%"=="1" goto probe
if "%CHOICE%"=="2" goto dry
if "%CHOICE%"=="3" goto run
if "%CHOICE%"=="0" exit /b 0
goto menu

:probe
python follow_new_shops.py --probe
goto menu

:dry
python follow_new_shops.py --dry-run
goto menu

:run
python follow_new_shops.py --max 30
goto menu
