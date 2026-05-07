@echo off
title TikTok Analytics - Starting...
color 0B

echo ============================================
echo    TIKTOK ANALYTICS - KHOI DONG
echo ============================================
echo.

:: Set directory
cd /d "%~dp0"

:: Kiem tra xem moi truong ao (.venv) da duoc tao chua
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Moi truong hoat dong chua duoc thiet lap.
    echo Vui long chay file "setup.bat" de cai dat ban dau truoc nhe!
    echo.
    pause
    exit /b 1
)

:: Kich hoat moi truong ao (.venv)
call .venv\Scripts\activate.bat
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

echo [OK] Da ket noi moi truong ao.
echo [OK] Dang kiem tra thu vien...
echo.

if not exist "%VENV_PY%" (
    echo [ERROR] Khong tim thay Python trong .venv
    echo Vui long chay lai file "setup.bat".
    echo.
    pause
    exit /b 1
)

"%VENV_PY%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Moi truong ao dang thieu FastAPI hoac Uvicorn.
    echo Dang thu cai lai thu vien tu requirements.txt...
    call .venv\Scripts\activate.bat
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Cai lai thu vien that bai.
        echo Vui long chay lai setup.bat hoac kiem tra ket noi mang.
        echo.
        pause
        exit /b 1
    )
)

echo [OK] Khoi dong server...
echo.
echo Dashboard: http://localhost:8000
echo.

:: Open browser
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"

:: Start application
"%VENV_PY%" app.py

echo.
echo ============================================
echo    SERVER STOPPED
echo ============================================
pause
