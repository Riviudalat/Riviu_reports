@echo off
setlocal EnableExtensions
title TikTok Analytics - Cap Nhat Code
color 0E

REM Git for Windows: prompt "Unlink ... Should I try again? (y/n)"
REM GIT_ASK_YESNO=false = luon tra loi "n" ^(bo qua, khong kẹt^).
set "GIT_ASK_YESNO=false"
set "GIT_TERMINAL_PROMPT=0"
set "GCM_INTERACTIVE=Never"

echo ============================================
echo    TIKTOK ANALYTICS - CAP NHAT CODE
echo ============================================
echo.

cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Khong tim thay Git tren may.
    echo Vui long cai dat Git tu https://git-scm.com/download/win roi chay lai.
    echo.
    pause
    exit /b 1
)

if not exist ".git" (
    echo [ERROR] Thu muc nay khong phai la repo Git.
    echo Hay clone lai du an tu GitHub roi chay lai capnhat.bat.
    echo.
    pause
    exit /b 1
)

echo [OK] Kiem tra process dang chay...
echo [INFO] Nen TAT Khoidong.bat ^(Ctrl+C^) truoc khi cap nhat de tranh khoa file .git.
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if not errorlevel 1 (
    echo [WARN] Dang co python.exe chay. Neu do la server TTBD, hay tat Khoidong.bat roi chay lai capnhat.bat.
)
tasklist /FI "IMAGENAME eq uvicorn.exe" 2>nul | find /I "uvicorn.exe" >nul
if not errorlevel 1 (
    echo [WARN] Dang co uvicorn.exe chay. Hay tat server truoc khi cap nhat.
)

call :cleanup_git_locks

echo.
echo [OK] Dang kiem tra trang thai code hien tai...
git status --porcelain >"%TEMP%\ttbd_status.tmp" 2>nul
set "STATUS_SIZE=0"
for %%A in ("%TEMP%\ttbd_status.tmp") do set "STATUS_SIZE=%%~zA"
del "%TEMP%\ttbd_status.tmp" >nul 2>&1

if not "%STATUS_SIZE%"=="0" (
    echo [WARN] Co thay doi cuc bo chua commit. Dang stash de an toan...
    git -c gc.auto=0 stash push -u -m "capnhat-auto-stash"
    if errorlevel 1 (
        echo [ERROR] Khong the stash thay doi cuc bo.
        echo Vui long luu lai cong viec dang lam roi chay lai.
        echo.
        pause
        exit /b 1
    )
    set "DID_STASH=1"
) else (
    set "DID_STASH=0"
)

echo.
echo [OK] Dang lay code moi nhat tu GitHub...
REM Tat auto-gc trong fetch de tranh Unlink pack.idx bi khoa tren Windows.
git -c core.longpaths=true -c gc.auto=0 -c gc.autopacklimit=0 fetch --all --prune
if errorlevel 1 (
    echo [WARN] Fetch lan 1 that bai. Thu lai sau khi don lock...
    call :cleanup_git_locks
    git -c core.longpaths=true -c gc.auto=0 -c gc.autopacklimit=0 fetch --all --prune
    if errorlevel 1 (
        echo [ERROR] Khong ket noi duoc GitHub. Kiem tra mang roi thu lai.
        echo.
        if "%DID_STASH%"=="1" git -c gc.auto=0 stash pop
        pause
        exit /b 1
    )
)

for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set "CUR_BRANCH=%%B"
echo [OK] Branch hien tai: %CUR_BRANCH%

echo [OK] Dong bo ve origin/%CUR_BRANCH% ...
git -c core.longpaths=true -c gc.auto=0 reset --hard "origin/%CUR_BRANCH%"
if errorlevel 1 (
    echo [WARN] Reset that bai. Thu don lock roi reset lai...
    call :cleanup_git_locks
    git -c core.longpaths=true -c gc.auto=0 -c gc.autopacklimit=0 fetch origin "%CUR_BRANCH%"
    git -c core.longpaths=true -c gc.auto=0 reset --hard "origin/%CUR_BRANCH%"
    if errorlevel 1 (
        echo [ERROR] Cap nhat that bai ^(file .git dang bi khoa^).
        echo Hay TAT Khoidong.bat, dong Cursor/antivirus tam, roi chay lai.
        if "%DID_STASH%"=="1" git -c gc.auto=0 stash pop
        echo.
        pause
        exit /b 1
    )
)

if "%DID_STASH%"=="1" (
    echo.
    echo [INFO] Dang khoi phuc thay doi cuc bo da stash...
    git -c gc.auto=0 stash pop
    if errorlevel 1 (
        echo [WARN] Co conflict khi pop stash. Hay tu xu ly bang Git.
    )
)

echo.
echo [OK] Dang cap nhat thu vien Python neu can...
if exist ".venv\Scripts\python.exe" (
    set "VENV_PY=%~dp0.venv\Scripts\python.exe"
    "%VENV_PY%" -m pip install -r requirements.txt --upgrade --quiet
    if errorlevel 1 (
        echo [WARN] Cai lai thu vien that bai. Hay chay setup.bat de sua.
    ) else (
        echo [OK] Thu vien da cap nhat.
    )
) else (
    echo [WARN] Chua co .venv. Hay chay setup.bat truoc khi dung.
)

for /f "delims=" %%H in ('git rev-parse --short HEAD') do set "HEAD_SHA=%%H"
echo.
echo ============================================
echo    CAP NHAT HOAN TAT
echo ============================================
echo Commit hien tai: %HEAD_SHA%
echo Bay gio:
echo   1. TAT cua so Khoidong.bat cu ^(Ctrl+C^)
echo   2. Chay lai Khoidong.bat
echo   3. F5 trang web
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
