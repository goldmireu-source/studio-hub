@echo off
chcp 65001 >nul
title Studio Hub
cd /d "%~dp0"

echo.
echo ============================================
echo   Studio Hub - NOIR GROOVE LAB
echo ============================================
echo.

REM ── Python 설치 확인 ──────────────────────────
where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python이 설치되어 있지 않습니다.
    echo.
    echo 외장하드 루트의 python-3.14.4-amd64.exe 를 먼저 실행하세요.
    echo 설치 시 반드시 "Add Python to PATH" 체크!
    echo.
    pause
    exit /b
)

REM ── 패키지 설치 (최초 1회) ─────────────────────
if not exist "lib\" (
    echo [최초 실행] 필요한 패키지 설치 중... 5~10분 걸립니다.
    echo.
    python -m pip install --upgrade pip
    python -m pip install --target=lib -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [!] 패키지 설치 실패. 에러 메시지를 확인하세요.
        echo     librosa 설치가 안 되면 Python 3.13 버전으로 다시 시도하세요.
        pause
        exit /b
    )
    echo.
    echo [완료] 패키지 설치 완료. 다음부터는 바로 실행됩니다.
    echo.
)

REM ── 패키지 경로 등록 후 실행 ───────────────────
set PYTHONPATH=%cd%\lib

echo Studio Hub 시작 중... http://localhost:5000
echo (창을 닫으면 서버가 종료됩니다)
echo.

REM 3초 후 브라우저 자동 오픈
start "" /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5000"

python app.py

pause
