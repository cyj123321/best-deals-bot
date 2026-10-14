@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Best Deals Bot - 本地运行
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 .venv
    echo 请先执行: python -m venv .venv
    echo            .venv\Scripts\pip install requests beautifulsoup4
    pause
    exit /b 1
)

echo [1/2] 正在抓取今日优惠...
echo.

".venv\Scripts\python.exe" bot.py
if errorlevel 1 (
    echo.
    echo [错误] bot.py 运行失败
    pause
    exit /b 1
)

echo.
echo [2/2] 完成,正在打开 index.html ...
start "" "index.html"
echo.
echo 本地预览地址: %CD%\index.html
echo.
pause
