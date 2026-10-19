@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo.
echo ============================================
echo   Full uninstall
echo   - uninstall curl_cffi
echo   - remove cookie / saved path / history
echo   - delete the downloaded files listed in history
echo ============================================
echo.
python "%~dp0src\full_uninstall.py"
echo.
pause
