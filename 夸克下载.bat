@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python "%~dp0src\quark.py" %*
echo.
pause
