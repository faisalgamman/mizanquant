# تقرير المراجعة والإغلاق — تدقيق تنفيذ DeepSeek مقابل خطة Claude المعمارية

**التاريخ:** 2026-06-05
**المراجِع:** Claude (Principal Architect / Tech Lead)
**النطاق:** working tree غير مُلتزَم (تنفيذ DeepSeek للخطة الجراحية A1–A6)
**المنهجية:** كل بند مُثبَت مقابل الكود الفعلي بدليل `ملف:سطر` أو إثبات `symtable` — لا اعتماد على ملخّص مكتوب.

---

## 0) الملخّص التنفيذي

| Milestone | الوصف | الحالة | الخطورة |
|-----------|-------|--------|---------|
| **A1** | `nixpacks.toml` → `python app/workspace_server.py` | ✅ مكتمل | — |
| **A2** | تحويل `halal_screener` إلى مكتبة (إزالة `app=FastAPI`) | ❌ لم يُنفَّذ | عالية |
| **A3** | إصلاح نداءات `get_account()` بلا `strategy_id` | 🟡 جزئي (3 باقية) | متوسطة |
| **A4** | كاش OHLCV دفعي في `usx_pro_filter` | 🔴 معطوب + انحراف مصدر | **حرجة** |
| **A5** | تفكيك `workspace_server` | 🟡 نظيف لكن 2.3% فقط | منخفضة |
| **A6** | تنظيف اليتامى | 🟡 جزئي (خلّف backup) | منخفضة |

**التقييم العام: 4.5 / 10** — الميزة العنوانية (الكاش) تُسقط النظام عند أول مسحة، وأخطر بند معماري (A2) لم يُلمَس، والاختبارات لم تُشغَّل.
**قرار الحوكمة:** ⛔ **لا التزام (no commit)** حتى إغلاق البنود الحرجة أدناه.

---

## 1) الثغرات التفصيلية وإجراءات الإصلاح

### 🔴 F-1 (حرجة) — كاش الـ5 دقائق: `UnboundLocalError` يُعطّل المسح بالكامل
**الملف:** `app/services/usx_pro_filter.py`
**الدليل:** إثبات `symtable` على دالة `filter_universe`:
```
_ohclv_batch_cache → is_local=True, is_global=False, is_assigned=True
```
**السبب الجذري:** السطر 481 يعيد إسناد المتغيّر العام `_ohclv_batch_cache = {...}` بدون التصريح `global`. هذا يجعل بايثون يعامله **محلياً في كامل الدالة**، فالقراءة في السطر 473 (`if _ohclv_batch_cache["data"]`) تسبق أي إسناد محلي → `UnboundLocalError` في **كل** استدعاء.
**الأثر:** Stage 1 من قمع الإشارات يرمي استثناءً كل مسحة → صفر مرشّحين → النظام لا ينتج أي إشارة.

**الإصلاح (سطر واحد) — أضف التصريح في رأس `filter_universe`:**
```python
def filter_universe(symbols, min_score=DEFAULT_MIN_SCORE, max_workers=3, skip_regime_check=False):
    global _ohclv_batch_cache          # ← السطر المفقود
    _market_cache.clear()
    ...
```
**معيار القبول:** استدعاء `filter_universe(["AAPL","MSFT"])` مرّتين متتاليتين دون استثناء، والثانية تسجّل `reused cached OHLCV batch`.

---

### 🟠 F-2 (حرجة سلوكياً) — انحراف مصدر البيانات يكسر شرط «تطابق المرشّحين»
**الملف:** `app/services/usx_pro_filter.py:302, 469–484`
**السبب الجذري:** المسار القديم كان يجلب عبر `_fetch_daily → halal_screener.fetch_yf` (**yfinance**). المسار الجديد يحقن `fetch_alpaca_batch` (**Alpaca IEX**). مصدران مختلفان (تعديلات أرباح، آخر شمعة، حجم IEX المجزّأ) → **درجات/مرشّحون قد يختلفون**.
**مخالفة الخطة:** بند A4 اشترط حرفياً «تطابق المرشّحين مع المسار القديم (انحدار)». تغيير المصدر = **تغيير سلوك**، لا تحسين أداء.

**خياران للإغلاق (يُختار واحد):**
- **(أ) الأأمن — توحيد المصدر:** اجعل الجلب الدفعي من نفس مصدر yfinance (batch wrapper حول `fetch_yf`) → تطابق مضمون.
- **(ب) إثبات المكافئة:** أبقِ Alpaca لكن أضِف اختبار `test_score_symbol_source_parity` يُثبت تطابق `passes`/`score` ضمن هامش على عيّنة ≥30 رمزاً قبل القبول.

