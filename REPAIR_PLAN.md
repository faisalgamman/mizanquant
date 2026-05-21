# خطة إصلاح MizanQuant — التفصيلية
**التاريخ:** 2026-05-20 | **الإصدار:** 2.0 (مصححة بعد التشخيص الدقيق)

---

## ⚠️ ملاحظة حاسمة: تعديل على التشخيص السابق

> **M1 — المشكلة ليست "weights مفقودة".** الـ weights موجودة (`models/*/20260519.pt`). المشكلة: الـ endpoint `/api/forecast/{model}` يُعيد تدريب النموذج **من الصفر في كل طلب** (on-the-fly training) بدلاً من تحميل pre-trained weights. هذا يستغرق 30-60 ثانية → timeout.
>
> **الحل الفوري:** تمرير params خفيفة (`epochs=3&n_splits=1`) للـ frontend. **الحل الدائم:** تعديل الـ endpoint ليستخدم `load_checkpoint()`.

---

## المرحلة 0 — إصلاح فوري (24-48 ساعة)

### P0.1: إصلاح ForecastML — الحل الفوري (30 دقيقة)

**الملف:** `app/static/forecast-panel.html`

**الخطوات:**
1. افتح الملف.
2. ابحث عن الـ function اللي تستدعي الـ forecast API (غالباً `fetchForecast()` أو `loadModelForecast()`).
3. غيّر الـ URL من:
   ```javascript
   `/api/forecast/${model}?symbol=${symbol}`
   ```
   إلى:
   ```javascript
   `/api/forecast/${model}?symbol=${symbol}&epochs=3&n_splits=1`
   ```
4. أضف loader أو زود الـ timeout:
   ```javascript
   const controller = new AbortController();
   setTimeout(() => controller.abort(), 60000); // 60s instead of default 5-10s
   fetch(url, { signal: controller.signal })
   ```

**التحقق:**
```bash
curl "http://localhost:6910/api/forecast/transformer?symbol=AAPL&epochs=3&n_splits=1"
# يجب أن يرد في < 5 ثواني ببيانات كاملة
```

---

### P0.2: إصلاح ForecastML — الحل الدائم (2-3 ساعات)

**الملف:** `src/api/forecast_router.py` أو `app/api/forecast.py` (حسب الموجود)

**الخطوات:**
1. افتح الـ router اللي يعالج `/api/forecast/{model}`.
2. ابحث عن الـ function اللي تُنشئ النموذج — غالباً تستدعي `walk_forward_predict()` مباشرة.
3. عدّل المنطق:

**الكود الحالي (غالباً):**
```python
model = TransformerModel(...)  # or LSTMModel
model.fit(X, y)  # يُعيد التدريب!
forecast = model.predict(X_latest)
```

**الكود المطلوب:**
```python
import os

MODEL_PATHS = {
    "lstm": "models/lstm/20260519.pt",
    "transformer": "models/transformer/20260519.pt",
    "ensemble": "models/ensemble/20260519.pkl"
}

def get_forecast(model_name: str, symbol: str, epochs: int = 3, n_splits: int = 1):
    path = MODEL_PATHS.get(model_name)
    
    # إذا وجدنا weights محفوظة ولم يُطلب training جديد
    if path and os.path.exists(path) and epochs <= 3:
        model = load_model(model_name, path)  # تحميل فقط
        return model.predict(get_features(symbol))
    
    # fallback: تدريب خفيف
    return walk_forward_predict(model_name, symbol, epochs=epochs, n_splits=n_splits)
```

4. تأكد من وجود `load_model()` في `src/models/` — إذا ما موجودة، اكتبها:
   ```python
   def load_model(model_name: str, path: str):
       if model_name == "lstm":
           model = LSTMModel(...)
           model.load_state_dict(torch.load(path, map_location="cpu"))
       elif model_name == "transformer":
           model = TransformerModel(...)
           model.load_state_dict(torch.load(path, map_location="cpu"))
       elif model_name == "ensemble":
           with open(path, "rb") as f:
               model = pickle.load(f)
       model.eval()
       return model
   ```

**التحقق:**
```bash
curl "http://localhost:6910/api/forecast/transformer?symbol=AAPL"
# يجب أن يرد في < 2 ثانية (باستخدام weights المحفوظة)
```

---

### P0.3: إصلاح Screener فارغ (1-2 ساعة)

**السبب:** جدول `ScreeningResult` في DB فاضي — Pipeline ما شُغّل.

