FROM python:3.11-slim

WORKDIR /app

# Copy project
COPY . .

# Step 1 — CPU-only PyTorch first (no CUDA drivers → ~250 MB vs 1.9 GB for CUDA wheel).
# Must run BEFORE requirements.txt so pip sees torch already satisfied and skips reinstall.
# DISABLED for Railway cost savings: RUN pip install --no-cache-dir torch \
# --index-url https://download.pytorch.org/whl/cpu

# Step 2 — openbb-forecast (no deps) + all other requirements
# torch in requirements.txt is already satisfied by the CPU build above.
RUN pip install --no-cache-dir --no-deps ./openbb_forecast && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf openbb_forecast

# Health check — lightweight /health endpoint (no yfinance, no broker checks)
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/health').raise_for_status()"

# Run
CMD python app/workspace_server.py