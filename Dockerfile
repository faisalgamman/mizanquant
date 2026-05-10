FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Copy project
COPY . .

# Install Python deps
RUN pip install --no-cache-dir -e openbb_forecast
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] yfinance pandas numpy \
    httpx pydantic-settings apscheduler

# Expose port
EXPOSE 6910

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:6910/api/info').raise_for_status()"

# Run
CMD ["uvicorn", "app.workspace_server:app", "--host", "0.0.0.0", "--port", "6910"]
