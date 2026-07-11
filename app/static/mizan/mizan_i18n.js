/* MIZAN terminal i18n — Arabic (default) ⇄ English.
 * A render-time translation layer: the whole terminal is authored in Arabic; when the user picks
 * EN we swap the document direction to LTR and translate matched text nodes / title / placeholder
 * attributes via the dictionary below. Unmatched strings stay Arabic (graceful — never mangled).
 * Toggle reloads the page for a clean state. Persisted in localStorage("mizan_lang"). */
(function () {
  "use strict";

  // ── Arabic → English dictionary (exact, trimmed match). Extend freely; misses fall back to AR.
  var TR = {
    // nav + shell
    "نظرة عامة": "Overview", "محفظة النواة": "Core Portfolio", "المسح": "Screener",
    "تحليل الأسهم": "Stock Analysis", "المحفظة": "Portfolio", "الدفتر الورقي": "Paper Ledger",
    "مختبر الاستراتيجية": "Strategy Lab", "العوامل": "Factors", "الموديلات": "Models",
    "السوق": "Market", "التقارير": "Reports", "التنبيهات": "Alerts", "الإعدادات": "Settings",
    "النظام يعمل": "System online", "تنبيهات المسح": "Scan alerts", "البروكر": "Broker",
    "متّصل · LIVE": "Connected · LIVE", "غير متّصل": "Disconnected", "ابحث عن رمز أو إسم…": "Search symbol or name…",
    "ابحث عن رمز أو اسم…": "Search symbol or name…", "بحث رمز…": "Search symbol…", "بحث رمز أو اسم…": "Search symbol or name…",
    "بحث زوج…": "Search pair…", "رمز السهم…": "Ticker…",
    // factor names
    "زخم 12-1": "12-1 Momentum", "قوّة نسبيّة (RS)": "Relative Strength (RS)", "شرط فوق EMA20": "Above EMA20",
    "التقلّب (ATR)": "Volatility (ATR)", "الامتداد عن EMA20": "Extension from EMA20", "قرب قمّة 52 أسبوعاً": "52-week-high proximity",
    "زخم البواقي": "Residual momentum", "زخم معدّل بالمخاطر": "Risk-adjusted momentum", "ثبات الزخم": "Momentum consistency",
    "التقلّب الهابط": "Downside volatility", "بيتا (مقابل SPY)": "Beta (vs SPY)", "أقصى تراجع 6ش": "Max drawdown 6m",
    "الموقع بالنطاق 20ي": "20d range position", "انعكاس 5 أيام": "5-day reversal", "أخرى": "Other",
    "الزخم": "Momentum", "القوّة النسبية": "Relative strength", "الامتداد": "Extension", "التذبذب": "Volatility",
    "المنهار": "Beaten-down", "انجراف الأرباح (PEAD)": "Earnings drift (PEAD)",
    "🔭 محرّك الاكتشاف — المتابعة (بحثيّ · ظلّيّ)": "🔭 Discovery engine — monitor (research · shadow)",
    "حالة الأبحاث الجديدة — تنضج أماميّاً": "New-research status — maturing forward",
    "حجم اللوحة (لقطات)": "Panel size (snapshots)", "تواريخ مُعنونة": "Labelled dates",
    "عائلات الإشارة": "Signal families", "سعر + أرباح": "Price + earnings", "وصفات في السباق": "Recipes in the race",
    "🆚 الترتيب مقابل السوق": "🆚 Rank vs the market", "…يقيس": "…measuring",
    "📊 عامل الأرباح (PEAD) — غير سعريّ": "📊 Earnings factor (PEAD) — non-price",
    "🏁 أقوى وصفة (المشروط بالنظام · 20ي)": "🏁 Strongest recipe (regime-conditional · 20d)", "…يحسب": "…computing",
    "🎰 دفتر المضاربة السريعة — الحلم مقاساً (ورقيّ 100%)": "🎰 Fast-speculation ledger — the dream, measured (100% paper)",
    "▶ شغّل دورة": "▶ Run a cycle", "العائد الأسبوعيّ المُركَّب": "Compounded weekly return", "الهدف (الحلم)": "Target (the dream)",
    "نسبة الفوز": "Win rate", "صفقات مغلقة": "Closed trades", "متوسّط الرابحة": "Avg winner", "متوسّط الخاسرة": "Avg loser",
    "لا مراكز مفتوحة الآن": "No open positions now", "…يحمّل دفتر المضاربة": "…loading the speculation ledger", "ساعات": "Hrs",
    // generic magnitudes / verdicts
    "عالٍ": "High", "متوسط": "Medium", "منخفض": "Low", "مرتفع": "High", "قيد التحقّق": "Validating",
    "موثوق": "Reliable", "قيد التعلّم": "Learning", "✓ متوافق": "✓ Compliant", "حلال": "Halal",
    "الحلال": "Halal", "متوافقة شرعاً": "Sharia-compliant", "حلال فقط · درجة ≥ 55": "Halal only · score ≥ 55",
    "غير متوافق": "Non-compliant", "شرعية متوافقة · AAOIFI ✓": "Sharia-compliant · AAOIFI ✓",
    "محجوبة شرعاً": "Sharia-blocked", "محجوب شرعاً": "Sharia-blocked",
    // overview
    "نظرة على السوق": "Market snapshot", "أفضل فرصة اليوم": "Top opportunity today",
    "القوى · IC (IC)": "Factor power · IC", "الأداء التراكمي للنموذج (الألفا)": "Cumulative model performance (alpha)",
    "خريطة العوامل (IC)": "Factor map (IC)", "السوق عبر الزمن (HMM)": "Market over time (HMM)",
    "هادئ (Risk-On)": "Calm (Risk-On)", "تذبذب": "Choppy", "أزمة (Risk-Off)": "Crisis (Risk-Off)",
    "تحلّل الألفا (Alpha Decay)": "Alpha decay", "آخر الإشارات القوية": "Latest strong signals",
    "ملخّص المحفظة الورقية": "Paper portfolio summary", "البنية التحتية": "Infrastructure",
    "الاستراتيجية": "Strategy", "البيانات": "Data", "تحديث العوامل": "Factor refresh", "اليوم": "Today",
    "نضج الدفتر + تدريب Meta": "Ledger maturation + Meta training", "تحقّق أسبوعي": "Weekly validation",
    "الاثنين": "Monday", "إعادة التوازن الشهري": "Monthly rebalance", "1 من الشهر": "1st of month",
    "ما القادم": "What's next", "صحّة النظام": "System health", "التعرّض للمخاطر": "Risk exposure",
    "الاستراتيجية الحالية": "Current strategy", "لا تغيير مطلوب": "No change needed", "راجع البوّابة": "Review the gate",
    // screener
    "درجة عالية": "High score", "الدرجة ≥ 70 · فرز بالدرجة": "Score ≥ 70 · sort by score", "زخم قويّ": "Strong momentum",
    "الدرجة ≥ 55 · فرز بالزخم": "Score ≥ 55 · sort by momentum", "قوّة أساسية": "Fundamental strength",
    "فرز بالأساسي": "Sort by fundamentals", "أزمة": "Crisis", "تقليدي": "Traditional", "هادئ صاعد": "Calm bull",
    "هادئ": "Calm", "صاعد": "Bullish", "محايد": "Neutral", "مُخاطِر": "Risk-on", "دفاعي": "Defensive",
    "كل الأسهم": "All stocks", "توصية شراء": "Buy signal", "⭐ المفضلة": "⭐ Favorites", "⚡ انفجار يومي": "⚡ Daily explosion",
    "🔗 الأزواج": "🔗 Pairs", "الدرجة الشاملة": "Composite score", "التوصية": "Signal", "السعر": "Price",
    "التغيّر اليومي": "Daily change", "القيمة السوقية": "Market cap", "القيمة السوقيّة": "Market cap",
    "السيولة ($م)": "Liquidity ($M)", "تقني /30": "Tech /30", "أساسي /25": "Fund /25", "ذكاء AI /15": "AI /15",
    "مشاعر /20": "Sentiment /20", "حلال /12": "Halal /12", "ATR % (مخاطرة)": "ATR % (risk)", "محلّلون": "Analysts",
    "نمو الإيرادات": "Revenue growth", "عائد:مخاطرة": "Reward:risk", "اسم الماسح المحفوظ:": "Saved screen name:",
    "تقني": "Tech", "أساسي": "Fundamentals", "ذكاء": "AI", "مشاعر": "Sentiment", "زخم": "Momentum",
    "إعادة جلب أحدث نتائج المسح": "Refetch latest scan results", "الكل": "All", "اشترِ X": "Buy X", "اشترِ Y": "Buy Y",
    "راقب": "Watch", "⚠ محرّك الإحصاء (statsmodels) غير متاح.": "⚠ Stats engine (statsmodels) unavailable.",
    "؟": "?", "· مضاعف الدفتر": "· ledger multiplier", "اختر 2–4 للمقارنة": "Pick 2–4 to compare", "ذكاء AI": "AI",
    "توزيع الدرجات": "Score distribution", "خريطة القطاعات (متوسط الدرجة)": "Sector map (avg score)",
    "خريطة الكون — المساحة = القيمة السوقيّة · اللون = الدرجة": "Universe map — area = market cap · color = score",
    "الماسحات المحفوظة": "Saved screens", "الفلاتر النشطة": "Active filters", "تصنيفات سريعة": "Quick rankings",
    "الأعلى درجة": "Highest score", "الأعلى زخم": "Highest momentum", "الأقوى أساساً": "Strongest fundamentals",
    "الأقوى تقنياً": "Strongest technicals", "أداء النتائج": "Results performance", "افتح التحليل": "Open analysis",
    "لا نتائج بعد للحساب": "No results to compute yet", "…يحسب أداء السلّة": "…computing basket performance",
    "إحصائيات الماسح": "Screener stats", "إجمالي النتائج": "Total results", "الظاهرة بعد الفلترة": "Shown after filtering",
    "متوسّط الدرجة": "Average score", "متوسّط الزخم": "Average momentum", "أفضل قطاع": "Best sector",
    "النتائج": "Results", "كل القطاعات": "All sectors", "السعر ≥": "Price ≥", "الدرجة ≥": "Score ≥", "فرز": "Sort",
    "الأساسي": "Fundamentals", "التقني": "Technicals", "★ حفظ الماسح": "★ Save screen",
    "🔗 أزواج متكاملة (تكامل مشترك داخل القطاع) · بحثيّ · طويل فقط": "🔗 Cointegrated pairs (within sector) · research · long-only",
    "…يقرأ الأزواج المُخزّنة": "…reading stored pairs",
    "…يمسح الأزواج في الخلفية (قد يستغرق ~دقيقتين — عُد بعد قليل)": "…scanning pairs in background (~2 min — check back)",
    "الزوج (Y / X)": "Pair (Y / X)", "النتيجة صارمة بحقّ — الماسح لا يُظهر إلا أزواجاً تصمد خارج العيّنة.": "A strict result — only pairs that hold out-of-sample are shown.",
    "⚡ ماسح انفجار لحظي · بحثيّ فقط · مستقلّ عن الحلال": "⚡ Momentum-explosion scanner · research only · halal-independent",
    "…يمسح الانفجارات": "…scanning explosions", "مكوّنات درجة الانفجار": "Explosion score components",
    "حجم نسبي": "Relative volume", "فجوة": "Gap", "توسّع الحجم": "Volume expansion",
    "ماسح تقنيّ للانفجار اللحظي (بحثيّ فقط) — ليس توصية ولا دخول دفتر ورقي.": "Technical explosion scanner (research only) — not a signal, no paper entry.",
    "لا انفجارات مؤكّدة الآن": "No confirmed explosions now", "مسح المقارنة": "Clear comparison",
    "☑ للمقارنة · الصفّ للتوسيع": "☑ to compare · row to expand", "🧭 النظام الآن:": "🧭 Regime now:",
    "لماذا؟": "Why?", "خطة الصفقة": "Trade plan", "دخول تقديري": "Est. entry", "وقف": "Stop", "هدف": "Target",
    "محلّلون:": "Analysts:", "مطّلعون:": "Insiders:", "صفقة ورقيّة": "Paper trade",
    "…يمسح الكون": "…scanning the universe",
    "كل مستطيل سهم — مساحته ∝ قيمته السوقيّة، لونه = درجته المركّبة (أخضر عالٍ · أصفر متوسّط · أحمر منخفض). انقر أيّ مستطيل للتحليل.": "Each tile is a stock — area ∝ market cap, color = composite score (green high · yellow mid · red low). Click a tile to analyze.",
    "الاسم": "Name", "الوصف": "Description", "▷ تطبيق": "▷ Apply", "ماسح محفوظ": "Saved screen",
    "لا فلاتر — الكون كامل": "No filters — full universe", "مقابل SPY": "vs SPY", "⬇ تصدير النتائج (CSV)": "⬇ Export results (CSV)",
    "✕ إغلاق": "✕ Close", "المقياس": "Metric", "بيانات حيّة من الماسح — للمقارنة والبحث، لا توصية.": "Live scan data — for comparison and research, not a signal.",
    "لا توصية": "not a signal",
    // factors
    "العوامل — معامل المعلومات عبر الآفاق": "Factors — Information Coefficient across horizons", "IC المشروط بالنظام": "Regime-conditional IC",
    "البوّابة ذاتية المعايرة": "Self-calibrating gate", "معتمَدة": "Approved", "افتراضية": "Default", "…يُعاير": "…calibrating",
    "سباق المركّبات المرشّحة — ظلّي/بحثيّ": "Candidate composite race — shadow/research", "IC 5ي": "IC 5d", "IC 10ي": "IC 10d",
    "IR 10ي": "IR 10d", "IC 20ي": "IC 20d", "IR 20ي": "IR 20d", "الخلاصة": "Verdict", "يتراكم عبر الأنظمة…": "Accumulating across regimes…",
    "التفاصيل ←": "Details →", "العتبة الحالية": "Current threshold",
    "فائض السلّة العليا (المهمّ للطويل) + IC · لا يمسّ التسجيل الحيّ": "Top-bucket excess (the long-only metric) + IC · never touches live scoring",
    "المركّب المرشّح": "Candidate composite", "فائض القمّة 5ي": "Top excess 5d", "فائض 10ي": "Excess 10d", "فوز٪": "Win %",
    "⚠ درس مقاس: النظام طويل فقط —": "⚠ Measured lesson: the system is long-only —", "يشتري القمّة": "buys the top bucket",
    "، فالمقياس الصحيح هو": ", so the right metric is", "فائض السلّة العليا": "top-bucket excess",
    "لا IP. المرشّح الأفضل بالـIC (زخم−EMA20) هو الأسوأ في القمّة. حاليّاً الزخم الخام و«زخم−امتداد» أفضل السلّة العليا —": "Not IC. The IC-best candidate (mom−EMA20) is the worst in the top bucket. Currently raw momentum and 'mom−extension' lead the top bucket —",
    "لكن لا شيء دالّ إحصائيّاً بعد": "but nothing is statistically significant yet",
    "…يحسب سباق المركّبات الظلّي": "…computing the shadow composite race", "العامل": "Factor", "القوّة": "Power", "الاتجاه": "Direction",
    "…يحمّل العوامل": "…loading factors", "أفضل عامل": "Best factor", "…لا بيانات للخريطة": "…no data for the map",
    "مؤكَّد": "Confirmed", "يحتاج وقتاً أطول": "Needs more time", "غير مؤكَّد بعد": "Not confirmed yet",
    // ledger / portfolio
    "المراكز المفتوحة (": "Open positions (", "أسبوعي": "Weekly", "شهري": "Monthly", "أزواج": "Pairs", "ظلّ": "Shadow",
    "متخرّج": "Graduated", "المراكز الورقية المفتوحة (": "Open paper positions (", "شراء": "Buy", "المغلقة (": "Closed (",
    "النقد": "Cash", "مراكز مفتوحة": "Open positions", "قوّة شرائية": "Buying power", "الكوكبيت ←": "Cockpit →",
    "الكمّية": "Qty", "القيمة": "Value", "ربح/خسارة": "P/L", "لا مراكز مفتوحة حاليّاً": "No open positions now",
    "الأسبوعي (PV)": "Weekly (PV)", "مفتوح": "Open", "الشهري (PVM)": "Monthly (PVM)", "إجمالي المفتوحة": "Total open",
    "إجمالي المغلقة": "Total closed", "الجانب": "Side", "الدخول": "Entry", "الثقة": "Confidence", "لا مراكز مفتوحة": "No open positions",
    "الخروج": "Exit", "العائد": "Return", "لا صفقات مغلقة بعد": "No closed trades yet", "قيمة المحفظة": "Portfolio value",
    "ربح/خسارة اليوم": "Today's P/L", "نوع الحساب": "Account type", "ورقي": "Paper", "ألفا أسبوعي (t)": "Weekly alpha (t)",
    "ألفا تراكمي": "Cumulative alpha", "حارس فرط التخصيص (PBO)": "Overfit guard (PBO)", "عتبة الدخول MIN_RS": "Entry threshold MIN_RS",
    "من الدفتر المُغلق": "from the closed ledger", "ألفا الاختيار التراكمي عبر": "Cumulative selection alpha over",
    "صفقة مغلقة (عائد الصفقة − SPY).": "closed trades (trade return − SPY).", "يتراكم — يظهر المنحنى بعد صفقات مغلقة كافية.": "Accumulating — the curve appears after enough closed trades.",
    "الحالة:": "Status:", "الألفا على 20 يوماً ≈": "Alpha at 20 days ≈", "من قيمتها على 5 أيام (زخم 12-1).": "of its 5-day value (12-1 momentum).",
    "…يتراكم": "…accumulating", "الكل ←": "All →", "الرمز": "Symbol", "الإشارة": "Signal", "الدرجة": "Score", "…يحمّل": "…loading",
    "مفتوحة": "Open", "إجمالي القيمة": "Total value", "العائد اليومي": "Daily return", "آخر فحص: منذ دقائق": "Last check: minutes ago",
    "إجمالي المخاطرة": "Total risk", "VaR (95%) يومي": "VaR (95%) daily", "مضاعِف الدفتر": "Ledger multiplier",
    "VaR بارامتري يومي (القيمة × تقلّب SPY × 1.645).": "Parametric daily VaR (value × SPY vol × 1.645).", "نشطة": "Active",
    // market / models
    "المؤشّرات الحيّة": "Live indicators", "أخبار السوق": "Market news", "نموذج Meta-labeling": "Meta-labeling model",
    "موثوق ✓": "Reliable ✓", "دون عتبة 0.53": "Below 0.53 threshold", "لوحة الموديلات (Leaderboard)": "Models leaderboard",
    "النظام": "Regime", "التضخّم (CPI)": "Inflation (CPI)", "الفائدة": "Rates", "البطالة": "Unemployment",
    "AUC خارج العيّنة": "Out-of-sample AUC", "AUC داخل العيّنة": "In-sample AUC", "العيّنات": "Samples", "المعدّل الأساسي": "Base rate",
    "تتراكم مع تسجيل أداء الاستراتيجيات — لا توجد موديلات مُقيَّمة بعد.": "Accumulates as strategy performance is recorded — no scored models yet.",
    "مؤشّرات حيّة": "Live indicators",
    // settings
    "وزن الزخم 12-1 (الأقوى تاريخياً)": "12-1 momentum weight (historically strongest)", "وزن القوّة النسبية RS": "RS weight",
    "وزن المشاعر/التحليلات": "Sentiment/analyst weight", "حسّاسية دلالة توصية البوّابة": "Gate recommendation significance", "عتبة الدخول الأسبوعي": "Weekly entry threshold",
    "تقلّب المحفظة المستهدف": "Target portfolio volatility", "عتبة ثقة نموذج Meta": "Meta model confidence threshold",
    "إعادة عتبة الدخول إلى الافتراضي (−2)؟ ورقي فقط · قابل للعكس.": "Reset the entry threshold to default (−2)? Paper only · reversible.",
    "تمّت الإعادة → MIN_RS": "Reset done → MIN_RS", "تعذّر": "Failed",
    "تطبيق أوزان العوامل الجديدة على تسجيل الماسح؟ يؤثّر في ترتيب الأسهم — ورقي فقط · قابل للعكس بالكامل.": "Apply the new factor weights to screener scoring? Affects ranking — paper only · fully reversible.",
    "…يطبّق": "…applying", "خطأ:": "Error:", "تمّ التطبيق ✓ — يظهر الأثر في المسح التالي.": "Applied ✓ — the effect shows in the next scan.",
    "إعادة كل الأوزان إلى الافتراضي؟": "Reset all weights to default?", "…يعيد": "…resetting", "أُعيدت إلى الافتراضي ✓": "Reset to default ✓",
    "عتبة الدخول (البوّابة)": "Entry threshold (the gate)", "معتمَدة بالدليل": "Evidence-approved",
    "أوزان العوامل — المركّب (ورقي · قابل للعكس)": "Factor weights — composite (paper · reversible)", "معدّلة": "Modified", "خطأ": "Error",
    "مفاتيح الضبط (env · قياس فقط · قابلة للعكس)": "Tuning knobs (env · measurement only · reversible)",
    "MIN_RS الحالية": "Current MIN_RS", "إعادة إلى الافتراضي (−2)": "Reset to default (−2)", "تطبيق": "Apply", "إعادة للافتراضي": "Reset to default",
    "تُغيّر هذه الأوزان ترتيب المركّب في الماسح فقط (لا صفقات حقيقية). محفوظة على القرص وتُعاد بالكامل بزرّ الإعادة.": "These weights only change composite ranking in the screener (no real trades). Saved to disk and fully reset by the reset button.",
    "…يحمّل الأوزان": "…loading weights", "المفتاح": "Key",
    "التعديل عبر متغيّرات البيئة على الخادم — كلّها آمنة، ورقية، وقابلة للعكس. لا صفقات حقيقية.": "Edited via server env vars — all safe, paper, and reversible. No real trades.",
    // core portfolio + ledgers + criteria
    "العائد السنويّ (CAGR)": "Annual return (CAGR)", "أقصى تراجع تاريخيّ": "Max historical drawdown", "نسبة العائد/التراجع": "Return/drawdown ratio",
    "أداء هبوط 2022": "2022 bear performance", "🩹 حاسبة ميزانيّة الألم": "🩹 Pain-budget calculator", "📋 مولّد أوامر السلّة": "📋 Basket order generator",
    "🛑 بطاقة القواطع المكتوبة — تُقرّها قبل أوّل أمر حقيقيّ": "🛑 Written circuit-breaker card — approve before the first real order",
    "مُقرّة ✓": "Approved ✓", "لم تُقرّ بعد": "Not approved yet", "من رأس مالك التجريبيّ": "of your experimental capital",
    "(أدخِل رأس المال التجريبيّ في ③)": "(enter experimental capital in ③)", "تحديث الإقرار": "Update approval", "أُقرّ هذه القواطع": "Approve these breakers",
    "📓 دفتر النواة الورقيّ — مرآة موازية لقياس انزلاقك": "📓 Paper core ledger — a parallel mirror to measure your slippage",
    "↻ حدّث الدفتر": "↻ Refresh ledger", "▶ افتح الدفتر": "▶ Open ledger", "منذ": "since",
    "🌙 قمر الزخم — تتبّع أماميّ ظلّي (غير منشور حيّاً)": "🌙 Momentum satellite — forward shadow tracking (not deployed live)",
    "🌙 قمر الزخم — تتبّع أماميّ ظلّيّ (غير منشور حيّاً)": "🌙 Momentum satellite — forward shadow tracking (not deployed live)",
    "↻ حدّث القمر": "↻ Refresh satellite", "▶ افتح القمر": "▶ Open satellite", "↻ حدّث المستكشف": "↻ Refresh explorer", "▶ افتح المستكشف": "▶ Open explorer",
    "🚀 المستكشف — صيّاد الذيل (ظلّيّ، رهان يانصيب)": "🚀 The Explorer — tail hunter (shadow, lottery bet)",
    "🚀 المستكشف — صيّاد الذيل (ظلّي، رهان يانصيب)": "🚀 The Explorer — tail hunter (shadow, lottery bet)",
    "🏁 سباق المحرّكات الثلاثة — المنحنى الأماميّ": "🏁 Three-engine race — the forward curve",
    "🏁 سباق النواة ضدّ القمر — المنحنى الأماميّ": "🏁 Core vs satellite race — the forward curve",
    "يوم مُسجَّل": "days logged", "📜 معايير التخرّج — مقفلة مسبقاً (قبل البيانات)": "📜 Graduation criteria — pre-registered (before the data)",
    "مقفلة ✓": "Locked ✓", "غير مقفلة": "Not locked", "تحديث القفل": "Update lock", "أقفل المعايير": "Lock criteria",
    "مضى": "Elapsed", "يوم من أصل": "days of", "قبل الحكم.": "before the verdict.", "قيد المراقبة": "Watching", "تخرّج ✓": "Graduated ✓",
    "يُوصى بالأرشفة": "Archive recommended", "اقفل المعايير أوّلاً": "Lock the criteria first", "لا بيانات بعد": "No data yet",
    "رأس المال ($)": "Capital ($)", "أقصى خسارة تتحمّلها (%)": "Max loss you can bear (%)", "التعرّض المقترح": "Suggested exposure",
    "المبلغ المستثمَر": "Amount invested", "يبقى نقداً": "Stays in cash", "أسوأ خسارة متوقّعة تاريخيّاً": "Worst historical expected loss",
    "⬇ تصدير CSV": "⬇ Export CSV", "عدد الأسهم": "Number of names", "لكل سهم (~)": "Per name (~)", "إجمالي المستثمَر فعليّاً": "Total actually invested",
    "…يحمّل السلّة الحلال (أدخِل رأس مالاً كافياً)": "…loading the halal basket (enter enough capital)", "قائمة تنفّذها": "A list you execute",
    "يدويّاً من IBKR": "manually from IBKR", "الكون الحلال الكامل": "the full halal universe", "أنت": "you", "النسبة %": "Percent %",
    "خط التوقّف ≈": "Stop line ≈", "② انحرافك عن السلّة ← تنبيه انضباط": "② Your drift from the basket ← discipline alert", "الحدّ %": "Limit %",
    "تجاوزُه = أعِد التوازن نحو السلّة (لا تطارد اسماً)": "Exceeding it = rebalance toward the basket (don't chase a name)",
    "③ رأس المال التجريبيّ — ما تستطيع خسارته كاملاً": "③ Experimental capital — what you can lose entirely",
    "ابدأ صغيراً — مبلغٌ خسارتُه كاملاً لا تؤلمك": "Start small — an amount whose total loss won't hurt", "④ جرس «أزمة» النظام (HMM)": "④ System 'crisis' bell (HMM)",
    "معلوماتيّ فقط": "Informational only", "① خسارة تراكميّة قصوى ← توقّف ومراجعة": "① Max cumulative loss ← stop & review",
    "إلغاء الإقرار": "Revoke approval", "…يحمّل البطاقة": "…loading the card", "عائد غير محقّق (بأسعار آخر مسح)": "Unrealized return (latest scan prices)",
    "عدد المراكز": "Positions", "آخر إعادة توازن": "Last rebalance", "الحاليّ": "Current", "غير محقّق %": "Unrealized %",
    "النظام يتداول النواة بالتساوي": "The system trades the core equal-weight", "ورقيّاً": "on paper", "انزلاقك الشخصيّ": "your personal slippage",
    "…يحمّل الدفتر": "…loading the ledger", "الفرضيّة الوحيدة التي عبرت آلة الزمن بعد التكاليف": "The only hypothesis that passed the time machine after costs",
    "+4.6%/سنة": "+4.6%/yr", "فوق النواة — لكن بتراجع أسوأ (": "over the core — but at a worse drawdown (", "لا يُنشَر حيّاً": "not deployed live",
    "؛ هذا الدفتر يجمع دليلاً": "; this ledger gathers evidence", "أماميّاً": "forward", "عائد غير محقّق (أماميّ)": "Unrealized return (forward)",
    "…يحمّل القمر": "…loading the satellite", "🔬 من": "🔬 From", "تشريح الصاعدين": "the Winner Autopsy", "تقلّب عالٍ + بُعد عن قمّة 52 أسبوعاً": "high volatility + far below the 52-week high",
    "مسمومة بانحياز البقاء": "poisoned by survivorship bias", "سلّة يانصيب صغيرة — تتوقّع فشل معظمها": "a small lottery basket — expect most to fail",
    "…يحمّل المستكشف": "…loading the explorer", "آليّاً": "mechanical", "① مدّة الانتظار (عدد إعادات التوازن)": "① Wait period (number of rebalances)",
    "إعادات": "rebalances", "② أدنى تفوّق على النواة (ألفا تراكميّة %)": "② Minimum edge over the core (cumulative alpha %)", "دون هذا = لا قيمة مضافة": "below this = no added value",
    "③ أقصى تراجع إضافيّ مسموح فوق النواة %": "③ Max extra drawdown allowed over the core %", "تراجع أسوأ من النواة بأكثر منه = يسقط": "a drawdown worse than the core by more = it fails",
    "المحرّك": "Engine", "ألفا %": "Alpha %", "تراجع أسوأ من النواة": "Drawdown worse than core", "الحكم الآليّ": "Mechanical verdict",
    "يبدأ رسم السباق بعد يومين من التسجيل (لقطة تلقائيّة كلّ يوم عند الإغلاق).": "The race curve starts after two logged days (an automatic snapshot each day at close).",
    "بيتا حلال": "halal beta",
    // lab
    "مختبر الفهم": "Understanding Lab", "🔬 المختبر المتقدّم ←": "🔬 Advanced lab →", "حالة اليوم:": "Today's status:", "— السوق": "— market",
    "لقطة في الذاكرة": "snapshots in memory", "· عتبة الدخول": "· entry threshold", "· ⚠ قرار ينتظرك (اطّلع أدناه)": "· ⚠ a decision awaits you (see below)",
    "· لا قرارات عاجلة": "· no urgent decisions", "كيف يعمل النظام؟ — حلقة تتكرّر كل يوم": "How does the system work? — a loop that repeats daily",
    "هل النظام يربح؟ — مقارنةً بشراء السوق فقط (SPY)": "Is the system winning? — vs simply buying the market (SPY)", "مقابل السوق": "vs the market",
    "من": "of", "صفقة": "trades", "تفوّقت على السوق": "beat the market", "ثقة القياس:": "Measurement confidence:", "…يقيس أداء الاستراتيجيات": "…measuring strategy performance",
    "⏱️ آلة الزمن — لو تداولت الوصفة منذ 2022 (أعلى-5 أسبوعيّاً · بعد التكاليف)": "⏱️ Time machine — if you had traded the recipe since 2022 (top-5 weekly · after costs)",
    "العائد الكلّي": "Total return", "أقصى تراجع": "Max drawdown", "مقابل الكون": "vs the universe", "📊 الكون بالتساوي (المعيار)": "📊 Equal-weight universe (benchmark)",
    "…تُشغّل آلة الزمن (محاكاة 4 سنوات على اللوحة)": "…running the time machine (4-year panel simulation)", "ماذا تعلّم النظام؟ — اكتشافاته بلغة بسيطة": "What did the system learn? — its findings in plain language",
    "مدى التأكّد:": "Certainty:", "…يستخلص الاكتشافات": "…extracting findings", "كيف يصلّح نفسه؟ — قرارات تنتظرك + سجلّ التصحيح": "How does it fix itself? — pending decisions + correction log",
    "مراجعة في الإعدادات ←": "Review in settings →", "أثبتت جودتها": "proved its quality", "و": "and", "راجع الأوزان ←": "Review the weights →",
    "سباق الوصفات ←": "Recipe race →", "عتبة الدخول:": "Entry threshold:", "أثبتت تفوّقاً — جاهزة لمراجعتك.": "Proved an edge — ready for your review.",
    "لكن الدلالة لم تنضج بعد — القياس مستمرّ.": "But significance hasn't matured yet — measurement continues.", "📥 قرارات تنتظرك": "📥 Decisions awaiting you",
    "القياس يقترح:": "The measurement suggests:", "مراجعة العتبة": "review the threshold", "القياس لا يقترح تغييراً الآن.": "The measurement suggests no change now.",
    "· نافذة أخيرة": "· recent window", "قيد المراقبة:": "Watching:", "📖 دفتر التصحيح — ماذا غيّر النظام ولماذا": "📖 Correction log — what the system changed and why",
    "يَجمع": "Collect", "يلتقط صورة للسوق كل يوم ويخزّنها ليتعلّم منها لاحقاً.": "Takes a picture of the market each day and stores it to learn from later.",
    "لقطة مُجمَّعة · تنمو يوميّاً": "snapshots collected · growing daily", "يُحلّل": "Analyze", "يقيس أيّ إشارة تنبّأت فعلاً بالربح — لا بالرأي، بل بالأرقام.": "Measures which signal actually predicted profit — not by opinion, by numbers.",
    "لقطة مُقاسة بنتيجتها": "snapshots labelled by outcome", "يتعلّم": "Learn", "اكتشف أن «": "Discovered that '", "» أفضل إشارة، و«": "' is the best signal, and '",
    "» تؤذي.": "' hurts.", "يستخلص أيّ الإشارات تنفع وأيّها تضرّ.": "Extracts which signals help and which hurt.", "أقوى إشارة مُكتشَفة": "strongest signal found",
    "يُصلّح": "Correct", "يجرّب وصفات أفضل في الظلّ، ولا يغيّر شيئاً حيّاً إلا بموافقتك.": "Tests better recipes in the shadow, and changes nothing live without your approval.",
    "وصفات تُختبَر الآن": "recipes under test now", "هادئ/محايد": "calm/neutral", "» أفضل إشارة": "' best signal",
    "الأسهم القويّة بهذه الإشارة تميل للاستمرار في الربح.": "Stocks strong on this signal tend to keep winning.", "» تؤذي": "' hurts",
    "الاعتماد عليها يجعل النظام يشتري في الوقت الخطأ (سعر ممتدّ).": "Relying on it makes the system buy at the wrong time (an extended price).",
    "نجرّب وصفات جديدة": "we test new recipes", "أفضلها للشراء: «": "best to buy: '", "أثبتت تفوّقاً — جاهزة لمراجعتك.": "Proved an edge — ready for your review.",
    "لا وصفة ناضجة بعد — القياس مستمرّ. النظام يحلّل ويقترح تلقائيّاً، لكنه لا يطبّق شيئاً على التسجيل الحيّ إلا بموافقتك.": "No mature recipe yet — measurement continues. The system analyzes and proposes automatically, but applies nothing to live scoring without your approval.",
    "— كيف يعمل نظامك ونتائجه، بلغة بسيطة": "— how your system works and its results, in plain language",
    "لم يُجرِ النظام تغييراً معتمَداً بعد — كل تعديل ينتظر قرارك. هذا مقصود: القياس يقترح، وأنت تقرّر.": "The system has made no approved change yet — every edit awaits your decision. This is intentional: the measurement proposes, you decide.",
    // analysis
    "أمر ورقي على IBKR paper — بتأكيدك": "Paper order on IBKR paper — with your confirmation", "غير متاح: الخطّة ناقصة أو غير متوافقة شرعاً": "Unavailable: the plan is incomplete or non-compliant",
    "الخطّة": "The plan", "درجة شاملة": "Composite score", "العائد المتوقّع": "Expected return", "مدّة الاحتفاظ": "Hold period", "مستوى المخاطرة": "Risk level",
    "وقف كارثي": "Catastrophe stop", "التوقّعات": "Forecast", "المخاطر": "Risks", "هابط": "Down", "قويّ": "Strong", "ضعيف": "Weak", "فوق": "Above", "تحت": "Below",
    "نطاق القمّة": "Range top", "تقييم المحلّلين": "Analyst rating", "بيع": "Sell", "احتفاظ": "Hold", "النقاط الرئيسية": "Key points",
    "مسار التوقّع — مونت-كارلو (٣٠ يوم · ٣٠٠ محاكاة · GBM)": "Forecast path — Monte Carlo (30 days · 300 sims · GBM)", "السعر المتوقّع (٣٠ي)": "Expected price (30d)",
    "احتمال الربح": "Win probability", "الانجراف السنوي": "Annual drift", "التقلّب السنوي": "Annual volatility", "VaR ٩٥٪ (طرفي)": "VaR 95% (tail)",
    "VaR ٩٥٪ (٣٠ي)": "VaR 95% (30d)", "CVaR ٩٥٪ (٣٠ي)": "CVaR 95% (30d)", "تحليل ما بعد الوفاة (Pre-mortem) — لماذا قد تفشل الصفقة": "Pre-mortem — why the trade might fail",
    "نموذج لغوي": "LLM", "تفاصيل السهم": "Stock details", "السعر الحالي": "Current price", "أعلى (النطاق)": "High (range)", "أدنى (النطاق)": "Low (range)",
    "متوسّط الحجم": "Avg volume", "الأرباح القادمة": "Upcoming earnings", "التقييم الأساسي": "Fundamental valuation", "هامش إجمالي": "Gross margin",
    "دين/حقوق": "Debt/equity", "FCF/سهم": "FCF/share", "درجة الأساسيات": "Fundamentals score", "الوسوم": "Tags", "الآن": "now", "قبل": "ago",
    "قواعد التنبيه": "Alert rules", "التنبيهات الأخيرة": "Recent alerts", "حالة السوق": "Market status", "ثقة النظام": "Regime confidence", "من HMM": "from HMM",
    "ألفا الأسبوعي": "Weekly alpha", "حالة الدفتر الورقي": "Paper ledger status", "الموديل (Meta)": "Model (Meta)", "فتح التفاصيل": "Open details",
    "صفقة ورقية": "Paper trade", "تحليل متقدّم": "Advanced analysis", "الخصائص المتوقّعة": "Predicted properties", "احتمال النجاح": "Success probability",
    "القطاع": "Sector", "لا فرص مؤكّدة الآن": "No confirmed opportunities now", "المزيد ←": "More →", "راجع الخطّة أدناه ثم اضغط «صفقة ورقية».": "Review the plan below then press 'Paper trade'.",
    "الخطّة غير مكتملة أو غير متوافقة شرعاً — لا يمكن الإرسال.": "The plan is incomplete or non-compliant — cannot submit.", "…يُرسل الأمر إلى الوسيط الورقي": "…sending the order to the paper broker",
    "لم تُرسَل:": "Not sent:", "الوسيط الورقي غير متّصل": "Paper broker not connected", "خطأ لدى الوسيط": "Broker error", "سبب غير معروف": "Unknown reason",
    "تعذّر الإرسال — تحقّق من اتّصال الوسيط الورقي.": "Send failed — check the paper broker connection.", "نمو قوي": "Strong growth", "ربحية عالية": "High profitability",
    "اتجاه قويّ": "Strong trend", "مخاطرة": "Risk", "عالية": "high", "متوسطة": "medium", "محلّل": "analysts", "التقييم:": "Rating:", "لا تغطية تحليلية": "No analyst coverage",
    "النطاق ٢٥–٧٥٪": "25–75% band", "النطاق ٥–٩٥٪": "5–95% band", "الوسيط": "Median",
    "نموذج GBM ثابت (انجراف/تقلّب مقدّران من التاريخ) — تصوّر للمخاطر لا توصية. الانجراف قد يُقلَّص عبر MC_DRIFT_SHRINK للمعايرة.": "Static GBM model (drift/vol estimated from history) — a risk visualization, not a signal. Drift may be shrunk via MC_DRIFT_SHRINK for calibration.",
    "لا مخاطر بارزة مُحدّدة": "No notable risks identified", "تُقيَّم مع كل مسح · قياسيّة فقط": "Evaluated each scan · measurement only",
    "لا قواعد — أضِف قاعدة أدناه.": "No rules — add one below.", "+ سهم جديد «شراء قوي»": "+ New 'Strong Buy' stock", "درجة ≥": "Score ≥", "+ أضِف": "+ Add",
    "ينطلق التنبيه مرّة حين يَدخل سهم الشرط (لا يتكرّر كل مسح). لا صفقات — إشعار فقط.": "The alert fires once when a stock meets the condition (not every scan). No trades — notification only.",
    "الرئيسية /": "Home /", "🖨️ تصدير PDF (طباعة)": "🖨️ Export PDF (print)",
    "تقرير ورقي/قياسي — لا صفقات حقيقية. زرّ التصدير يفتح حوار الطباعة (احفظ كـ PDF).": "Paper/measurement report — no real trades. Export opens the print dialog (save as PDF).",
    "📊 مختبر الاستراتيجية — العوامل والباك-تيست": "📊 Strategy Lab — factors and backtest", "📈 المحفظة — الأداء والمراكز": "📈 Portfolio — performance and positions",
    "🧮 العوامل — IC متعدّد الآفاق": "🧮 Factors — multi-horizon IC", "…يحمّل المخطّط": "…loading the chart", "…يحسب مسار التوقّع (مونت-كارلو)": "…computing the forecast path (Monte Carlo)",
    "الرئيسية / تحليل الأسهم /": "Home / Stock Analysis /", "تحليل": "Analysis", "💼 صفقة ورقيّة": "💼 Paper trade", "ملخّص أداء MIZAN —": "MIZAN performance summary —",
    "تقارير تفصيلية": "Detailed reports", "لا صفقات": "no trades", "قياس فقط": "measurement only"
  };

  // dynamic patterns (whole-node): [regex, replacer]
  var PAT = [
    [/^منذ (\d+) يوم$/, function (m, n) { return n + "d ago"; }],
    [/^(\d[\d,]*) اسماً$/, function (m, n) { return n + " names"; }],
    [/^(\d+) يوم مُسجَّل$/, function (m, n) { return n + " days logged"; }],
    [/^مضى (\d+) يوم من أصل (\d+) قبل الحكم\.?$/, function (m, a, b) { return a + " of " + b + " days elapsed before the verdict."; }],
    [/^أقررتَها في (.+)$/, function (m, d) { return "Approved on " + d; }],
    [/^قُفِلت في (.+)$/, function (m, d) { return "Locked on " + d; }]
  ];

  function tr(s) {
    if (!s) return null;
    var k = s.trim();
    if (!k) return null;
    if (Object.prototype.hasOwnProperty.call(TR, k)) return s.replace(k, TR[k]);
    for (var i = 0; i < PAT.length; i++) {
      var mm = k.match(PAT[i][0]);
      if (mm) return s.replace(k, PAT[i][1].apply(null, mm));
    }
    return null;
  }

  function lang() { try { return localStorage.getItem("mizan_lang") || "ar"; } catch (e) { return "ar"; } }

  function initDir() {
    var l = lang();
    document.documentElement.dir = l === "en" ? "ltr" : "rtl";
    document.documentElement.lang = l;
  }

  var observer = null, pending = null;

  function apply() {
    if (lang() !== "en") return;
    var root = document.getElementById("root");
    if (!root) return;
    if (observer) observer.disconnect();
    try {
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
      var nd;
      while ((nd = walker.nextNode())) {
        var en = tr(nd.nodeValue);
        if (en != null && en !== nd.nodeValue) nd.nodeValue = en;
      }
      var els = root.querySelectorAll("[placeholder],[title]");
      for (var i = 0; i < els.length; i++) {
        ["placeholder", "title"].forEach(function (a) {
          var v = els[i].getAttribute(a);
          if (v) { var e = tr(v); if (e != null && e !== v) els[i].setAttribute(a, e); }
        });
      }
    } catch (e) { /* never break the app over a translation pass */ }
    if (observer) observer.observe(root, { childList: true, subtree: true, characterData: true });
  }

  function schedule() { clearTimeout(pending); pending = setTimeout(apply, 60); }

  window.__mizanToggleLang = function () {
    try { localStorage.setItem("mizan_lang", lang() === "en" ? "ar" : "en"); } catch (e) {}
    location.reload();
  };
  window.__mizanLang = lang;

  initDir();
  function boot() {
    var root = document.getElementById("root");
    if (!root) { setTimeout(boot, 100); return; }
    observer = new MutationObserver(schedule);
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    apply();
    setInterval(apply, 2000);   // catch React re-renders that reset text to Arabic
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
