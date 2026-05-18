@echo off
title TikTok Analytics - Cap Nhat Code
color 0E

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

echo [OK] Dang kiem tra trang thai code hien tai...
git status --porcelain >"%TEMP%\ttbd_status.tmp"
for /f %%A in ("%TEMP%\ttbd_status.tmp") do set "STATUS_SIZE=%%~zA"
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
git fetch --all --prune
if errorlevel 1 (
    echo [ERROR] Khong ket noi duoc GitHub. Kiem tra mang roi thu lai.
    echo.
    if "%DID_STASH%"=="1" git stash pop
    pause
    exit /b 1
)

for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set "CUR_BRANCH=%%B"
echo [OK] Branch hien tai: %CUR_BRANCH%

git pull --ff-only origin %CUR_BRANCH%
if errorlevel 1 (
    echo [WARN] Khong the fast-forward. Thu reset cung ve origin/%CUR_BRANCH%...
    git reset --hard origin/%CUR_BRANCH%
    if errorlevel 1 (
        echo [ERROR] Cap nhat that bai.
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

echo.
echo ============================================
echo    CAP NHAT HOAN TAT
echo ============================================
echo Bay gio ban co the chay Khoidong.bat de su dung ban moi.
echo.
pause