**نقطة ثانوية (أداء):** `_next_earnings_days(symbol)` (سطر 328) ما زال نداء yfinance **لكل رمز** داخل `score_symbol` → ادّعاء «10-50×» جزئي؛ عنق الزجاجة (الأرباح) لم يُجمَّع. يُوصى بتجميع/كاش الأرباح لاحقاً.

---

### 🟠 F-3 (عالية) — A2 لم يُنفَّذ: الـ Split-Brain لم يُحلّ جذرياً
**الملف:** `halal_screener.py:118`
**الدليل:** ما زال السطر `app = FastAPI(lifespan=_app_lifespan)` قائماً + يضمّن 7 رواتر (سطور 4918–4954) + lifespan مستقل يبدأ scheduler.
**الواقع:** DeepSeek حاذى `nixpacks` فقط (A1)، لكن `halal_screener` **ما زال تطبيقاً قابلاً للنشر مستقلاً**. الازدواجية المعمارية قائمة؛ أي `uvicorn halal_screener:app` يدوي يشغّل دماغ تداول ثانياً (scheduler مزدوج).

**الإصلاح (A2 الحقيقي):**
1. أبقِ تجميع الرواتر في دالة `get_routers()` أو متغيّر `routers` يستوردها `workspace_server`.
2. احذف/احرس `app = FastAPI(...)` خلف `if __name__ == "__main__"` فقط، أو انقل تعريف الـ app إلى ملف نشر واحد.
3. تأكّد أن `_app_lifespan` (وبدء الـ scheduler فيه) **لا يُستورَد ضمنياً** عند استيراد الرواتر.
**معيار القبول:** استيراد رواتر halal_screener داخل workspace_server **لا يبدأ** scheduler ثانياً (اختبار F-T3/T4 أدناه).

---

### 🟡 F-4 (متوسطة) — A3 ناقص: 3 نداءات `get_account()` بلا `strategy_id`
**المواقع المتبقية:**
- `app/api/v1/system.py:188`
- `app/routers/admin.py:78`
- `app/routers/admin.py:124`

**شرط سلامة الإصلاح (ألّا يكسر fallback الحسابات القديمة):**
1. **عقد التوقيع ثابت:** `get_account(strategy_id: str | None = None)` — تحقّق بـ `inspect.signature` أن الوسيط اختياري (المستدعون القدامى لا ينكسرون).
2. **سلوك `None` آمن:** حين `strategy_id=None` يجب السقوط إلى **أول مفتاح في `STRATEGY_CONFIGS`** (اعتماد صالح)، لا إلى `DEFAULT/legacy` (الذي يعطي 401).
3. **اختبار مكافئة:** `get_account(None)` يُطابق `get_account('A')` أو يفشل برسالة واضحة، لا 401 صامت.

**الإصلاح:** مرّر `strategy_id` صراحةً في المواقع الثلاثة، أو وجّهها عبر `broker.factory.get_broker(strategy_id)`.

---

### 🟡 F-5 (منخفضة) — تفكيك أدنى من المطلوب (A5)
**الواقع المقيس:** `workspace_server.py`: 7693 → 7513 = **180 سطراً (2.3%)** فقط. المستخرَج: 6 مسارات GET للقراءة فقط إلى `app/api/dashboard_api.py`.
**الإيجابي:** التفكيك **نظيف** — موصول (`include_router` سطر 6842)، صفر تكرار، صفر يتيم، الـ routing سليم.
**الناقص — مجموعات يجب استخراجها تالياً (بالأولوية):**

| المجموعة | البادئة | السبب |
|---|---|---|
| Bootstrap/Lifespan/Scheduler | سطور 59–132 | **الأخطر** — منطق تشغيل الدماغ يجب أن يخرج لوحدة `app/bootstrap.py` |
| AI/Agent | `/api/ai/*`, `/api/agent/*`, `/api/chart/agent/*` | الأكبر والأكثر تشابكاً |
| Screener/Selection | `/api/screener/*`, `/api/selection/*` | منطق العمل الجوهري |
| Context/Regime | `/api/context/bundle`, `/api/chart/rotation` | يستهلكه الـ header strip |
| Investors/Forecast | `/api/investors/*`, `/api/forecast/*` | كتلة كبيرة منفصلة |

**ملاحظة حوكمة:** كل استخراج لاحق = milestone مستقل باختبار route-parity قبله وبعده.

---

