@echo off
rem Baut dist\LabManager.exe (laeuft ohne installiertes Python). Die Konfiguration liegt in %APPDATA%\LabManager\lab.json.
cd /d "%~dp0"
python -m pip install --user --quiet pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --uac-admin --name LabManager --paths . LabManager.pyw
echo.
echo Fertig: dist\LabManager.exe