**الخطوات:**
1. **تشغيل Pipeline يدوي:**
   ```bash
   # عبر API
   curl -X POST "http://localhost:6910/api/v1/pipeline/run" \
     -H "X-API-Key: YOUR_API_KEY"
   
   # أو عبر Python مباشرة
   cd /c/Users/TECH\ VALLEY/mizanquant
   python -c "from src.pipeline.unified_pipeline import UnifiedPipeline; p = UnifiedPipeline(); p.run_full_pipeline()"
   ```

2. **التحقق من FMP quota:**
   - افتح `logs/` أو شغّل الـ Pipeline وراقب الـ output.
   - إذا ظهر `FMP quota exceeded` أو `429 Too Many Requests`:
     - افتح `src/config.py` أو `app/config.py`.
     - غيّر `max_per_run` من `80` إلى `50` (لإضافة هامش أمان).
     - أو غيّر الساعة اللي يشتغل فيها الـ Pipeline لوقت ما يكون فيه quota متاح.

3. **التحقق من النتائج:**
   ```bash
   sqlite3 data/mizanquant.db "SELECT COUNT(*) FROM screening_results;"
   # يجب أن يكون > 0
   ```

---

### P0.4: إصلاح Overview Portfolio Bug (30 دقيقة)

**الملف:** `app/api/v1/overview.py`

**الخطوات:**
1. افتح الملف وابحث عن `_get_portfolio()`.
2. غالباً الـ function تُرجع `{}` أو ما تستدعي Alpaca.

**الكود المطلوب:**
```python
from app.services.trading_service import get_alpaca_portfolio

def _get_portfolio():
    try:
        return get_alpaca_portfolio()  # أو نفس الـ function اللي تستخدمها /api/trading/summary
    except Exception:
        return {}
```

3. أو إذا الـ `_get_portfolio()` تستدعي DB فقط، عدّلها:
   ```python
   def _get_portfolio():
       # أولاً: حاول Alpaca (source of truth)
       try:
           from app.api.trading import get_trading_summary
           return get_trading_summary()
       except Exception:
           pass
       
       # fallback: من DB
       return db.query(Portfolio).first() or {}
   ```

**التحقق:**
```bash
curl "http://localhost:6910/api/v1/overview"
# يجب أن يظهر portfolio data مش `{}`
```

---

### P0.5: إصلاح Broker "not_configured" في System Status (15 دقيقة)

**الملف:** `app/api/system.py` أو `src/api/api_service.py` (حسب `_dashboard_health()`)

**الخطوات:**
1. ابحث عن `_dashboard_health()` أو `get_system_status()`.
2. ابحث عن الشرط اللي يتحقق من الـ broker — غالباً:
   ```python
   if broker_type == "alpaca":
       status = "ok"
   else:
       status = "degraded"
   ```

3. غيّره إلى:
   ```python
   import os
   
   alpaca_key = os.getenv("ALPACA_API_KEY") or settings.ALPACA_API_KEY
   if alpaca_key and len(alpaca_key) > 10:
       broker_status = "ok"
   else:
       broker_status = "not_configured"
   ```

**التحقق:**
```bash
curl "http://localhost:6910/api/system/status"
# يجب أن يظهر: broker: "connected" أو "ok" بدلاً من "not_configured"
```

---

### P0.6: إصلاح Halal Screening Bug — packaged_foods / entertainment (30 دقيقة)

**الملف:** `app/services/halal_screening.py` أو `src/services/halal_screening.py`

**الخطوات:**
1. ابحث عن `HARAM_INDUSTRIES`.
2. غالباً تلاقي:
   ```python
   HARAM_INDUSTRIES = {
       "alcohol", "tobacco", "gambling", "packaged_foods", "entertainment", ...
   }
   ```

3. غيّرها إلى:
   ```python
   HARAM_INDUSTRIES = {
       "alcohol", "tobacco", "gambling", "adult_entertainment", "casinos", ...
   }
   
   REVIEW_INDUSTRIES = {
       "packaged_foods",      # يحتاج مراجعة: هل فيه لحم خنزير؟
       "entertainment",       # يحتاج مراجعة: هل فيه محتوى فاسد؟
       "media", "hotels", "restaurants"
   }
   ```

4. في الـ function اللي تفحص الصناعة، غيّر المنطق:
   ```python
   def classify_industry(industry: str):
       industry_lower = industry.lower()
       if industry_lower in HARAM_INDUSTRIES:
           return {"status": "haram", "reason": f"Industry: {industry}"}
       elif industry_lower in REVIEW_INDUSTRIES:
           return {"status": "review", "reason": f"Manual review needed: {industry}"}
       else:
           return {"status": "pass", "reason": "Industry accepted"}
   ```

