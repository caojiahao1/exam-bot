@echo off
title exam-bot
setlocal
cd /d "%~dp0"

echo ============================================
echo   exam-bot - 开卷答题助手
echo ============================================
echo.

REM ---- 找 Python 3 ----
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo [错误] 没有找到 Python 3
  echo        请先安装： https://www.python.org/downloads/
  echo        安装时勾选 "Add Python to PATH"
  echo.
  pause
  exit /b 1
)

echo [1/2] 启动本地服务...
start "exam-bot" cmd /c "%PY% app\server.py"
timeout /t 3 /nobreak >nul

echo [2/2] 打开控制台...
start "" http://127.0.0.1:8778

echo.
echo 控制台已打开。如果浏览器没自动打开，手动访问：
echo     http://127.0.0.1:8778
echo.
echo 关闭这个窗口不会停止服务；要停止请关掉 exam-bot 那个黑窗口。
echo.
pause
