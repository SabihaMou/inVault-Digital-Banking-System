@echo off
cd /d "%~dp0"
start "" http://127.0.0.1:8000
where py >nul 2>nul
if %errorlevel%==0 (py -3.10 app.py) else (python app.py)
if errorlevel 1 (echo FinVault failed to start.&pause)
