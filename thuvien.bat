@echo off
setlocal EnableExtensions
title Riviu Reports - Thu vien
color 06

REM Cai/cap nhat thu vien Python vao .venv. setup.bat, capnhat.bat va Khoidong.bat
REM goi file nay voi --called (khong xoa man hinh, khong pause).
REM Uu tien uv + uv.lock; neu khong co uv thi dung Python + pip + requirements.txt.
REM Ma thoat: 0 = xong, 1 = loi, 2 = khong co uv va khong tim thay Python 3.11-3.14.
REM RIVIU_SKIP_UV=1 bo qua uv va dung pip.

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
set "CALLED="
if /i "%~1"=="--called" set "CALLED=1"
if defined CALLED goto :main
call :render_banner "CAI THU VIEN PYTHON"
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
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
REM uv tu chon .venv cua du an; bien nay (neu co tren may) se dua no di cho khac.
set "UV_PROJECT_ENVIRONMENT="

call :find_uv
if not defined UV_EXE goto :pip_path

echo %UI_OK%[OK]%UI_TEXT% Dung uv: %UV_EXE%%UI_RESET%
echo %UI_TEXT%Dang dong bo thu vien theo uv.lock...%UI_RESET%
call "%UV_EXE%" sync --locked --no-dev
if not errorlevel 1 goto :browser
echo %UI_WARN%[CANH BAO]%UI_TEXT% uv sync that bai. Thu lai bang pip...%UI_RESET%

:pip_path
echo %UI_TEXT%Dang cai thu vien bang pip tu requirements.txt...%UI_RESET%
if exist "%VENV_PY%" goto :venv_ready
call :find_python
if not defined PY (
    echo %UI_ERROR%[LOI]%UI_TEXT% Khong co uv va khong tim thay Python 3.11-3.14.%UI_RESET%
    call :finish 2
    exit /b 2
)
echo %UI_TEXT%Dang tao .venv bang %PY%...%UI_RESET%
%PY% -m venv .venv
if errorlevel 1 (
    echo %UI_ERROR%[LOI]%UI_TEXT% Tao moi truong ao that bai.%UI_RESET%
    call :finish 1
    exit /b 1
)

:venv_ready
REM .venv do uv tao khong co pip.
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 "%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
"%VENV_PY%" -m pip install --upgrade pip --quiet >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo %UI_ERROR%[LOI]%UI_TEXT% Cai thu vien tu requirements.txt that bai.%UI_RESET%
    echo %UI_TEXT%      Kiem tra mang. Neu van loi, xoa thu muc .venv roi chay lai setup.bat.%UI_RESET%
    call :finish 1
    exit /b 1
)

:browser
if not exist "%VENV_PY%" (
    echo %UI_ERROR%[LOI]%UI_TEXT% Khong tim thay Python trong .venv.%UI_RESET%
    call :finish 1
    exit /b 1
)
echo %UI_OK%[OK]%UI_TEXT% Thu vien Python da san sang.%UI_RESET%
echo %UI_TEXT%Dang kiem tra Chromium cho Playwright...%UI_RESET%
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
    echo %UI_ERROR%[LOI]%UI_TEXT% Cai Chromium cho Playwright that bai.%UI_RESET%
    call :finish 1
    exit /b 1
)
echo %UI_OK%[OK]%UI_TEXT% Chromium da san sang.%UI_RESET%

"%VENV_PY%" -c "import fastapi, uvicorn, pandas, openpyxl, playwright, PIL, jinja2, python_multipart, googleapiclient, google.auth, google_auth_oauthlib, requests, socks, riviu"
if errorlevel 1 (
    echo %UI_ERROR%[LOI]%UI_TEXT% Moi truong van con loi import.%UI_RESET%
    call :finish 1
    exit /b 1
)
REM Khoidong.bat so file nay voi requirements.txt de biet thu vien da khop ban code chua.
copy /y "requirements.txt" ".venv\.riviu-requirements.txt" >nul
echo %UI_OK%[OK]%UI_TEXT% Moi truong Python da kiem tra xong.%UI_RESET%
call :finish 0
exit /b 0

:finish
if defined CALLED exit /b 0
echo.
pause
exit /b 0

:find_uv
set "UV_EXE="
if "%RIVIU_SKIP_UV%"=="1" (
    echo %UI_DIM%RIVIU_SKIP_UV=1: bo qua uv.%UI_RESET%
    exit /b 0
)
for /f "delims=" %%U in ('where uv 2^>nul') do if not defined UV_EXE set "UV_EXE=%%U"
if not defined UV_EXE call :probe_uv
if defined UV_EXE goto :uv_ready

echo %UI_TEXT%Chua co uv. Dang cai uv vao thu muc nguoi dung...%UI_RESET%
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
call :probe_uv
if defined UV_EXE goto :uv_ready
where winget >nul 2>&1
if not errorlevel 1 (
    echo %UI_TEXT%Thu cai uv bang winget...%UI_RESET%
    winget install --id astral-sh.uv -e --silent --accept-package-agreements --accept-source-agreements
    call :probe_uv
)
if defined UV_EXE goto :uv_ready
echo %UI_WARN%[CANH BAO]%UI_TEXT% Khong cai duoc uv. Dung Python + pip.%UI_RESET%
exit /b 0

:uv_ready
REM Them thu muc cua uv vao PATH cho lan chay nay (cua so moi se tu thay).
for %%D in ("%UV_EXE%") do set "PATH=%%~dpD;%PATH%"
exit /b 0

:probe_uv
for %%P in ("%USERPROFILE%\.local\bin\uv.exe" "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe") do (
    if not defined UV_EXE if exist "%%~P" set "UV_EXE=%%~P"
)
exit /b 0

:find_python
set "PY="
for %%V in (3.13 3.12 3.14 3.11) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=py -%%V"
    )
)
if defined PY exit /b 0
for %%X in (python python3) do (
    if not defined PY (
        %%X -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=%%X"
    )
)
if defined PY exit /b 0
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
) do (
    if not defined PY if exist "%%~P" set "PY="%%~P""
)
exit /b 0
