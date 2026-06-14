FROM python:3.11-slim

WORKDIR /app

# Copy project
COPY . .

# torch was REMOVED from requirements.txt for Railway cost (~300-500MB resident RAM +
# ~2GB image). DL forecast models fall back to ARIMA (_HAS_TORCH guard); the core
# Monte-Carlo engine is pure numpy. To re-enable DL models, add torch back to
# requirements.txt (prefer the CPU wheel: --index-url https://download.pytorch.org/whl/cpu).
#
# openbb-forecast (installed --no-deps; its Monte-Carlo submodule needs only numpy) +
# all other requirements.
RUN pip install --no-cache-dir --no-deps ./openbb_forecast && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf openbb_forecast

# Health check — lightweight /health endpoint (no yfinance, no broker checks)
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/health').raise_for_status()"

# Run
CMD python app/workspace_server.py