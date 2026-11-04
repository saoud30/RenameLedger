@echo off
rem Builds dist\RenameLedger.exe (a single file you can share; no Python needed to run it).
cd /d "%~dp0"
py -m pip install . || goto :err
py -m pip install --upgrade pyinstaller || goto :err
py -m PyInstaller --noconfirm --onefile --windowed --paths src --name RenameLedger RenameLedger.pyw || goto :err
echo.
echo Built: %~dp0dist\RenameLedger.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
