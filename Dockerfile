FROM python:3.11-slim

WORKDIR /app

# 8GB RAM tier — allow more threads for ML models
ENV OMP_NUM_THREADS=4
ENV MKL_NUM_THREADS=4
ENV OPENBLAS_NUM_THREADS=4
ENV MALLOC_TRIM_THRESHOLD_=131072
ENV PYTHONUNBUFFERED=1
# Matplotlib non-interactive backend
ENV MPLBACKEND=Agg
ENV MPLCONFIGDIR=/tmp/matplotlib

# Install PyTorch CPU-only (no CUDA = ~200MB instead of 2GB)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install all other dependencies
RUN pip install --no-cache-dir \
    fastapi uvicorn yfinance pandas numpy \
    xgboost scikit-learn \
    requests httpx \
    pydantic-settings python-dotenv poetry-core \
    sqlalchemy psycopg2-binary \
    matplotlib

COPY . .

RUN pip install --no-cache-dir --no-deps -e /app/openbb_forecast/

CMD uvicorn halal_screener:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
