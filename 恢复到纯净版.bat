@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo.
echo ============================================
echo   Reset to clean state
echo   (remove cookie / saved path / dependency)
echo   Downloaded files will NOT be deleted.
echo ============================================
echo.
set /p ANS=Continue? (type y to confirm):
if /i not "%ANS%"=="y" goto :cancel
echo.
python "%~dp0src\reset_tool.py"
echo.
pause
goto :eof

:cancel
echo Cancelled.
echo.
pause
