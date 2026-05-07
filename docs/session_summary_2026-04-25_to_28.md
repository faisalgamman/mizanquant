# Session Summary — openbb-trading
## 2026-04-25 to 2026-04-28

ملخّص شامل لجلسة تطوير مكثّفة على مشروع `openbb-trading`. كل عمل تمّ
الحديث عنه أو رفعه إلى main موثّق هنا مع رمز الـ commit ومراجع الملفّات.

---

## 1. الوضع المبدئي

- **اللغة:** Python 3.11 (Dockerfile)، النشر على Railway Hobby plan
- **الاستراتيجيّات:** A=HANA (momentum), B=marem (mean-reversion), C=mazem (ML)
- **الحساب:** كلّها على Alpaca paper, base URL `https://paper-api.alpaca.markets`
- **الأهداف:** تطبيق كتاب Ernest Chan كاملاً + ربط IBKR + تحويل البرنامج
  إلى advisor يرسل إشارات للـ Telegram

---

## 2. Phases 1-8 (Chan Refactor)

ستّ مراحل فلسفيّة مأخوذة مباشرة من *Algorithmic Trading: Winning
Strategies and Their Rationale* للدكتور Ernest P. Chan.

| Phase | Chan Ch | الموضوع | الـ module | الاختبارات | Commit |
|-------|---------|---------|----------|-----------|--------|
| 1 | Ch.2 | Backtest hygiene (look-ahead removal, 20bps costs, Deflated Sharpe, permutation p-value, reality check) | `app/services/backtest_qc.py` | 12 | (مدمج عبر عدّة commits) |
| 2 | Ch.7 | Continuous Kelly سُمك مع shrinkage و fractional و max-cap | `app/services/kelly.py` | 9 | `f867064` |
| 3 | Ch.3 | Stationarity gate (ADF + Hurst R/S + OU half-life) للاستراتيجيّة B | `app/services/stationarity.py` | 10 | `0e916d0` |
| 4 | Ch.5 | Momentum quality gate (t-stat + 12-1 + Hurst) للاستراتيجيّة A | `app/services/momentum_quality.py` | 9 | `1070a2d` |
| 5 | Ch.4 | Cointegration toolkit (Engle-Granger, hedge ratio, spread z-score) — جاهز لاستراتيجيّة pairs مستقبليّة | `app/services/cointegration.py` | 8 | `d0b20d2` |
| 6 | Ch.6 | Per-asset strategy regime router (trending/ranging/noisy) — يوجّه التداول للاستراتيجيّة المناسبة | `app/services/strategy_regime.py` | 5 | `dc4f521` |
| 8 | epilogue | Paper-trade graduation gate + preflight script (DSR≥0.60, n≥30, days≥14, symbols≥8, mean>0) | `app/services/paper_trade_gate.py` + `scripts/preflight.py` | 8 | `bf035e6` |

**النتيجة الإجماليّة:** 6 modules جديدة، 61 اختباراً يمرّ، إصلاح bug في
`deflated_sharpe` (تم تطبيق صيغة Lo/Mertens على Sharpe السنوي بدلاً من
per-period) — كان var_sr يصبح سالباً على العيّنات القويّة و DSR ينهار للصفر.

---

## 3. إصلاحات تشغيليّة على Alpaca

### 3.1 "Buys but never sells" — `8d1a36e`
- **السبب:** أوامر bracket تُرسَل بـ `time_in_force="day"`. أرجل SL/TP
  ترث TIF فتُلغى عند إغلاق الجلسة، فالصفقة تستمرّ بدون خروج تلقائي.
- **الإصلاح:** تحويل إلى `gtc` للأوامر الجديدة.
- **استرداد الصفقات العالقة:** أُضيف `rearm_orphan_position()` و
  `rearm_all_orphans()` + endpoint `POST /admin/rearm_orphans` يُعيد تسليح
  SL/TP كأوامر GTC مستقلّة لكل position بدون أرجل.

