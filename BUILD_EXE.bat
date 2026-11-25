@echo off
title SK PDF Compressor - EXE Builder
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto fail
python make_icons.py || goto fail
python -m PyInstaller --noconfirm --onefile --windowed --name "SK PDF Compressor" --icon app.ico --add-data "app.ico;." --collect-all tkinterdnd2 app.py || goto fail
copy /y "dist\SK PDF Compressor.exe" . >nul
echo EXE ready. & pause & exit /b 0
:fail
echo Build failed. & pause & exit /b 1
