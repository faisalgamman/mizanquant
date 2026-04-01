FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn yfinance pandas numpy xgboost scikit-learn requests poetry-core

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY . .

RUN pip install --no-cache-dir --no-deps -e /app/openbb_forecast/

CMD uvicorn halal_screener:app --host 0.0.0.0 --port ${PORT:-8080}