### 3.2 Alpaca 401 Spam — `ad4daa6`
- **السبب:** المتغيّر `ALPACA_API_KEY` (الافتراضي بدون suffix) كان قديماً
  وغير صالح. fill_watcher كان يجرّبه كلّ 5 ثوانٍ → 17,000 طلب فاشل يومياً.
- **التشخيص:** أُضيف endpoint `GET /admin/alpaca_check` يفحص كل
  credentials (DEFAULT + A + B + C) ويعرض حالة كل واحدة.
- **الإصلاح:** المستخدم حذف `ALPACA_API_KEY` و `ALPACA_SECRET_KEY` من
  Railway Variables. الاستراتيجيّات A/B/C كلّها ACTIVE (~$5,000 كلّ
  واحدة) ولا 401 بعدها.

---

## 4. Phase A — Broker Abstraction (`f5cdabf`)

طبقة عزل قبل ربط IBKR — additive، non-breaking.

```
app/services/broker/
├── __init__.py          واجهة الحزمة
├── base.py              BrokerClient Protocol (7 methods)
├── alpaca_adapter.py    AlpacaBroker pass-through
└── factory.py           get_broker(strategy_id) factory
```

**routing عبر env vars:**
- `STRATEGY_BROKER_<id>=ibkr` لاستراتيجيّة محدّدة
- `BROKER_TYPE=alpaca|ibkr` افتراضي عام
- اسم broker مجهول → fallback إلى Alpaca + warning (typo لا يوقف التداول)

**7 contract tests** يمرّون. كل callsites القائمة (trading_engine،
fill_watcher، reconciliation) لم تُمسّ — Phase A purely additive.

---

## 5. Phase B — IBKR Adapter (`598bc8f`)

`IBBroker` يطبّق `BrokerClient` Protocol عبر `ib_insync` socket. مكتوب
ومُختَبَر، **خامل (inert)** على Railway حالياً.

- يُترجم Alpaca-style payloads إلى ib_insync Contract+Order
- bracket orders → 3 أوامر مع `transmit` flags صحيحة
- per-strategy client IDs لمنع تصادم socket
- degradation رشيق (gateway down يرجع None بدلاً من crash)
- 10 اختبارات على mocks
- runbook كامل في `docs/ibkr_setup.md`

### 5.1 محاولة تشغيل IB Gateway على Railway

أُنشئت خدمة `ib-gateway` ثانية باستخدام `gnzsnz/ib-gateway:latest`.
بعد 5+ ساعات debugging شاملة:

✅ **ما نجح:**
- IBC login إلى paper account `DUP607506`
- TCP connection من openbb-trading إلى ib-gateway:4002
- VNC manual configuration (Trusted IPs + disable localhost-only)
- إعدادات API الصحيحة: `Allow connections from localhost only` معطّل،
  Trusted IPs تحوي `127.0.0.1` و `0.0.0.0`

❌ **ما لم يعمل:**
- IB API handshake يُتمَم بـ `TimeoutError` رغم قبول TCP
- Logs الـ gateway تُظهر: `Client disconnected before version was sent`
- السبب الجذري: شبكة Railway الـ private (WireGuard) تتدخّل في
  بروتوكول IB API — الـ packets الأولى لا تعبر بشكل صحيح
- Volume mount `/root/Jts` لا يحفظ إعدادات Trusted IPs (تعود للوضع
  الافتراضي عند كل restart)

### 5.2 القرار النهائي — تأجيل IB

**التوصية المتّفق عليها:** إيقاف Phase B والتركيز على Alpaca paper حتّى:
1. تخرّج Phase 8 (30 صفقة + 14 يوم + DSR ≥ 0.60)
2. تمويل ≥$25k على الحساب الحقيقي (لتجاوز قيد PDT)
3. الانتقال لـ VPS منفصل لـ IB Gateway (Hetzner CX11 ~$5/شهر)

الخدمة `ib-gateway` متوقّفة على Railway الآن. الكود في `app/services/broker/ibkr_adapter.py`
يبقى جاهزاً للاستخدام — يكفي تفعيل `STRATEGY_BROKER_A=ibkr` بعد توفّر
الشروط.

