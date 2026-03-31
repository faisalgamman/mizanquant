FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn yfinance pandas numpy xgboost scikit-learn requests nltk

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY halal_screener.py .
COPY russell1000_halal.py .

EXPOSE 8000

CMD ["uvicorn", "halal_screener:app", "--host", "0.0.0.0", "--port", "8000"]