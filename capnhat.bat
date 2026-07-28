@echo off
setlocal EnableExtensions
title Riviu Reports - Cap nhat
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
call :render_banner "CAP NHAT PHIEN BAN"
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

REM Git for Windows: prompt "Unlink ... Should I try again? (y/n)"
REM GIT_ASK_YESNO=false = luon tra loi "n" ^(bo qua, khong kẹt^).
set "GIT_ASK_YESNO=false"
set "GIT_TERMINAL_PROMPT=0"
set "GCM_INTERACTIVE=Never"

cd /d "%~dp0"

echo %UI_ORANGE%[1/4] KIEM TRA HE THONG%UI_RESET%

where git >nul 2>&1
if errorlevel 1 (
    echo %UI_ERROR%[LOI]%UI_TEXT% Khong tim thay Git tren may.%UI_RESET%
    echo %UI_TEXT%      Cai Git tu https://git-scm.com/download/win roi chay lai.%UI_RESET%
    echo.
    pause
    exit /b 1
)

if not exist ".git" (
    echo %UI_ERROR%[LOI]%UI_TEXT% Thu muc nay khong phai repo Git.%UI_RESET%
    echo %UI_TEXT%      Clone lai du an roi chay lai capnhat.bat.%UI_RESET%
    echo.
    pause
    exit /b 1
)

echo %UI_OK%[OK]%UI_TEXT% Git va thu muc du an san sang.%UI_RESET%
echo %UI_DIM%Nen tat Khoidong.bat ^(Ctrl+C^) truoc khi cap nhat.%UI_RESET%
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if not errorlevel 1 (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Dang co python.exe chay. Nen tat server roi cap nhat.%UI_RESET%
)
tasklist /FI "IMAGENAME eq uvicorn.exe" 2>nul | find /I "uvicorn.exe" >nul
if not errorlevel 1 (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Dang co uvicorn.exe chay. Hay tat server.%UI_RESET%
)

call :cleanup_git_locks

echo.
echo %UI_ORANGE%[2/4] BAO VE THAY DOI CUC BO%UI_RESET%
git status --porcelain >"%TEMP%\ttbd_status.tmp" 2>nul
set "STATUS_SIZE=0"
for %%A in ("%TEMP%\ttbd_status.tmp") do set "STATUS_SIZE=%%~zA"
del "%TEMP%\ttbd_status.tmp" >nul 2>&1

if not "%STATUS_SIZE%"=="0" (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Co thay doi chua commit. Dang stash an toan...%UI_RESET%
    git -c gc.auto=0 stash push -u -m "capnhat-auto-stash"
    if errorlevel 1 (
        echo %UI_ERROR%[LOI]%UI_TEXT% Stash thay doi cuc bo that bai.%UI_RESET%
        echo %UI_TEXT%      Luu cong viec dang lam roi chay lai.%UI_RESET%
        echo.
        pause
        exit /b 1
    )
    echo %UI_OK%[OK]%UI_TEXT% Da luu tam thay doi cuc bo.%UI_RESET%
    set "DID_STASH=1"
) else (
    echo %UI_OK%[OK]%UI_TEXT% Khong co thay doi cuc bo can luu tam.%UI_RESET%
    set "DID_STASH=0"
)

echo.
echo %UI_ORANGE%[3/4] DONG BO MA NGUON%UI_RESET%
echo %UI_TEXT%Dang lay code moi nhat tu GitHub...%UI_RESET%
REM Tat auto-gc trong fetch de tranh Unlink pack.idx bi khoa tren Windows.
git -c core.longpaths=true -c gc.auto=0 -c gc.autopacklimit=0 fetch --all --prune
if errorlevel 1 (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Fetch lan 1 that bai. Dang don lock va thu lai...%UI_RESET%
    call :cleanup_git_locks
    git -c core.longpaths=true -c gc.auto=0 -c gc.autopacklimit=0 fetch --all --prune
    if errorlevel 1 (
        echo %UI_ERROR%[LOI]%UI_TEXT% Khong ket noi duoc GitHub.%UI_RESET%
        echo %UI_TEXT%      Kiem tra mang roi thu lai.%UI_RESET%
        echo.
        if "%DID_STASH%"=="1" git -c gc.auto=0 stash pop
        pause
        exit /b 1
    )
)

