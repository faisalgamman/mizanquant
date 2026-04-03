FROM python:3.11-slim

WORKDIR /app

# Limit threads to prevent memory bloat on small containers
ENV OMP_NUM_THREADS=2
ENV MKL_NUM_THREADS=2
ENV OPENBLAS_NUM_THREADS=2

RUN pip install --no-cache-dir fastapi uvicorn yfinance pandas numpy xgboost scikit-learn requests httpx pydantic-settings python-dotenv poetry-core sqlalchemy psycopg2-binary matplotlib

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY . .

RUN pip install --no-cache-dir --no-deps -e /app/openbb_forecast/

CMD uvicorn halal_screener:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
