FROM python:3.11-slim

WORKDIR /app

# Copy app package
COPY app/ /app/app/

# Copy openbb_forecast to a non-conflicting path (avoid namespace package clash)
COPY openbb_forecast/openbb_forecast/ /app/_vendor/openbb_forecast/

# Copy project root files
COPY requirements.txt railway.json /app/

# Debug: verify data subpackage exists
RUN python -c "import os, sys; d='/app/_vendor/openbb_forecast/data'; exists=os.path.isdir(d); print(f'DATA EXISTS: {exists}', file=sys.stderr); [print(f'  {f}', file=sys.stderr) for f in sorted(os.listdir(d))] if exists else print('  MISSING!', file=sys.stderr)"

# PYTHONPATH: /app for 'app' package, /app/_vendor for 'openbb_forecast' package
ENV PYTHONPATH="/app:/app/_vendor:$PYTHONPATH"

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Debug: verify data directory and files
RUN ls -laR /app/_vendor/openbb_forecast/ && echo "=== data/__init__.py ===" && cat /app/_vendor/openbb_forecast/data/__init__.py

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/api/system/status').raise_for_status()"

CMD python app/workspace_server.py