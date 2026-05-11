FROM python:3.11-slim

WORKDIR /app

# Copy project
COPY . .

# Install the openbb_forecast package (uses setup.py find_packages → includes all subpackages)
RUN pip install --no-cache-dir --no-deps ./openbb_forecast

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Remove source dir to avoid shadowing installed package
RUN rm -rf openbb_forecast

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/api/system/status').raise_for_status()"

# Run (port via PORT env var, Railway sets this automatically)
CMD python app/workspace_server.py