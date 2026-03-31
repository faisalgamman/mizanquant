FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn yfinance pandas numpy xgboost scikit-learn requests nltk

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY . .

RUN pip install --no-cache-dir -e /app/openbb_forecast 2>/dev/null || echo "openbb_forecast not found, skipping"

EXPOSE 8000

CMD ["uvicorn", "halal_screener:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## لكن المشكلة الأكبر:

`openbb_forecast` موجود فقط على كمبيوترك في:
```
D:\Stock-Prediction-Models-master\openbb-forecast\