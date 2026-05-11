FROM python:3.11-slim

WORKDIR /app

# Copy project
COPY . .

# Install Python deps (openbb-forecast first via local package, then requirements)
RUN pip install --no-cache-dir --no-deps ./openbb_forecast && \
    pip install --no-cache-dir -r requirements.txt

# Remove source dir to avoid shadowing installed openbb_forecast package
RUN rm -rf openbb_forecast

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/api/system/status').raise_for_status()"

# Run (port via PORT env var, Railway sets this automatically)
CMD python app/workspace_server.py