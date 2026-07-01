@echo off
title Kiem tra Proxy - TikTok Analytics
color 0E

echo ============================================
echo    TU KIEM TRA PROXY (Google + TikTok)
echo ============================================
echo.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Moi truong hoat dong chua duoc thiet lap.
    echo Vui long chay file "setup.bat" de cai dat ban dau truoc nhe!
    echo.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [ERROR] Khong tim thay Python trong .venv
    echo Vui long chay lai file "setup.bat".
    echo.
    pause
    exit /b 1
)

echo Dang doc data\proxy_list.txt (neu khong co se hoi ban dan proxy vao day)...
echo.

"%VENV_PY%" tools\kiem_tra_proxy.py %*

echo.
echo ============================================
echo    DA XONG - xem bao cao data\proxy_check_report_*.txt
echo ============================================
pause
