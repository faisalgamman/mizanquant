FROM python:3.11-slim

WORKDIR /app

# Copy project
COPY . .

# Install openbb-forecast first (setuptools find_packages includes all subpackages like data/)
# Then remove source dir to prevent namespace-package conflicts with site-packages install
RUN pip install --no-cache-dir --no-deps ./openbb_forecast && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf openbb_forecast

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/api/system/status').raise_for_status()"

# Run
CMD python app/workspace_server.py