---

## 6. Signals Advisor — Telegram لتنفيذ يدوي (`e68c2f9` + `3d5ae85` + `dec8971` + `e11e1c4`)

تحويل البرنامج إلى **advisor** يبحث عن إشارات BUY ويرسلها للـ Telegram،
المستخدم ينفّذها يدوياً على IBKR.

### 6.1 الـ Module

`app/services/signals_advisor.py`:
- `scan_universe_for_strategy(sid)` — يفحص الكون عبر استراتيجيّة واحدة
- `scan_and_notify_strong_buys(...)` — يعمل على A/B/C ويرسل Telegram

كل إشارة Telegram تحوي:
```
🎯 STRONG BUY SIGNAL
━━━━━━━━━━━━━━━
Symbol:     AAPL
Strategy:   HANA / Momentum
Confidence: 85%
━━━━━━━━━━━━━━━
Price:      $185.50
Stop Loss:  $179.93  (−3.0%)
Take Prof:  $196.63  (+6.0%)
Shares:     5  (≈ $927.50 notional)
Risk:       $50.00  (1.0% of $5000)
━━━━━━━━━━━━━━━
Votes:      BUY 8 / SELL 0 / HOLD 1
Time:       2026-04-27 09:15 ET
```

### 6.2 Telegram BUY-Only Filter

`app/services/telegram_alert.py`:
- متغيّر `TELEGRAM_BUY_ONLY=true` (افتراضي)
- يكتم كل الرسائل ما عدا التي تحوي:
  - `STRONG BUY SIGNAL`
  - `PRE-MARKET SIGNALS`
  - `READY TO TRADE`
- ضوضاء صفر — فقط إشارات قابلة للتنفيذ

### 6.3 الجدولة

5 مسوحات يوميّة (ET أيّام العمل):
- 09:00 — pre-market (موجود)
- 10:30 — post-open
- 12:00 — mid-session
- 14:30 — pre-close
- 16:30 — post-close

### 6.4 Endpoint للتشغيل اليدوي

`POST /admin/scan_signals`:
- `?symbols=AAPL,MSFT` (≤20 رمز) → synchronous JSON
- `?dry_run=true` → preview بدون Telegram
- بدون symbols → background thread (full universe)
- 8 workers parallelism

---

## 7. USX Pro V4 Filter Integration (Started)

### 7.1 الفكرة

Pipeline ثلاث مراحل:
```
HALAL_STOCKS  ─►  USX V4 (regime + per-stock)  ─►  AI consensus  ─►  Telegram
   (~357)         (~50-100 candidates)             (5-15 signals)    (BUY only)
```

### 7.2 الـ Module المبدئي

`app/services/usx_pro_filter.py` (تمّ إنشاؤه، لم يُربَط بعد):

**Stage 1 — market regime gate** (one-shot):
- SPY > EMA21 daily
- VIX percentile rank < 85
- HY credit (HYG/LQD + HYG/TLT vs EMA20)
- Breadth proxy (SPY vs SMA200)
- إن فشل أيّ شرط → no signals today

**Stage 2 — per-symbol qualifier:**
- ADV20 ≥ $50M
- Price ≥ $10
- |% from EMA200| ≤ 15%
- Daily uptrend (price > EMA21 > EMA50)
- Earnings ≥ 5 days away (block on missing data — fail-safe)
- Weighted score ≥ 65/100 (10 components matching Pine Script)

**100-point weighted score (long-only):**
| Component | Max Weight |
|-----------|-----------|
| Daily Trend (price>EMA21>EMA50) | 20 |
| Regime (SPY bull + VIX OK) | 15 |
| MACD histogram positive & rising | 10 |
| RSI in 45..65 zone | 8 |
| ADX ≥ 20 with DI+ > DI- | 7 |
| RS vs SPY (5-bar excess ROC, tiered) | 20 |
| Volume ratio (≥1.2: 6, ≥1.0: 3) | 6 |
| Gap up 0..1.5% | 4 |
| Bollinger squeeze | 5 |
| Above VWAP | 5 |
| **Total** | **100** |