for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set "CUR_BRANCH=%%B"
echo %UI_OK%[OK]%UI_TEXT% Branch hien tai: %CUR_BRANCH%%UI_RESET%

echo %UI_TEXT%Dang dong bo ve origin/%CUR_BRANCH%...%UI_RESET%
git -c core.longpaths=true -c gc.auto=0 reset --hard "origin/%CUR_BRANCH%"
if errorlevel 1 (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Reset lan 1 that bai. Dang don lock va thu lai...%UI_RESET%
    call :cleanup_git_locks
    git -c core.longpaths=true -c gc.auto=0 -c gc.autopacklimit=0 fetch origin "%CUR_BRANCH%"
    git -c core.longpaths=true -c gc.auto=0 reset --hard "origin/%CUR_BRANCH%"
    if errorlevel 1 (
        echo %UI_ERROR%[LOI]%UI_TEXT% Cap nhat that bai do file .git dang bi khoa.%UI_RESET%
        echo %UI_TEXT%      Tat Khoidong.bat, dong Cursor/antivirus tam, roi chay lai.%UI_RESET%
        if "%DID_STASH%"=="1" git -c gc.auto=0 stash pop
        echo.
        pause
        exit /b 1
    )
)
echo %UI_OK%[OK]%UI_TEXT% Ma nguon da dong bo.%UI_RESET%

if "%DID_STASH%"=="1" (
    echo.
    echo %UI_TEXT%Dang khoi phuc thay doi cuc bo da stash...%UI_RESET%
    git -c gc.auto=0 stash pop
    if errorlevel 1 (
        echo %UI_WARN%[CANH BAO]%UI_TEXT% Co conflict khi pop stash. Can xu ly bang Git.%UI_RESET%
    )
)

echo.
echo %UI_ORANGE%[4/4] CAP NHAT THU VIEN%UI_RESET%
if exist ".venv\Scripts\python.exe" (
    set "VENV_PY=%~dp0.venv\Scripts\python.exe"
    echo %UI_TEXT%Dang kiem tra requirements.txt...%UI_RESET%
    "%VENV_PY%" -m pip install -r requirements.txt --upgrade --quiet
    if errorlevel 1 (
        echo %UI_WARN%[CANH BAO]%UI_TEXT% Cap nhat thu vien that bai. Chay setup.bat de sua.%UI_RESET%
    ) else (
        echo %UI_OK%[OK]%UI_TEXT% Thu vien da cap nhat.%UI_RESET%
    )
) else (
    echo %UI_WARN%[CANH BAO]%UI_TEXT% Chua co .venv. Hay chay setup.bat.%UI_RESET%
)

for /f "delims=" %%H in ('git rev-parse --short HEAD') do set "HEAD_SHA=%%H"
echo.
echo %UI_ORANGE%========================================================================%UI_RESET%
echo %UI_OK%   CAP NHAT HOAN TAT%UI_RESET%
echo %UI_ORANGE%========================================================================%UI_RESET%
echo %UI_DIM%Commit hien tai: %HEAD_SHA%%UI_RESET%
echo %UI_TEXT%1. Tat cua so Khoidong.bat cu ^(Ctrl+C^).%UI_RESET%
echo %UI_TEXT%2. Chay lai Khoidong.bat.%UI_RESET%
echo %UI_TEXT%3. F5 trang web.%UI_RESET%
echo.
pause
endlocal
exit /b 0

:cleanup_git_locks
if exist ".git\index.lock" del /f /q ".git\index.lock" >nul 2>&1
if exist ".git\shallow.lock" del /f /q ".git\shallow.lock" >nul 2>&1
if exist ".git\gc.pid" del /f /q ".git\gc.pid" >nul 2>&1
if exist ".git\gc.log" del /f /q ".git\gc.log" >nul 2>&1
for %%F in (".git\objects\pack\*.lock") do (
    if exist "%%~fF" del /f /q "%%~fF" >nul 2>&1
)
exit /b 0
