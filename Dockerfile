FROM python:3.11-slim

WORKDIR /app

# Copy project
COPY . .

# Debug: check data directory exists
RUN python -c "import os; d='/app/openbb_forecast/openbb_forecast/data'; print('data dir:', os.path.isdir(d), os.listdir(d) if os.path.isdir(d) else 'MISSING')"

# The openbb_forecast source is at openbb_forecast/openbb_forecast/
# Copy it to /app so it's importable as 'openbb_forecast' without conflicts
RUN mkdir -p /app_pkg && cp -r openbb_forecast/openbb_forecast /app_pkg/openbb_forecast
ENV PYTHONPATH="/app_pkg:$PYTHONPATH"

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/api/system/status').raise_for_status()"

# Run (port via PORT env var, Railway sets this automatically)
CMD python app/workspace_server.py