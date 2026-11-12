@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3.10 app.py --seed) else (python app.py --seed)
if errorlevel 1 (echo Setup failed. Install Python 3.10 and retry.&pause&exit /b 1)
echo FinVault demo setup completed.
pause
