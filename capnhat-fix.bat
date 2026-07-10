@echo off
REM Ban cuu hoa khi capnhat.bat bi ket Unlink (y/n).
REM Chay file nay tren may bi loi: tat Khoidong.bat truoc.
setlocal
cd /d "%~dp0"
set "GIT_ASK_YESNO=false"
set "GIT_TERMINAL_PROMPT=0"
echo [OK] Fetch + reset (khong hoi y/n)...
git -c gc.auto=0 -c gc.autopacklimit=0 -c core.longpaths=true fetch --all --prune
git -c gc.auto=0 -c core.longpaths=true reset --hard origin/main
for /f "delims=" %%H in ('git rev-parse --short HEAD') do echo Commit: %%H
echo Xong. Chay lai Khoidong.bat.
pause
