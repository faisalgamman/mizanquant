@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%;%CD%\openbb_forecast
echo Starting MizanQuant Server...
echo Dashboard: http://127.0.0.1:6910/static/dashboard.html
echo.
python -m uvicorn app.workspace_server:app --host 0.0.0.0 --port 6910
pause