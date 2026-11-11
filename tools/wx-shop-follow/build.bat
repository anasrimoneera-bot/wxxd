@echo off
rem Build Auto-Favorite.exe on this PC (Windows, Python 3.10-3.12).
rem Output: dist\Auto-Favorite.exe
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10-3.12 from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

echo Installing build tools...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto failed

echo Building Auto-Favorite.exe (a few minutes)...
python -m PyInstaller --noconfirm --onefile --windowed --name Auto-Favorite --collect-all rapidocr_onnxruntime --collect-binaries onnxruntime app.py
if errorlevel 1 goto failed

echo.
echo Done: %~dp0dist\Auto-Favorite.exe
pause
exit /b 0

:failed
echo.
echo Build failed. See the messages above.
pause
exit /b 1
