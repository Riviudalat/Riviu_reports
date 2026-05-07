@echo off
title TikTok Analytics - Setup
color 0A

echo ============================================
echo    TIKTOK ANALYTICS - SETUP V2 (PORTABLE)
echo ============================================
echo.

cd /d "%~dp0"

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
echo [!] Khong tim thay Python tren may tinh nay!
echo Dang thu tai va cai dat tu dong bang winget...
winget install Python.Python.3.13 --accept-package-agreements --silent
echo.
echo [!] Da cai dat Python xong. VUI LONG DONG CUA SO NAY VA CHAY LAI SETUP.BAT!
pause
exit /b 1

:found_python
echo [OK] Tim thay Python: %PY%
echo.

:: 2. TẠO MÔI TRƯỜNG ẢO (VIRTUAL ENVIRONMENT)
if not exist ".venv" (
    echo Dang tao moi truong ao .venv de hoat dong doc lap tren moi may...
    "%PY%" -m venv .venv
    if errorlevel 1 (
        echo [!] Loi tao moi truong ao.
        pause
        exit /b 1
    )
    echo [OK] Da tao xong moi truong ao.
) else (
    echo [OK] Moi truong ao da ton tai.
)
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [!] Khong tim thay Python trong .venv sau khi tao moi truong ao.
    pause
    exit /b 1
)

:: 3. CÀI ĐẶT THƯ VIỆN CỐ ĐỊNH TỪ REQUIREMENTS.TXT
echo.
echo Dang cai dat thu vien...
call .venv\Scripts\activate.bat
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [!] Loi cai dat thu vien tu requirements.txt
    pause
    exit /b 1
)

:: 4. CÀI ĐẶT TRÌNH DUYỆT CHROME/CHROMIUM
echo.
echo Dang tai trinh duyet phu cho Playwright...
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [!] Loi cai dat Chromium cho Playwright
    pause
    exit /b 1
)

echo.
echo Dang kiem tra lai moi truong...
"%VENV_PY%" -c "import fastapi, uvicorn, pandas, openpyxl, playwright"
if errorlevel 1 (
    echo.
    echo [!] Moi truong da cai xong nhung van import loi.
    echo Vui long chay lai setup.bat hoac kiem tra Python tren may.
    pause
    exit /b 1
)

echo.
echo ============================================
echo    CAI DAT HOAN TAT!
echo ============================================
echo Ban co the chay file "Khoidong.bat" de su dung phan mem.
echo.
pause