---

### P0.7: إصلاح Infinite Retry Loop في market_data.py (30 دقيقة)

**الملف:** `app/services/market_data.py` أو `src/services/market_data.py`

**الخطوات:**
1. ابحث عن `fetch_alpaca_intraday()`.
2. ابحث عن الـ loop اللي يعيد المحاولة عند HTTP 429.
3. غيّره ليصبح:
   ```python
   import time
   
   MAX_RETRIES = 5
   BASE_DELAY = 2  # seconds
   
   def fetch_alpaca_intraday(symbol: str):
       for attempt in range(MAX_RETRIES):
           try:
               response = requests.get(url, headers=headers)
               if response.status_code == 200:
                   return response.json()
               elif response.status_code == 429:
                   if attempt < MAX_RETRIES - 1:
                       delay = BASE_DELAY * (2 ** attempt)  # exponential backoff: 2, 4, 8, 16, 32
                       time.sleep(delay)
                       continue
                   else:
                       raise Exception(f"Rate limit exceeded after {MAX_RETRIES} retries")
               else:
                   response.raise_for_status()
           except Exception as e:
               if attempt == MAX_RETRIES - 1:
                   raise e
               time.sleep(BASE_DELAY)
       
       raise Exception("Max retries exceeded")
   ```

---

## المرحلة 1 — استقرار ومنع تكرار المشاكل (1-2 أسبوع)

### P1.1: تخزين Model Weights في Railway Volume (4 ساعات)

**الهدف:** منع ضياع الـ weights عند كل deploy.

**الخطوات:**
1. في Railway dashboard → Project → Volumes → Add Volume.
2. اسم الـ Volume: `model-weights`.
3. Mount path: `/app/models`.
4. عدّل `Dockerfile`:
   ```dockerfile
   # إنشاء symlink أو نقل الملفات
   RUN mkdir -p /app/models
   VOLUME ["/app/models"]
   ```
5. عدّل `scripts/train_models.py` ليحفظ في `/app/models/` بدلاً من المسار النسبي.
6. ارفع weights الحالية إلى الـ Volume (عبر Railway CLI أو SCP).

---

### P1.2: إضافة Alembic للـ DB Migrations (3-4 ساعات)

**الخطوات:**
```bash
pip install alembic
alembic init alembic
```

عدّل `alembic.ini`:
```ini
sqlalchemy.url = sqlite:///data/mizanquant.db
# أو PostgreSQL: postgresql://user:pass@localhost/mizanquant
```

عدّل `alembic/env.py`:
```python
from app.database import Base  # أو من أين ما تعريف الـ Base

target_metadata = Base.metadata
```

أنشئ أول migration:
```bash
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head
```

أضف إلى `scripts/start_server.sh` (أو `entrypoint`):
```bash
alembic upgrade head  # قبل تشغيل الـ server
```

---

### P1.3: FMP Safety — Early Abort + Call Counter (2 ساعات)

**الملف:** `src/services/fmp_client.py` أو `app/services/market_data.py`

**الخطوات:**
1. أضف counter عام:
   ```python
   _fmp_call_count = 0
   MAX_FMP_CALLS_PER_DAY = 240  # 250 - margin
   ```
2. في بداية كل FMP call:
   ```python
   global _fmp_call_count
   if _fmp_call_count >= MAX_FMP_CALLS_PER_DAY:
       raise Exception("FMP daily quota exhausted — aborting to prevent 429s")
   _fmp_call_count += 1
   ```
3. خفض `max_per_run` في `src/config.py`:
   ```python
   SCREENING_MAX_PER_RUN = 50  # was 80
   ```

---

### P1.4: Public Status Endpoint (بدون auth) (30 دقيقة)

**الملف:** `app/api/system.py`

**الخطوات:**
```python
from fastapi import APIRouter

@router.get("/api/v1/status/public")
def public_status():
    return {
        "status": "operational",
        "version": "1.0.0",
        "alpaca_connected": _is_alpaca_connected(),
        "last_pipeline_run": _get_last_pipeline_time(),
        "screening_count": _get_screening_count()
    }
```

---

### P1.5: Telegram Dedup — استبدال الذاكرة بـ DB Check (1 ساعة)

**الملف:** `app/services/telegram_bot.py`