### 🟡 F-6 (منخفضة) — مخلّفات لم تُنظَّف (A6)
- **متبقٍّ:** `app/config_backup_20260603.py` (20KB) داخل الشجرة → يخالف «نظّف مخلفاتك». **الإجراء:** حذفه (مع التأكّد أنه نسخة من `app/config.py` لا فرق وظيفي).
- ✅ تمّ: حذف `REPAIR_PLAN.md`/`STATUS_REPORT.md`، نقل سكربتات الجذر إلى `scripts/`.
- ملفّات من جلستي (ليست من DeepSeek): `graph_query.sh`, `.graph_context` — يمكن إبقاؤها كأداة أو إضافتها لـ `.gitignore`.

---

## 2) خطة الاختبار — 5 سيناريوهات حرجة (+1 إضافي)

| # | الاسم | الملف المقترح | يكشف | متوقّع الآن |
|---|------|---------------|------|------------|
| T1 | `test_filter_universe_cache_hit_no_unboundlocal` | `tests/test_usx_cache.py` | F-1: استدعاءان خلال 300s بلا `UnboundLocalError` + إعادة استخدام الكاش | **يفشل** |
| T2 | `test_app_route_parity_after_extraction` | `tests/test_routing_parity.py` | F-5: وجود الـ6 مسارات (لا 404) + لا تكرار | يمرّ |
| T3 | `test_lifespan_single_scheduler_boot` | `tests/test_lifespan.py` | بدء scheduler مرّة واحدة عبر `TestClient(workspace_server.app)` وإيقافه | يحتاج تحقّق |
| T4 | `test_no_dual_app_double_lifespan` | `tests/test_lifespan.py` | F-3: استيراد رواتر halal_screener لا يبدأ scheduler ثانياً | **قد يفشل** |
| T5 | `test_get_account_none_strategy_fallback` | `tests/test_account_fallback.py` | F-4: `get_account(None)` ≠ 401 لمسارات system/admin | يحتاج تحقّق |
| T6 (إضافي) | `test_score_symbol_source_parity` | `tests/test_usx_cache.py` | F-2: تطابق `passes`/`score` بين Alpaca-df و yfinance-df | يحتاج تحقّق |

**هيكل T1 (الأهم — يثبت الـcrash):**
```python
def test_filter_universe_cache_hit_no_unboundlocal(monkeypatch):
    import app.services.usx_pro_filter as u
    # بيانات وهمية لتجنّب الشبكة
    monkeypatch.setattr(u, "fetch_alpaca_batch", lambda syms, period="2y": {s: _fake_df() for s in syms})
    monkeypatch.setattr(u, "check_market_regime", lambda use_cache=True: u.RegimeReport(overall_ok=True, reason="ok", vix_rank=0.2))
    out1, _ = u.filter_universe(["AAPL", "MSFT"])     # يجب ألّا يرمي UnboundLocalError
    out2, _ = u.filter_universe(["AAPL", "MSFT"])     # يجب أن يعيد استخدام الكاش
    assert isinstance(out1, list) and isinstance(out2, list)
```

---

## 3) قائمة الإغلاق التنفيذية (بالترتيب الإلزامي)

```
[ ] 1. ⛔ لا تلتزم أي شيء بعد.
[ ] 2. F-1: أضف `global _ohclv_batch_cache` في filter_universe   (سطر واحد، يزيل الـcrash)
[ ] 3. F-2: احسم مصدر البيانات — وحّد على yfinance batch  أو  اكتب T6 وأثبت التطابق
[ ] 4. F-4: أغلق نداءات 401 الثلاثة (system.py:188، admin.py:78/124) عبر strategy_id صريح
[ ] 5. F-6: احذف app/config_backup_20260603.py
[ ] 6. اكتب T1–T6 وشغّلها + الـ68 اختباراً الحالية → يجب أن تَخضرّ كلها
[ ] 7. التزام لكل milestone على حدة (A1، A4، A5، A6 منفصلة) — لا commit عملاق
[ ] 8. افتح A2 كبند تالٍ صريح: إزالة app=FastAPI من halal_screener (F-3)
```

**بوابة القبول النهائية لإغلاق الـMilestone:**
- ✅ `pytest` أخضر (68 + 6 جديدة)، صفر regression.
- ✅ `filter_universe` يعمل ويعيد استخدام الكاش بلا استثناء.
- ✅ جرد `workspace_server.app.routes` قبل/بعد متطابق (لا endpoint مفقود).
- ✅ scheduler يبدأ **مرّة واحدة** فقط.
- ✅ صفر نداء `get_account()` بلا strategy_id في مسارات الإنتاج.
- ✅ شجرة العمل نظيفة (لا ملفات backup/يتيمة).

---

## 4) ملاحظة منهجية
هذا التقرير وثيقة فقط — لم يُعدَّل أي كود إنتاجي أثناء كتابته. بنود الإصلاح أعلاه جاهزة للتنفيذ الجراحي حال الموافقة، بالترتيب الوارد في §3.
