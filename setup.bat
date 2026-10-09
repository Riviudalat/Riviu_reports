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

:: 1. THU VIEN PYTHON + CHROMIUM (thuvien.bat)
:: Uu tien uv + uv.lock (tu cai uv neu thieu); khong duoc thi Python + pip + requirements.txt.
echo %UI_ORANGE%[1/2] CAI THU VIEN VA TRINH DUYET%UI_RESET%
call "%~dp0thuvien.bat" --called
if errorlevel 2 goto :install_python
if errorlevel 1 (
    echo.
    echo %UI_ERROR%[LOI]%UI_TEXT% Cai dat chua hoan tat. Xem loi o tren roi chay lai setup.bat.%UI_RESET%
    pause
    exit /b 1
)

:: 2. KIEM TRA MOI TRUONG
echo.
echo %UI_ORANGE%[2/2] KIEM TRA MOI TRUONG%UI_RESET%
"%~dp0.venv\Scripts\python.exe" -c "import fastapi, uvicorn, pandas, openpyxl, playwright, riviu"
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

:install_python
:: Khong co uv va khong tim thay Python 3.11-3.14: cai Python roi chay lai.
echo %UI_WARN%[CANH BAO]%UI_TEXT% Khong tim thay Python tren may.%UI_RESET%
echo %UI_TEXT%Dang cai dat Python 3.13 bang winget...%UI_RESET%
winget install Python.Python.3.13 --accept-package-agreements --silent
echo.
echo %UI_OK%[OK]%UI_TEXT% Lenh cai dat Python da hoan tat.%UI_RESET%
echo %UI_WARN%Dong cua so nay, sau do chay lai setup.bat.%UI_RESET%
pause
exit /b 1
