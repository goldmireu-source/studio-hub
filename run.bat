@echo off
title Studio Hub
cd /d "%~dp0"
if not exist "lib\" (
    echo First run on this PC. Installing packages... 5-10 min.
    python -m pip install --target=lib -r requirements.txt
)
set PYTHONPATH=%cd%\lib
start "" /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5000"
python app.py
pause