FROM python:3.11-slim

WORKDIR /app

# Copy project
COPY . .

# Rename project dir to avoid shadowing the openbb_forecast package name
# (the project root "openbb_forecast" conflicts with the package "openbb_forecast")
RUN mv openbb_forecast _openbb_forecast_src
ENV PYTHONPATH="/app/_openbb_forecast_src:$PYTHONPATH"

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/api/system/status').raise_for_status()"

# Run (port via PORT env var, Railway sets this automatically)
CMD python app/workspace_server.py