**الخطوات:**
1. بدلاً من:
   ```python
   _sent_alerts = set()  # يضيع بعد restart!
   ```
2. استخدم:
   ```python
   def was_alert_sent(alert_id: str) -> bool:
       return db.query(AlertLog).filter(AlertLog.alert_id == alert_id).first() is not None
   
   def mark_alert_sent(alert_id: str):
       db.add(AlertLog(alert_id=alert_id, sent_at=datetime.utcnow()))
       db.commit()
   ```

---

## المرحلة 2 — إعادة هيكلة معمارية (3-6 أسابيع)

### P2.1: تفكيك trading_engine.py (God-File)

**الملف الحالي:** `app/services/trading_engine.py` (~1,232 سطر)

**التقسيم المقترح:**
```
app/services/trading/
├── __init__.py
├── order_builder.py          # بناء الأوامر ( market/limit/stop )
├── broker_submitter.py       # إرسال الأوامر لـ Alpaca
├── trade_coordinator.py      # التنسيق بين الأوامر والتنبيهات
├── reconciler.py             # المطابقة بين الأوامر المرسلة والمنفذة
└── idempotency.py            # تتبع الأوامر لمنع التكرار
```

**الخطوات:**
1. انسخ كل class/function من `trading_engine.py`.
2. وزّعها على الملفات الجديدة.
3. عدّل الـ imports في كل الملفات اللي تستخدم `trading_engine.py`.
4. شغّل الـ tests للتأكد من عدم الكسر.

---

### P2.2: دمج AI Agent — حذف ai_agent.py (2-3 ساعات)

**الملفات:** `app/ai_agent.py` + `app/services/claude_agent.py`

**الخطوات:**
1. قارن القدرات بين الملفين.
2. انقل أي logic فريد من `ai_agent.py` إلى `claude_agent.py`.
3. حدّث كل الـ imports في المشروع.
4. احذف `app/ai_agent.py`.
5. تأكد من `pytest` يمر.

---

### P2.3: تفعيل Redis للـ Caching (2-3 ساعات)

**الخطوات:**
```bash
# Redis موجود في requirements — فعّله فقط
pip install redis
```

أضف إلى `app/cache/redis_cache.py`:
```python
import redis
import json
from functools import wraps

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

def cached(ttl_seconds=300):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{json.dumps(args)}:{json.dumps(kwargs)}"
            cached_val = r.get(key)
            if cached_val:
                return json.loads(cached_val)
            result = func(*args, **kwargs)
            r.setex(key, ttl_seconds, json.dumps(result))
            return result
        return wrapper
    return decorator
```

استخدمه على الـ endpoints الثقيلة:
```python
@app.get("/api/market/context")
@cached(ttl_seconds=60)
def get_market_context():
    ...
```

---

### P2.4: JWT + RBAC بدلاً من Flat API Key (6-8 ساعات)

**الخطوات:**
```bash
pip install python-jose[cryptography] passlib[bcrypt]
```

أنشئ `app/auth/`:
```python
# app/auth/jwt.py
from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
```

**الأدوار:**
- `admin`: كل شيء
- `operator`: تشغيل pipeline + رؤية data
- `readonly`: رؤية فقط

---

### P2.5: APScheduler بدلاً من subprocess (2 ساعات)

**الملف:** `app/services/scheduler.py`

**الكود الحالي (غالباً):**
```python
subprocess.run(["python", "run_pipeline.py"])
```

**الكود المطلوب:**
```python
from src.pipeline.unified_pipeline import UnifiedPipeline

def run_pipeline_job():
    pipeline = UnifiedPipeline()
    pipeline.run_full_pipeline()

scheduler.add_job(run_pipeline_job, CronTrigger(hour=8, minute=0))
```

---

## المرحلة 3 — ميزات استراتيجية (2-4 أشهر)

### P3.1: MLflow Model Registry (4-6 ساعات)

**الخطوات:**
```bash
pip install mlflow
```

في `scripts/train_models.py`:
```python
import mlflow

mlflow.set_tracking_uri("file:///app/mlruns")  # أو Railway Volume

with mlflow.start_run():
    mlflow.log_param("epochs", epochs)
    mlflow.log_metric("val_loss", val_loss)
    mlflow.pytorch.log_model(model, "transformer_model")
    mlflow.log_artifact("models/transformer/20260519.pt")
```

---

### P3.2: ترقية Railway (5 دقائق)

