@echo off
title Riviu Reports - Khoi dong
color 06

set "UI_ORANGE="
set "UI_TEXT="
set "UI_OK="
set "UI_WARN="
set "UI_ERROR="
set "UI_DIM="
set "UI_RESET="
set "UI_ESC="
if defined WT_SESSION for /f "delims=" %%E in ('echo prompt $E^| cmd') do set "UI_ESC=%%E"
if not defined UI_ESC goto :ui_ready
set "UI_ORANGE=%UI_ESC%[38;2;255;107;0m"
set "UI_TEXT=%UI_ESC%[97m"
set "UI_OK=%UI_ESC%[92m"
set "UI_WARN=%UI_ESC%[93m"
set "UI_ERROR=%UI_ESC%[91m"
set "UI_DIM=%UI_ESC%[90m"
set "UI_RESET=%UI_ESC%[0m"

:ui_ready
call :render_banner "KHOI DONG HE THONG"
if /i "%~1"=="--ui-check" exit /b 0
goto :main

:render_banner
cls
echo %UI_ORANGE%========================================================================%UI_RESET%
echo %UI_ORANGE%   RIVIU REPORTS%UI_RESET%
echo %UI_TEXT%   %~1%UI_RESET%
echo %UI_ORANGE%========================================================================%UI_RESET%
echo.
exit /b 0

:main

:: Set directory
cd /d "%~dp0"

echo %UI_ORANGE%[1/4] KIEM TRA MOI TRUONG%UI_RESET%

:: Kiem tra xem moi truong ao (.venv) da duoc tao chua
if not exist ".venv\Scripts\activate.bat" (
    echo %UI_ERROR%[LOI]%UI_TEXT% Moi truong hoat dong chua duoc thiet lap.%UI_RESET%
    echo %UI_TEXT%      Chay "setup.bat" de cai dat ban dau.%UI_RESET%
    echo.
    pause
    exit /b 1
)

:: Kich hoat moi truong ao (.venv)
call .venv\Scripts\activate.bat
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

echo %UI_OK%[OK]%UI_TEXT% Da ket noi moi truong ao.%UI_RESET%
"%VENV_PY%" -c "from proxy_utils import PROXY_TEST_BUILD; print('[OK] Proxy test build:', PROXY_TEST_BUILD)"
echo %UI_ORANGE%[2/4] KIEM TRA THU VIEN%UI_RESET%
echo.

if not exist "%VENV_PY%" (
    echo %UI_ERROR%[LOI]%UI_TEXT% Khong tim thay Python trong .venv.%UI_RESET%
    echo %UI_TEXT%      Chay lai "setup.bat".%UI_RESET%
    echo.
    pause
    exit /b 1
)

"%VENV_PY%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Thieu FastAPI hoac Uvicorn.%UI_RESET%
    echo %UI_TEXT%            Dang cai lai thu vien tu requirements.txt...%UI_RESET%
    call .venv\Scripts\activate.bat
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo %UI_ERROR%[LOI]%UI_TEXT% Cai lai thu vien that bai.%UI_RESET%
        echo %UI_TEXT%      Chay lai setup.bat hoac kiem tra ket noi mang.%UI_RESET%
        echo.
        pause
        exit /b 1
    )
)

echo %UI_OK%[OK]%UI_TEXT% Thu vien san sang.%UI_RESET%
echo.
echo %UI_ORANGE%[3/4] KIEM TRA CONG 1231%UI_RESET%
set "PORT_PID="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 1231 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($conn) { $conn.OwningProcess }"`) do set "PORT_PID=%%P"

if defined PORT_PID (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Cong 1231 dang duoc PID %PORT_PID% su dung.%UI_RESET%
    echo %UI_TEXT%            Dang dung process cu...%UI_RESET%
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$targetPid = %PORT_PID%; try { Stop-Process -Id $targetPid -Force -ErrorAction Stop; Start-Sleep -Seconds 1; exit 0 } catch { exit 1 }"
    if errorlevel 1 (
        echo %UI_ERROR%[LOI]%UI_TEXT% Khong dung duoc process dang chiem cong 1231.%UI_RESET%
        echo %UI_TEXT%      Dong app do thu cong roi chay lai.%UI_RESET%
        echo.
        pause
        exit /b 1
    )

    set "PORT_PID="
    for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 1231 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($conn) { $conn.OwningProcess }"`) do set "PORT_PID=%%P"
    if defined PORT_PID (
        echo %UI_ERROR%[LOI]%UI_TEXT% Cong 1231 van bi PID %PORT_PID% chiem dung.%UI_RESET%
        echo %UI_TEXT%      Dong process thu cong roi chay lai.%UI_RESET%
        echo.
        pause
        exit /b 1
    )

    echo %UI_OK%[OK]%UI_TEXT% Da giai phong cong 1231.%UI_RESET%
    echo.
) else (
    echo %UI_OK%[OK]%UI_TEXT% Cong 1231 dang san sang.%UI_RESET%
    echo.
)

echo %UI_ORANGE%[4/4] KHOI DONG SERVER%UI_RESET%
echo %UI_OK%[OK]%UI_TEXT% Moi truong da san sang.%UI_RESET%
echo.
echo %UI_DIM%Dashboard: http://localhost:1231%UI_RESET%
echo %UI_DIM%Nhan Ctrl+C de dung server.%UI_RESET%
echo.

:: Open browser
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:1231"

:: Start application
"%VENV_PY%" app.py

echo.
echo %UI_ORANGE%========================================================================%UI_RESET%
echo %UI_WARN%   SERVER DA DUNG%UI_RESET%
echo %UI_ORANGE%========================================================================%UI_RESET%
pause
exit /b 0