### 7.3 الخطوات الباقيّة (لم تُكمَل بعد)

- [ ] ربط `usx_pro_filter.filter_universe()` في `signals_advisor.py`
      كمرحلة قبل AI consensus
- [ ] اختبارات contract لـ usx_pro_filter
- [ ] إضافة slot scheduling ساعي (كل ساعة 9:30-15:30 ET)
- [ ] رفع commit + اختبار e2e

---

## 8. Out-of-Scope Register (`aa71f29`)

سجلّ دائم في `docs/out_of_scope.md` بـ 10 بنود مؤجَّلة:

1. Pairs trading Strategy D (يبني على Phase 5)
2. Hidden Markov Model regime detector
3. Walk-forward optimization
4. Interactive Brokers backend (Phase A/B جاهزان، تأجيل التشغيل)
5. Order-book / L2 microstructure
6. Detailed transaction-cost model
7. Survivorship-free historical universe
8. Cross-strategy correlation-aware Kelly
9. Anomaly / data-quality alerts
10. Reproducibility seal (git SHA + data hash)

**القاعدة:** لا تُضاف أيّ منها قبل تخرّج A/B/C على paper.

---

## 9. الإصلاحات الجانبيّة على البنية التحتيّة

- **Dockerfile** (`e8e2e6a`): يقرأ من `requirements.txt` بدل قائمة
  مكوّبة. كان `nltk` غير مثبَّت.
- **`.dockerignore`** (`e8e2e6a`): يستبعد tests/, reports/, logs/, *.docx,
  copilot.py — صورة أنحف، لا secrets في image.
- **`.gitignore`** moves: حُلّت conflicts ودُمج المحلّي مع remote.
- **`/admin/ibkr_ping`**: layered diagnostic (DNS → TCP → IB handshake)
  يكشف بالضبط أيّ طبقة فشلت. أعطى `Client disconnected before version
  was sent` الذي شخّص نهائياً مشكلة Railway private network.

---

## 10. الإحصائيّات النهائيّة

- **Commits رفعت:** ~20+
- **Modules جديدة:** 8 (`backtest_qc`, `kelly`, `stationarity`,
  `momentum_quality`, `cointegration`, `strategy_regime`,
  `paper_trade_gate`, `signals_advisor`, broker package, `usx_pro_filter`)
- **Tests جديدة:** 76+
- **Lines of code added:** ~3,500+
- **اللغات:** Python (production) + Pine Script (مرجع للتحويل)

---

## 11. الحالة الراهنة (نهاية 2026-04-28)

✅ **يعمل تشغيليّاً:**
- ثلاث استراتيجيّات A/B/C على Alpaca paper بـ ~$5,000 كل واحدة
- Bracket orders بـ GTC (لا "buys but never sells")
- Auto-trade مستمرّ، يتجمع بيانات للـ graduation
- Telegram filter `TELEGRAM_BUY_ONLY=true` — لا ضوضاء
- 5 مسوحات يوميّة من signals_advisor
- Endpoints: `/admin/scan_signals`, `/admin/alpaca_check`,
  `/admin/rearm_orphans`, `/admin/preflight`, `/admin/regime`

⏸️ **متوقّف بانتظار شروط:**
- IBKR integration (يحتاج VPS منفصل + تمويل ≥$25k)
- USX Pro V4 wiring (الـ module منشَأ، الربط لم يُكمَل)
- Strategy D (pairs trading) — بعد graduation

📅 **الخطوة التاليّة الفوريّة:**
- مراقبة A/B/C لـ 14 يوم
- 30 صفقة لكل استراتيجيّة كحد أدنى
- تشغيل preflight لرؤية أيّها يصل لـ DSR ≥ 0.60

---

*تمّ إنشاء هذا الملخّص بناءً على طلب المستخدم في 2026-04-28.*