في Railway Dashboard:
1. Project Settings → Plans.
2. اختر **Standard** (8 GB RAM, 2 vCPUs).
3. الـ deploy راح يصير أسرع 4x.

---

### P3.3: ترقية FMP إلى Starter Plan (10 دقائق)

1. ادخل على [financialmodelingprep.com](https://financialmodelingprep.com).
2. اشترك في **Starter Plan** ($14/شهر).
3. احصل على API key جديد.
4. حدّث `ALPACA_API_KEY` → لا، `FMP_API_KEY` في Railway Variables.
5. غيّر `MAX_FMP_CALLS_PER_DAY` إلى `2900`.

---

### P3.4: WebSocket Dashboard للتحديث الفوري (8-12 ساعة)

**الخطوات:**
1. أضف `websocket` endpoint في `workspace_server.py`:
   ```python
   from fastapi import WebSocket
   
   @app.websocket("/ws/dashboard")
   async def dashboard_ws(websocket: WebSocket):
       await websocket.accept()
       while True:
           data = await get_latest_portfolio()
           await websocket.send_json(data)
           await asyncio.sleep(5)  # update every 5s
   ```

2. في `forecast-panel.html` و`dashboard-legacy.html`:
   ```javascript
   const ws = new WebSocket("ws://localhost:6910/ws/dashboard");
   ws.onmessage = (event) => {
       const data = JSON.parse(event.data);
       updateUI(data);
   };
   ```

---

### P3.5: إضافة بورصة EGX (السوق المصري) (20-30 ساعة)

**المصدر:** EGX API أو Bloomberg API (مدفوع) أو web scraping من [egx.com.eg](https://www.egx.com.eg).

**الخطوات:**
1. أنشئ `app/services/egx_client.py`.
2. اكتب parser لبيانات EGX (HTML → JSON).
3. أضف `symbol_format="EGX"` في `validate_symbol()`.
4. عدّل `halal_screening.py` ليدعم معايير هيئة الرقابة المالية المصرية + AAOIFI.
5. أضف `EGX_SYMBOLS` في `src/config.py`.

---

## الجدول الزمني المُقترح

| الأسبوع | المهمة | الوقت المُقدَّر | المسؤول |
|---------|--------|----------------|---------|
| **يوم 0** | P0.1 + P0.2 (Forecast fix) | 3 ساعات | Backend Dev |
| **يوم 0** | P0.3 (Screener Pipeline) | 2 ساعات | Backend Dev |
| **يوم 1** | P0.4 + P0.5 (Overview + Status) | 1 ساعة | Backend Dev |
| **يوم 1** | P0.6 + P0.7 (Halal + Retry) | 1 ساعة | Backend Dev |
| **يوم 2** | اختبار شامل + تحقق | 4 ساعات | QA |
| **أسبوع 2** | P1.1 + P1.2 + P1.3 (Volume + Alembic + FMP) | 12 ساعة | Backend/DevOps |
| **أسبوع 3** | P1.4 + P1.5 + P2.1 (Status + Dedup + Refactor) | 16 ساعة | Backend |
| **أسبوع 4-5** | P2.2 + P2.3 + P2.4 (AI Merge + Redis + JWT) | 24 ساعة | Backend |
| **أسبوع 6** | P2.5 (Scheduler fix) + اختبار | 8 ساعات | Backend/QA |
| **شهر 2** | P3.1 + P3.2 + P3.3 (MLflow + Railway + FMP) | 12 ساعات | DevOps |
| **شهر 3** | P3.4 (WebSocket) | 12 ساعة | Full-stack |
| **شهر 4** | P3.5 (EGX) | 30 ساعة | Backend |

---

## قائمة التحقق النهائية (Checklist)

### بعد كل إصلاح في المرحلة 0، تأكد من:
- [ ] الـ API يرد `200 OK`
- [ ] البيانات اللي يرجعها غير فاضية (`[]` أو `{}`)
- [ ] الـ Dashboard يعرض البيانات بدون refresh
- [ ] `pytest` يمر على الـ test files المرتبطة
- [ ] `git commit` برسالة واضحة

### أوامر التحقق السريعة:
```bash
# 1. Health
curl http://localhost:6910/health

# 2. Forecast (سريع)
curl "http://localhost:6910/api/forecast/transformer?symbol=AAPL&epochs=3&n_splits=1"

# 3. Portfolio
curl http://localhost:6910/api/trading/summary

# 4. Screener
curl "http://localhost:6910/screener?universe=halal"

# 5. System Status
curl http://localhost:6910/api/system/status

# 6. Market Context
curl http://localhost:6910/api/market/context

# 7. Tests
pytest tests/ -x -q
```

---

## تعديل عاجل — إكتشاف جديد (2026-05-20): النظام متصل على Paper Account فاضي

> **⚠️ هذا يُغيّر تشخيص جزء كبير من المشاكل.**
>
> الصور المُرفقة تُظهر أن الـ Dashboard متصل على **Paper Account (PA3JESVTCPNL)** بـ **Equity $0.00** وليس Live Account اللي فيه $98K. هذا يفسر:
> - Portfolio فارغ → Paper account فاضي
> - Positions 0 → ما فيه صفقات في الحساب الورقي
> - Daily Workflow Timeout → APIs تستدعي حساب فاضي → error
> - Risk Desk Failed → Consensus/VaR يعتمدون على بيانات غير موجودة
> - Backtest Internal Server Error → bug منفصل في endpoint
> - Trading Lab يعرض "20503d ago" → بيانات demo/وهمية
>
> **الحل الفوري:**
> 1. اذهب إلى Profile Settings في Dashboard.
> 2. غيّر API Keys من Paper إلى Live (القديمة: `PKYE2RHKI35PCWXER6NZ74TLGE`).
> 3. أو: في Alpaca Dashboard، حوّل رصيد من Live إلى Paper Trading.
> 4. أعد تحميل الصفحة.

### P0.8: إصلاح Broker Connection — التبديل بين Live/Paper (15 دقيقة)

**الملف:** `app/config.py` أو `.env` أو Railway Variables

**الخطوات:**
1. تأكد من الـ env variables:
   ```bash
   # للـ Live Account (بيانات حقيقية)
   ALPACA_API_KEY=PKYE2RHKI35PCWXER6NZ74TLGE
   ALPACA_SECRET_KEY=EvzYjB4sWq8FgSZkvkKz2RM5x88MxMyqiJvKq75o9Gah
   ALPACA_BASE_URL=https://api.alpaca.markets
   
   # للـ Paper Account (اختبار)
   # ALPACA_API_KEY=PA3JESVTCPNL...
   # ALPACA_BASE_URL=https://paper-api.alpaca.markets
   ```

2. أضف toggle في System Settings:
   ```python
   # app/api/system.py
   @router.post("/api/system/broker-mode")
   def set_broker_mode(mode: str):  # "live" أو "paper"
       if mode not in ["live", "paper"]:
           raise HTTPException(400, "mode must be 'live' or 'paper'")
       os.environ["ALPACA_BASE_URL"] = (
           "https://api.alpaca.markets" if mode == "live" else "https://paper-api.alpaca.markets"
       )
       return {"status": "ok", "mode": mode, "url": os.environ["ALPACA_BASE_URL"]}
   ```

3. أضف indicator واضح في Dashboard header:
   ```html
   <span class="badge" id="broker-mode">PAPER</span>
   <!-- يتغير إلى "LIVE" عند الاتصال بالحساب الحقيقي -->
   ```

**التحقق:**
```bash
curl http://localhost:6910/api/trading/summary
# يجب أن يُرجع equity > 0 إذا كان Live، أو equity = 0 إذا كان Paper
```

---

## ملاحظات تنفيذية

1. **لا تركّب كل الإصلاحات دفعة واحدة.** ركّب P0.1 → اختبر → commit → P0.2 → اختبر → commit.
2. **إذا طلع خطأ في pytest** في أي مرحلة، لا تنتقل للمرحلة اللي بعدها.
3. **الـ FMP quota** يتجدد كل 24 ساعة عند منتصف الليل ET. إذا نفد، ارجع لـ P0.3 في اليوم الثاني.
4. **Railway deploy:** كل commit على main يُطلق deploy تلقائياً. تأكد من أن Alembic migration يشتغل في entrypoint قبل الـ server.
5. **قبل أي deploy:** تأكد من `ALPACA_BASE_URL` — إذا نسيته، النظام يتصل على Paper فاضي وكل شيء يبدو "معطل".

---

*خطة مُعدّلة بواسطة: Claude (Post-Diagnosis Correction) | 2026-05-20*
*التعديلات الجوهرية:*
- *M1: تصحيح من "weights مفقودة" إلى "endpoint يُعيد التدريب on-demand"*
- *P0.8 (جديد): إضافة اكتشاف Paper Account فارغ + حل التبديل بين Live/Paper*
