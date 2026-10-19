@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo.
echo === Step 1/2: installing curl_cffi ===
echo.
python -m pip install --upgrade curl_cffi
set PIP_RC=%errorlevel%
echo.
echo === Step 2/2: checking ===
echo.
python "%~dp0src\check_deps.py"
set CHK_RC=%errorlevel%
echo.
echo ---------------------------------------------
if %PIP_RC%==0 (echo pip exit code: %PIP_RC%  OK) else (echo pip exit code: %PIP_RC%  FAILED)
if %CHK_RC%==0 (echo dependency check: OK) else (echo dependency check: FAILED)
echo ---------------------------------------------
echo.
pause
