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
"%VENV_PY%" -c "from proxy_utils import PROXY_TEST_BUILD; print('[OK] Proxy test build:', PROXY_TEST_BUILD)"
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

echo [OK] Dang kiem tra cong 1231...
set "PORT_PID="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 1231 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($conn) { $conn.OwningProcess }"`) do set "PORT_PID=%%P"

if defined PORT_PID (
    echo [WARN] Cong 1231 dang duoc su dung boi PID %PORT_PID%. Dang dung process cu...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$targetPid = %PORT_PID%; try { Stop-Process -Id $targetPid -Force -ErrorAction Stop; Start-Sleep -Seconds 1; exit 0 } catch { exit 1 }"
    if errorlevel 1 (
        echo [ERROR] Khong the dung process dang chiem cong 1231.
        echo Vui long dong thu cong app dang dung cong nay roi chay lai.
        echo.
        pause
        exit /b 1
    )

    set "PORT_PID="
    for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 1231 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($conn) { $conn.OwningProcess }"`) do set "PORT_PID=%%P"
    if defined PORT_PID (
        echo [ERROR] Cong 1231 van dang bi chiem sau khi dung process cu.
        echo Vui long dong thu cong process PID %PORT_PID% roi chay lai.
        echo.
        pause
        exit /b 1
    )

    echo [OK] Da giai phong cong 1231.
    echo.
) else (
    echo [OK] Cong 1231 dang san sang.
    echo.
)

echo [OK] Khoi dong server...
echo.
echo Dashboard: http://localhost:1231
echo.

:: Open browser
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:1231"

:: Start application
"%VENV_PY%" app.py

echo.
echo ============================================
echo    SERVER STOPPED
echo ============================================
pause
