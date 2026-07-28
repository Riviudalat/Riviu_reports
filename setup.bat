@echo off
title Riviu Reports - Cai dat
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
call :render_banner "CAI DAT HE THONG"
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

cd /d "%~dp0"

echo %UI_ORANGE%[1/4] TIM PYTHON%UI_RESET%

:: 1. TÌM PYTHON
set "PY="
for %%x in (py python python3) do (
    %%x --version >nul 2>&1
    if not errorlevel 1 (
        set "PY=%%x"
        goto :found_python
    )
)

:: Nếu không có trong PATH, tìm trong các thư mục cài đặt phổ biến
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
) do (
    if exist "%%~P" (
        set "PY=%%~P"
        goto :found_python
    )
)

:not_found
echo %UI_WARN%[CANH BAO]%UI_TEXT% Khong tim thay Python tren may.%UI_RESET%
echo %UI_TEXT%Dang cai dat Python 3.13 bang winget...%UI_RESET%
winget install Python.Python.3.13 --accept-package-agreements --silent
echo.
echo %UI_OK%[OK]%UI_TEXT% Lenh cai dat Python da hoan tat.%UI_RESET%
echo %UI_WARN%Dong cua so nay, sau do chay lai setup.bat.%UI_RESET%
pause
exit /b 1

:found_python
echo %UI_OK%[OK]%UI_TEXT% Tim thay Python: %PY%%UI_RESET%
echo.

:: 2. TẠO MÔI TRƯỜNG ẢO (VIRTUAL ENVIRONMENT)
echo %UI_ORANGE%[2/4] TAO MOI TRUONG AO%UI_RESET%
if not exist ".venv" (
    echo %UI_TEXT%Dang tao .venv de ung dung hoat dong doc lap...%UI_RESET%
    "%PY%" -m venv .venv
    if errorlevel 1 (
        echo %UI_ERROR%[LOI]%UI_TEXT% Tao moi truong ao that bai.%UI_RESET%
        pause
        exit /b 1
    )
    echo %UI_OK%[OK]%UI_TEXT% Da tao moi truong ao.%UI_RESET%
) else (
    echo %UI_OK%[OK]%UI_TEXT% Moi truong ao da ton tai.%UI_RESET%
)
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo %UI_ERROR%[LOI]%UI_TEXT% Khong tim thay Python trong .venv.%UI_RESET%
    pause
    exit /b 1
)

:: 3. CÀI ĐẶT THƯ VIỆN CỐ ĐỊNH TỪ REQUIREMENTS.TXT
echo.
echo %UI_ORANGE%[3/4] CAI THU VIEN%UI_RESET%
echo %UI_TEXT%Dang cap nhat pip va cai requirements.txt...%UI_RESET%
call .venv\Scripts\activate.bat
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo %UI_ERROR%[LOI]%UI_TEXT% Cai thu vien tu requirements.txt that bai.%UI_RESET%
    pause
    exit /b 1
)
echo %UI_OK%[OK]%UI_TEXT% Thu vien Python da san sang.%UI_RESET%

:: 4. CÀI ĐẶT TRÌNH DUYỆT CHROME/CHROMIUM
echo.
echo %UI_ORANGE%[4/4] CAI TRINH DUYET%UI_RESET%
echo %UI_TEXT%Dang cai Chromium cho Playwright...%UI_RESET%
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo %UI_ERROR%[LOI]%UI_TEXT% Cai Chromium cho Playwright that bai.%UI_RESET%
    pause
    exit /b 1
)
echo %UI_OK%[OK]%UI_TEXT% Chromium da san sang.%UI_RESET%

echo.
echo %UI_ORANGE%KIEM TRA MOI TRUONG%UI_RESET%
"%VENV_PY%" -c "import fastapi, uvicorn, pandas, openpyxl, playwright, PIL, jinja2, multipart, googleapiclient, google.auth, google_auth_oauthlib, socks"
if errorlevel 1 (
    echo.
    echo %UI_ERROR%[LOI]%UI_TEXT% Moi truong van con loi import.%UI_RESET%
    echo %UI_TEXT%      Chay lai setup.bat hoac kiem tra Python.%UI_RESET%
    pause
    exit /b 1
)

echo.
echo %UI_ORANGE%========================================================================%UI_RESET%
echo %UI_OK%   CAI DAT HOAN TAT%UI_RESET%
echo %UI_ORANGE%========================================================================%UI_RESET%
echo %UI_TEXT%Chay "Khoidong.bat" de mo Riviu Reports.%UI_RESET%
echo.
pause
exit /b 0
