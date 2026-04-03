FROM python:3.11-slim

WORKDIR /app

# Limit threads to prevent memory bloat on small containers
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MALLOC_TRIM_THRESHOLD_=65536
ENV PYTHONUNBUFFERED=1
# Reduce matplotlib memory: use Agg backend, no font cache rebuild
ENV MPLBACKEND=Agg
ENV MPLCONFIGDIR=/tmp/matplotlib

RUN pip install --no-cache-dir fastapi uvicorn yfinance pandas numpy xgboost scikit-learn requests httpx pydantic-settings python-dotenv poetry-core sqlalchemy psycopg2-binary matplotlib

COPY . .

RUN pip install --no-cache-dir --no-deps -e /app/openbb_forecast/

CMD uvicorn halal_screener:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
