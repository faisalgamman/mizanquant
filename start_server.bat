@echo off
pushd "%~dp0"
set PYTHONPATH=%CD%
echo Starting OpenBB Forecast Server...
echo Dashboard: http://127.0.0.1:6910/
echo.
python -c "import sys; sys.path.insert(0, r'%CD%'); from uvicorn import run; run('app.workspace_server:app', host='0.0.0.0', port=6910)"
pause
popd
