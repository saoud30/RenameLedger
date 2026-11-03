@echo off
rem Opens RenameLedger. Needs Python 3.9+ from python.org (tick "Add to PATH" / keep the py launcher).
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw "RenameLedger.pyw" %* & exit /b)
where pythonw >nul 2>nul && (start "" pythonw "RenameLedger.pyw" %* & exit /b)
echo Python was not found. Install it from https://www.python.org/downloads/ and run this file again.
start "" https://www.python.org/downloads/
pause
