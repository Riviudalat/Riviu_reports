@echo off
setlocal EnableExtensions
title TikTok Analytics - Cap Nhat Code
color 0E

REM Tranh prompt tuong tac (vd. "Should I try again? (y/n)" khi unlink pack).
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

REM Xoa lock Git neu con sot.
if exist ".git\index.lock" (
    echo [WARN] Tim thay .git\index.lock — dang xoa...
    del /f /q ".git\index.lock" >nul 2>&1
)
if exist ".git\shallow.lock" (
    del /f /q ".git\shallow.lock" >nul 2>&1
)

echo.
echo [OK] Dang kiem tra trang thai code hien tai...
git status --porcelain >"%TEMP%\ttbd_status.tmp" 2>nul
set "STATUS_SIZE=0"
for %%A in ("%TEMP%\ttbd_status.tmp") do set "STATUS_SIZE=%%~zA"
del "%TEMP%\ttbd_status.tmp" >nul 2>&1

if not "%STATUS_SIZE%"=="0" (
    echo [WARN] Co thay doi cuc bo chua commit. Dang stash de an toan...
    git stash push -u -m "capnhat-auto-stash"
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
git -c core.longpaths=true fetch --all --prune
if errorlevel 1 (
    echo [WARN] Fetch lan 1 that bai. Thu gc roi fetch lai...
    call :cleanup_git_locks
    git gc --prune=now >nul 2>&1
    git -c core.longpaths=true fetch --all --prune
    if errorlevel 1 (
        echo [ERROR] Khong ket noi duoc GitHub. Kiem tra mang roi thu lai.
        echo.
        if "%DID_STASH%"=="1" git stash pop
        pause
        exit /b 1
    )
)

for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set "CUR_BRANCH=%%B"
echo [OK] Branch hien tai: %CUR_BRANCH%

REM Uu tien reset --hard ve origin ^(tranh pull unpack/repack gay Unlink pack^).
echo [OK] Dong bo ve origin/%CUR_BRANCH% ...
git -c core.longpaths=true reset --hard "origin/%CUR_BRANCH%"
if errorlevel 1 (
    echo [WARN] Reset that bai. Thu don pack lock roi reset lai...
    call :cleanup_git_locks
    git gc --prune=now >nul 2>&1
    git -c core.longpaths=true fetch origin "%CUR_BRANCH%"
    git -c core.longpaths=true reset --hard "origin/%CUR_BRANCH%"
    if errorlevel 1 (
        echo [ERROR] Cap nhat that bai ^(co the file .git dang bi khoa^).
        echo Hay TAT Khoidong.bat / antivirus tam thoi, roi chay lai capnhat.bat.
        if "%DID_STASH%"=="1" git stash pop
        echo.
        pause
        exit /b 1
    )
)

if "%DID_STASH%"=="1" (
    echo.
    echo [INFO] Dang khoi phuc thay doi cuc bo da stash...
    git stash pop
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
for %%F in (".git\objects\pack\*.lock") do (
    if exist "%%~fF" del /f /q "%%~fF" >nul 2>&1
)
exit /b 0
