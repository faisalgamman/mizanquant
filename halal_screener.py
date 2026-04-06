import asyncio, time, logging, os, uuid, threading, re, gc, pandas as pd, numpy as np
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# --- New modular imports (Phase 1 restructuring) ---
from app.config import settings
from app.services.market_data import fetch as fetch_market_data, fetch_alpaca_intraday
from app.services.technical import (
    ema, rsi, macd, atr, calc_metrics,
    safe_scale, walk_forward_split, prepare_sequences, get_score,
)
from app.exceptions import DataFetchError, ModelTrainingError
from app.db.database import init_db
from app.background.cache_manager import record_signal, db_cache_get, db_cache_set
from app.services.halal_screening import (
    get_halal_status, get_screening_report, batch_screen, screen_symbol,
)
from app.services.alpaca_client import (
    get_account as alpaca_get_account,
    get_positions as alpaca_get_positions,
    get_orders as alpaca_get_orders,
    get_portfolio_history as alpaca_get_portfolio_history,
)
from app.services.telegram_alert import (
    alert_strong_signal, alert_consensus, alert_daily_summary,
    alert_system_health, alert_signal_with_chart,
    send_message as tg_send, send_photo as tg_send_photo,
)
from app.services.signal_tracker import (
    check_signal_outcomes, get_accuracy_report, get_signal_history,
)
from app.services.trading_engine import (
    on_signal as auto_trade_signal,
    execute_buy, execute_sell,
    get_trade_history, get_performance_report,
)
from app.services.risk_manager import get_risk_status

# Limit PyTorch/OpenMP threads to prevent memory bloat on small containers
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
# PyTorch removed for memory efficiency on Railway free tier.
# ML consensus replaced with lightweight technical analysis tools.
# Re-add when upgrading to Railway plan with >= 1GB RAM.

# Semaphore: only one model trains at a time to prevent OOM
_model_semaphore = threading.Semaphore(1)

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL, logging.INFO))
logger = logging.getLogger("screener")
app = FastAPI()

import keep_alive
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- Request logging middleware (debug OpenBB params) ---
from starlette.middleware.base import BaseHTTPMiddleware

class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if "/egx/" in str(request.url.path):
            logger.warning(f"[EGX REQUEST] {request.method} {request.url} | path={request.url.path} | query={request.url.query} | params={dict(request.query_params)}")
        response = await call_next(request)
        return response

app.add_middleware(RequestLoggerMiddleware)

# --- EGX module (completely separate from US stocks) ---
from app.egx.routes import router as egx_router
app.include_router(egx_router)

# --- 4.1: Timeout wrapper ---
async def with_timeout(coro, seconds=120):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Request timed out after {seconds}s")

# --- 4.2 + 4.3: Input validation helpers ---
VALID_SYMBOLS = set()  # populated after HALAL_STOCKS is defined

def validate_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if not re.match(r'^[A-Z]{1,6}$', s):
        raise HTTPException(status_code=400, detail=f"Invalid symbol format: {symbol}")
    return s

def validate_date(d: str) -> str:
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
        raise HTTPException(status_code=400, detail=f"Invalid date format: {d}. Use YYYY-MM-DD")
    return d

def validate_range(val, name, min_v, max_v):
    if val < min_v or val > max_v:
        raise HTTPException(status_code=400, detail=f"{name} must be between {min_v} and {max_v}, got {val}")
    return val

# --- 4.5: Simple rate limiter ---
_rate_limits = defaultdict(list)
_rate_lock = threading.Lock()

def check_rate_limit(endpoint: str, max_concurrent: int = 2):
    """Soft rate limiter — returns True if OK, False if over limit.
    Never raises HTTPException so OpenBB Pro widgets don't break."""
    with _rate_lock:
        _rate_limits[endpoint] = [t for t in _rate_limits[endpoint] if time.time() - t < 60]
        if len(_rate_limits[endpoint]) >= max_concurrent:
            return False  # caller decides what to do
        _rate_limits[endpoint].append(time.time())
        return True

# S&P 500 universe — excluding obvious haram sectors:
# Banks/financials (interest-based), insurance, alcohol, gambling, tobacco, weapons
# Each stock still goes through AAOIFI screening for final halal verification
_HARAM_EXCLUDE = {
    # Major banks & financial services (interest-based income)
    "BAC","C","COF","CFG","FITB","GS","HBAN","JPM","KEY","MS","MTB",
    "PNC","RF","SCHW","STT","TFC","USB","WFC","BK","BEN","BLK","BX",
    "CBOE","CME","ICE","IVZ","KKR","MSCI","NDAQ","SPGI","TROW",
    # Insurance
    "ACGL","AFL","AIG","AIZ","ALL","AJG","BRO","CB","CI","CINF",
    "CNC","COR","EG","ELV","ERIE","GL","HIG","HUM","L","MET",
    "PFG","PGR","PRU","RJF","TRV","UNH","WRB","WTW",
    # Alcohol & tobacco
    "BF.B","STZ","TAP","MO","PM","SAM",
    # Gambling & casinos
    "WYNN","LVS","MGM","CCL","NCLH","RCL",
    # Weapons/defense (controversial — kept some dual-use industrials)
    "LMT","NOC","GD","RTX","HII","LHX","BA",
    # Conventional media with haram content
    "FOX","FOXA","NFLX","DIS","WBD","LYV",
    # Utilities — almost all fail AAOIFI debt screen (>33% debt/market cap)
    "AEE","AEP","AES","ATO","CEG","CMS","CNP","D","DTE","DUK","ED",
    "EIX","ES","ETR","EVRG","EXC","FE","LNT","NEE","NI","NRG","PCG",
    "PEG","PPL","SO","SRE","VST","WEC","XEL",
    # REITs — interest-based income structure, high leverage
    "AMT","ARE","AVB","BXP","CCI","CPT","DLR","DOC","EQIX","EQR",
    "ESS","EXR","FRT","HST","INVH","IRM","KIM","MAA","O","PLD",
    "PSA","REG","SBAC","SPG","UDR","VICI","VTR","WELL",
    # Payment processors / fintech — significant interest income from credit
    "AXP","SYF","CPAY",
}

# Full S&P 500 minus haram exclusions
_SP500_ALL = [
    "A","AAPL","ABBV","ABNB","ABT","ACN","ADBE","ADI","ADM","ADP","ADSK",
    "AEE","AEP","AES","AKAM","ALB","ALGN","ALLE","AMAT","AMCR","AMD",
    "AME","AMGN","AMP","AMT","AMZN","ANET","AON","AOS","APA","APD","APH",
    "APO","APP","APTV","ARE","ARES","ATO","AVB","AVGO","AVY","AWK","AXON",
    "AXP","AZO","BALL","BAX","BBY","BDX","BG","BIIB","BKNG","BKR","BLDR",
    "BMY","BR","BRK.B","BSX","BXP","CAG","CAH","CARR","CAT","CBRE","CCI",
    "CDNS","CDW","CEG","CF","CHD","CHRW","CHTR","CIEN","CL","CLX","CMCSA",
    "CMG","CMI","CMS","CNP","COHR","COIN","COO","COP","COST","CPAY","CPB",
    "CPRT","CPT","CRH","CRL","CRM","CRWD","CSCO","CSGP","CSX","CTAS",
    "CTRA","CTSH","CTVA","CVNA","CVS","CVX","D","DAL","DASH","DD","DDOG",
    "DE","DECK","DELL","DG","DGX","DHI","DHR","DLR","DLTR","DOC","DOV",
    "DOW","DPZ","DRI","DTE","DUK","DVA","DVN","DXCM","EA","EBAY","ECL",
    "ED","EFX","EIX","EL","EME","EMR","EOG","EPAM","EQIX","EQR","EQT",
    "ES","ESS","ETN","ETR","EVRG","EW","EXC","EXE","EXPD","EXPE","EXR",
    "F","FANG","FAST","FCX","FDS","FDX","FE","FFIV","FICO","FIS","FISV",
    "FIX","FRT","FSLR","FTNT","FTV","GDDY","GE","GEHC","GEN","GEV","GILD",
    "GIS","GLW","GM","GNRC","GOOG","GOOGL","GPC","GPN","GRMN","GWW","HAL",
    "HAS","HCA","HD","HLT","HOLX","HON","HOOD","HPE","HPQ","HRL","HSIC",
    "HST","HSY","HUBB","HWM","IBKR","IBM","IDXX","IEX","IFF","INCY","INTC",
    "INTU","INVH","IP","IQV","IR","IRM","ISRG","IT","ITW","J","JBHT","JBL",
    "JCI","JKHY","JNJ","KDP","KEYS","KHC","KIM","KLAC","KMB","KMI","KO",
    "KR","KVUE","LDOS","LEN","LH","LII","LIN","LITE","LLY","LNT","LOW",
    "LRCX","LULU","LUV","LYB","MA","MAA","MAR","MAS","MCD","MCHP","MCK",
    "MCO","MDLZ","MDT","META","MKC","MLM","MMM","MNST","MOS","MPC","MPWR",
    "MRK","MRNA","MRSH","MSFT","MSI","MTD","MU","NEM","NEE","NI","NKE","NOW",
    "NRG","NSC","NTAP","NTRS","NUE","NVDA","NVR","NWS","NWSA","NXPI","O",
    "ODFL","OKE","OMC","ON","ORCL","ORLY","OTIS","OXY","PANW","PAYX","PCAR",
    "PCG","PEG","PEP","PFE","PG","PH","PHM","PKG","PLD","PLTR","POOL",
    "PODD","PPG","PPL","PSA","PSKY","PSX","PTC","PWR","PYPL","Q","QCOM",
    "REG","REGN","RL","RMD","ROK","ROL","ROP","ROST","RSG","RVTY","SAIC",
    "SATS","SBAC","SBUX","SHW","SJM","SLB","SMCI","SNA","SNDK","SNPS","SO",
    "SOLV","SPG","SRE","STE","STLD","STX","SW","SWK","SWKS","SYF","SYK",
    "SYY","T","TDG","TDY","TECH","TEL","TER","TGT","TJX","TKO","TMO",
    "TMUS","TPL","TPR","TRGP","TRMB","TSCO","TSLA","TSN","TT","TTD","TTWO",
    "TXN","TXT","TYL","UAL","UBER","UDR","UHS","ULTA","UNP","UPS","URI",
    "V","VICI","VLO","VLTO","VMC","VRSK","VRSN","VRT","VRTX","VST","VTR",
    "VTRS","VZ","WAB","WAT","WDAY","WDC","WEC","WELL","WM","WMB","WMT",
    "WSM","WST","XEL","XOM","XYL","XYZ","YUM","ZBH","ZBRA","ZTS",
]

HALAL_STOCKS = [s for s in _SP500_ALL if s not in _HARAM_EXCLUDE]

# Survivorship bias mitigation: stocks removed from S&P 500 in 2023-2025
# that were halal-compliant when removed. Including these in backtests
# prevents inflating historical performance by only testing current winners.
_SP500_DELISTED_HALAL = [
    # Removed 2024-2025
    "ATVI",  # Activision (acquired by MSFT)
    "DISH",  # Dish Network (merged with EchoStar)
    "FRC",   # First Republic (failed - but was in S&P)
    "SIVB",  # SVB Financial (failed)
    "LUMN",  # Lumen Technologies (removed)
    "VFC",   # VF Corporation (removed)
    "ALK",   # Alaska Air (removed)
    "NCLH",  # Norwegian Cruise (moved to haram)
    "SEE",   # Sealed Air (removed)
    "NWL",   # Newell Brands (removed)
    "OGN",   # Organon (removed)
    "PARA",  # Paramount (removed / merged)
    "SEDG",  # SolarEdge (removed)
    "ENPH",  # Enphase (removed)
    "BBWI",  # Bath & Body Works (removed)
    "CTLT",  # Catalent (acquired)
    "AAL",   # American Airlines (removed)
    # Removed 2023
    "TWTR",  # Twitter (acquired by Musk)
    "SBNY",  # Signature Bank (failed)
    "DISCA", # Discovery (merged into WBD)
    "XLNX",  # Xilinx (acquired by AMD)
    "CERN",  # Cerner (acquired by Oracle)
    "FBHS",  # Fortune Brands Home (reorganized)
    "RE",    # Everest Group (ticker changed)
    "NLSN",  # Nielsen (taken private)
    "DRE",   # Duke Realty (acquired)
    "VIAC",  # ViacomCBS (now PARA)
]

# For backtesting only — includes delisted stocks to avoid survivorship bias
HALAL_STOCKS_BACKTEST = HALAL_STOCKS + [
    s for s in _SP500_DELISTED_HALAL if s not in _HARAM_EXCLUDE
]

VALID_SYMBOLS.update(HALAL_STOCKS)
VALID_SYMBOLS.update(_SP500_DELISTED_HALAL)

_SIMPLE_CACHE_TTL = settings.SIMPLE_CACHE_TTL
_cache = {}
_cache_ts = {}

def cache_get(k):
    if time.time() - _cache_ts.get(k, 0) < _SIMPLE_CACHE_TTL:
        return _cache.get(k)
    return None

def cache_set(k, v):
    _cache[k] = v
    _cache_ts[k] = time.time()

# Model cache: stores trained models with TTL
# 8GB RAM tier — can hold more models for faster consensus
MODEL_CACHE_TTL = min(settings.MODEL_CACHE_TTL, 1800)  # 30 min cache
MODEL_CACHE_MAX = 20  # more models in cache for faster repeat consensus
_model_cache = {}
_model_cache_ts = {}

def model_cache_get(key):
    if time.time() - _model_cache_ts.get(key, 0) < MODEL_CACHE_TTL:
        return _model_cache.get(key)
    # Expired — remove it
    _model_cache.pop(key, None)
    _model_cache_ts.pop(key, None)
    return None

def model_cache_set(key, model_data):
    # Evict oldest if at capacity
    if len(_model_cache) >= MODEL_CACHE_MAX:
        oldest_key = min(_model_cache_ts, key=_model_cache_ts.get)
        _model_cache.pop(oldest_key, None)
        _model_cache_ts.pop(oldest_key, None)
        gc.collect()
    _model_cache[key] = model_data
    _model_cache_ts[key] = time.time()

def _flush_all_caches():
    """Emergency memory cleanup — flush all caches."""
    _model_cache.clear()
    _model_cache_ts.clear()
    _cache.clear()
    _cache_ts.clear()
    gc.collect()
    logger.info("All caches flushed for memory relief")

def fetch_yf(symbol, period="2y", start=None, end=None):
    """Fetch market data via Alpaca (primary) or yfinance (fallback)."""
    return fetch_market_data(symbol, period=period, start=start, end=end)


# ---------------------------------------------------------------------------
# Halal verification gate — blocks unverified / haram stocks from trading
# ---------------------------------------------------------------------------
# Runtime cache of halal verification results.
_VERIFIED_HARAM = set()  # symbols confirmed haram via FMP
_VERIFIED_HALAL = set()  # symbols confirmed halal via FMP or curated list
_CURATED_SET = None      # lazy-built set for O(1) lookups

def _get_curated_set():
    global _CURATED_SET
    if _CURATED_SET is None:
        _CURATED_SET = set(HALAL_STOCKS)
    return _CURATED_SET

def verify_halal(symbol: str) -> tuple[bool, str]:
    """Check if a symbol is verified halal. Returns (is_halal, reason).

    Priority:
    1. If in _HARAM_EXCLUDE → blocked (sector-level exclusion)
    2. If already verified this session → use cached result
    3. If in curated HALAL_STOCKS list → allowed (already sector-filtered)
    4. If FMP data available → run live AAOIFI screening
    5. If not in curated list AND FMP unavailable → blocked
    """
    sym = symbol.upper().strip()

    # 1. Sector-level exclusion (instant reject)
    if sym in _HARAM_EXCLUDE:
        return False, "Excluded sector (banks/insurance/alcohol/gambling/weapons/utilities/REITs)"

    # 2. Session cache — fast path
    if sym in _VERIFIED_HALAL:
        return True, "Verified halal"
    if sym in _VERIFIED_HARAM:
        return False, "Verified haram (AAOIFI screening failed)"

    # 3. Curated list — stocks already passed sector exclusion
    # This is the fast path that avoids FMP API calls for known stocks
    curated = _get_curated_set()
    if sym in curated:
        _VERIFIED_HALAL.add(sym)
        return True, "Halal (curated S&P 500 list, sector-verified)"

    # 4. Not in curated list — must verify via FMP AAOIFI screening
    try:
        result = get_halal_status(sym)
        if result is not None:
            if result.get("is_halal"):
                _VERIFIED_HALAL.add(sym)
                return True, "Verified halal (AAOIFI)"
            else:
                _VERIFIED_HARAM.add(sym)
                reasons = []
                if not result.get("debt_pass", True):
                    reasons.append(f"debt {result.get('debt_ratio', '?')}% > 33%")
                if not result.get("interest_pass", True):
                    reasons.append(f"interest {result.get('interest_ratio', '?')}% > 5%")
                if not result.get("haram_pass", True):
                    reasons.append("haram sector/industry")
                if not result.get("liquidity_pass", True):
                    reasons.append(f"liquidity {result.get('liquidity_ratio', '?')}% > 33%")
                return False, f"Haram: {', '.join(reasons)}" if reasons else "Verified haram"
        else:
            # FMP unavailable AND not in curated list → block
            logger.warning(f"Halal gate: {sym} BLOCKED — not in curated list, no FMP data")
            return False, "Cannot verify — not in curated list and no financial data"
    except Exception as e:
        logger.error(f"Halal verification error for {sym}: {e}")
        return False, f"Verification error: {e}"

# Technical analysis functions (ema, rsi, macd, atr, calc_metrics, safe_scale,
# walk_forward_split, get_score, prepare_sequences) imported from app.services.technical

def analyze(symbol, df):
    try:
        close = df["close"]
        price = float(close.iloc[-1])
        ema21 = float(ema(close, 21).iloc[-1])
        sma50 = float(close.tail(50).mean())
        sma200 = float(close.tail(200).mean())
        rsi_s = rsi(close)
        rsi_val = float(rsi_s.iloc[-1])
        rsi_prv = float(rsi_s.iloc[-2])
        _, _, hist_s = macd(close)
        hist_now = float(hist_s.iloc[-1])
        hist_prev = float(hist_s.iloc[-2])
        atr_val = float(atr(df).iloc[-1])
        avg_vol = float(df["volume"].iloc[-20:].mean())
        vol_rat = float(df["volume"].iloc[-1] / avg_vol) if avg_vol else 0
        support = float(df["low"].iloc[-10:].min())
        score = 0
        signals = []
        if price > ema21: score += 25; signals.append("Trend OK")
        if 40 <= rsi_val <= 60 and rsi_val > rsi_prv: score += 20; signals.append(f"RSI {rsi_val:.1f}")
        elif 35 <= rsi_val <= 65 and rsi_val > rsi_prv: score += 10; signals.append(f"RSI {rsi_val:.1f}")
        if hist_now > 0 and hist_prev <= 0: score += 20; signals.append("MACD crossover")
        elif hist_now > 0 and hist_now > hist_prev: score += 10; signals.append("MACD expanding")
        if vol_rat >= 1.5: score += 15; signals.append(f"Vol {vol_rat:.1f}x")
        elif vol_rat >= 1.2: score += 8; signals.append(f"Vol {vol_rat:.1f}x")
        atr_pct = atr_val / price * 100
        if 1.0 <= atr_pct <= 4.0: score += 10; signals.append(f"ATR {atr_pct:.1f}%")
        dist = (price - support) / support * 100
        if dist <= 2.0: score += 10; signals.append(f"Sup {dist:.1f}%")
        if score >= 75: sig = "STRONG BUY"
        elif score >= 55: sig = "BUY"
        elif score >= 35: sig = "WATCH"
        else: sig = "NO TRADE"
        pos = int((settings.RISK_CAPITAL * (settings.RISK_PCT / 100)) / atr_val) if atr_val > 0 else 0
        chg1w = float(((price - close.iloc[-5]) / close.iloc[-5]) * 100) if len(close) >= 5 else 0
        chg1m = float(((price - close.iloc[-21]) / close.iloc[-21]) * 100) if len(close) >= 21 else 0
        chg3m = float(((price - close.iloc[-63]) / close.iloc[-63]) * 100) if len(close) >= 63 else 0
        return {"symbol": symbol, "price": round(price, 2), "ema21": round(ema21, 2),
                "sma50": round(sma50, 2), "sma200": round(sma200, 2), "rsi": round(rsi_val, 1),
                "rsi_trend": "Rising" if rsi_val > rsi_prv else "Falling",
                "atr_pct": round(atr_pct, 2), "volume_ratio": round(vol_rat, 2),
                "chg_1w": round(chg1w, 2), "chg_1m": round(chg1m, 2), "chg_3m": round(chg3m, 2),
                "swing_score": round(score, 1), "swing_signal": sig, "signals": ", ".join(signals),
                "stop_loss": round(price - atr_val, 2), "take_profit": round(price + (2 * atr_val), 2),
                "position_size": pos, "halal": "Yes"}
    except Exception as e:
        logger.error(f"{symbol}: {e}")
        return None

def _analyze_one(symbol):
    # Halal gate — skip stocks that fail AAOIFI verification
    is_halal, reason = verify_halal(symbol)
    if not is_halal:
        return None
    df = fetch_yf(symbol)
    if df is None: return None
    return analyze(symbol, df)

def run_screener():
    cached = cache_get("all")
    if cached: return cached
    results = []
    with ThreadPoolExecutor(max_workers=settings.SCREENER_WORKERS) as pool:
        futures = {pool.submit(_analyze_one, s): s for s in HALAL_STOCKS}
        for f in as_completed(futures):
            r = f.result()
            if r: results.append(r)
    results.sort(key=lambda x: x["swing_score"], reverse=True)
    cache_set("all", results)
    return results

def run_backtest(symbol, start_date, end_date, portfolio, risk_pct, hold_days):
    try:
        df = fetch_yf(symbol, start=start_date, end=end_date)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        df["ema21"] = ema(df["close"], 21)
        df["rsi"] = rsi(df["close"])
        df["rsi_prev"] = df["rsi"].shift(1)
        _, _, h = macd(df["close"])
        df["hist"] = h
        df["hist_prev"] = h.shift(1)
        df["atr_v"] = atr(df)
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        df["support"] = df["low"].rolling(10).min()
        df = df.dropna().reset_index(drop=True)
        df["score"] = df.apply(get_score, axis=1)
        trades = []
        port = portfolio
        in_trade = False
        entry_idx = entry_price = sl = tp = pos_size = 0
        trade_returns = []
        for i in range(len(df)):
            row = df.iloc[i]
            if in_trade:
                days = i - entry_idx
                if float(row["low"]) <= sl:
                    pnl = (sl - entry_price) * pos_size
                    port += pnl
                    ret = (sl - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(sl, 2), "Result": "Stop Loss", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif float(row["high"]) >= tp:
                    pnl = (tp - entry_price) * pos_size
                    port += pnl
                    ret = (tp - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(tp, 2), "Result": "Take Profit", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
                elif days >= hold_days:
                    pnl = (float(row["close"]) - entry_price) * pos_size
                    port += pnl
                    ret = (float(row["close"]) - entry_price) / entry_price
                    trade_returns.append(ret)
                    trades.append({"Entry Date": str(df.iloc[entry_idx]["date"])[:10], "Exit Date": str(row["date"])[:10], "Entry": round(entry_price, 2), "Exit": round(float(row["close"]), 2), "Result": "Time Stop", "PnL": round(pnl, 2), "Portfolio": round(port, 2), "Days": days, "Score": int(df.iloc[entry_idx]["score"])})
                    in_trade = False
            if not in_trade and float(df.iloc[i]["score"]) >= 55:
                entry_price = float(df.iloc[i]["close"])
                atr_v = float(df.iloc[i]["atr_v"])
                sl = entry_price - atr_v
                tp = entry_price + (2 * atr_v)
                risk = entry_price - sl
                pos_size = int((port * (risk_pct / 100)) / risk) if risk > 0 else 0
                if pos_size > 0:
                    in_trade = True
                    entry_idx = i
        if not trades: return [{"Symbol": symbol, "Message": "No trades found"}]
        wins = [t for t in trades if t["Result"] == "Take Profit"]
        losses = [t for t in trades if t["Result"] == "Stop Loss"]
        metrics = calc_metrics(trade_returns)
        summary = [{"Symbol": symbol, "Period": f"{start_date} to {end_date}",
                    "Total Trades": len(trades), "Winners": len(wins), "Losers": len(losses),
                    "Win Rate %": round(len(wins) / len(trades) * 100, 1),
                    "Total PnL": round(sum(t["PnL"] for t in trades), 2),
                    "Final Portfolio": round(port, 2),
                    "Return %": round((port - portfolio) / portfolio * 100, 2),
                    "Avg Win": round(sum(t["PnL"] for t in wins) / len(wins), 2) if wins else 0,
                    "Avg Loss": round(sum(t["PnL"] for t in losses) / len(losses), 2) if losses else 0,
                    **metrics}]
        return summary + trades
    except Exception as e:
        return [{"Error": str(e)}]

def run_monte_carlo(symbol, days, simulations, df=None):
    try:
        from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        mc = MonteCarloSimulator(seed=42)
        result = mc.simulate(prices, n_simulations=simulations, forecast_days=days)
        s = result["summary"]
        summary = [{"Symbol": symbol.upper(), "Current Price": round(float(s["initial_price"]), 2),
                    "Expected Price": round(float(s["expected_terminal_price"]), 2),
                    "Prob Profit %": round(float(s["prob_profit"]) * 100, 1),
                    "Annual Vol %": round(float(s["annualized_volatility"]) * 100, 1),
                    "Annual Drift %": round(float(s["annualized_drift"]) * 100, 1),
                    "VaR 95%": round(float(s["terminal_var_95"]) * 100, 1),
                    "CVaR 95%": round(float(s["terminal_cvar_95"]) * 100, 1),
                    "Simulations": simulations, "Forecast Days": days}]
        day_stats = []
        for d in result["day_stats"][::5]:
            day_stats.append({"Day": d["day"], "Mean Price": round(d["mean_price"], 2),
                              "Median": round(d["median_price"], 2),
                              "P5 Bear": round(d["percentile_5"], 2),
                              "P25": round(d["percentile_25"], 2),
                              "P75": round(d["percentile_75"], 2),
                              "P95 Bull": round(d["percentile_95"], 2),
                              "VaR%": round(d["var_95"] * 100, 2)})
        return summary + day_stats
    except Exception as e:
        return [{"Error": str(e)}]

def _run_with_memory_guard(func, *args, **kwargs):
    """Run a model function with semaphore to prevent OOM and gc after."""
    _model_semaphore.acquire()
    try:
        return func(*args, **kwargs)
    finally:
        gc.collect()
        _model_semaphore.release()

def run_lstm(symbol, horizon, df=None):
    return _run_with_memory_guard(_run_lstm_inner, symbol, horizon, df=df)

def _run_lstm_inner(symbol, horizon, df=None):
    try:
        from openbb_forecast.models.lstm import LSTMForecaster
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        SEQ_LEN = 20
        X_train, y_train, X_test, y_test, mean_p, std_p = prepare_sequences(prices, SEQ_LEN, horizon)

        cache_key = f"lstm_{symbol}_{horizon}"
        cached = model_cache_get(cache_key)
        if cached:
            lstm_final, fold_scores = cached["model"], cached["fold_scores"]
        else:
            folds = walk_forward_split(X_train, y_train, n_splits=3)
            fold_scores = []
            for X_tr, y_tr, X_val, y_val in folds:
                lstm = LSTMForecaster(hidden_size=64, num_layers=2, epochs=30, learning_rate=0.001, patience=5)
                lstm.fit(X_tr, y_tr)
                preds = lstm.predict(X_val)
                mse = float(np.mean((preds - y_val) ** 2))
                fold_scores.append(round(mse, 4))
            lstm_final = LSTMForecaster(hidden_size=64, num_layers=2, epochs=50, learning_rate=0.001, patience=10)
            lstm_final.fit(X_train, y_train)
            model_cache_set(cache_key, {"model": lstm_final, "fold_scores": fold_scores})

        preds = lstm_final.predict(X_test[-1:])
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "LSTM + Walk-Forward", "Folds": len(fold_scores),
                    "Avg Fold MSE": round(float(np.mean(fold_scores)), 4),
                    "Forecast Horizon": horizon, "Training Samples": len(X_train)}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except Exception as e:
        return [{"Error": str(e)}]

def run_transformer(symbol, horizon, df=None):
    return _run_with_memory_guard(_run_transformer_inner, symbol, horizon, df=df)

def _run_transformer_inner(symbol, horizon, df=None):
    try:
        from openbb_forecast.models.transformer import TransformerForecaster
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        SEQ_LEN = 20
        X_train, y_train, X_test, y_test, mean_p, std_p = prepare_sequences(prices, SEQ_LEN, horizon)

        cache_key = f"transformer_{symbol}_{horizon}"
        cached = model_cache_get(cache_key)
        if cached:
            tf_final, fold_scores = cached["model"], cached["fold_scores"]
        else:
            folds = walk_forward_split(X_train, y_train, n_splits=3)
            fold_scores = []
            for X_tr, y_tr, X_val, y_val in folds:
                tf = TransformerForecaster(d_model=64, n_heads=4, num_layers=2, epochs=30, patience=5)
                tf.fit(X_tr, y_tr)
                preds = tf.predict(X_val)
                mse = float(np.mean((preds - y_val) ** 2))
                fold_scores.append(round(mse, 4))
            tf_final = TransformerForecaster(d_model=64, n_heads=4, num_layers=2, epochs=50, patience=10)
            tf_final.fit(X_train, y_train)
            model_cache_set(cache_key, {"model": tf_final, "fold_scores": fold_scores})

        preds = tf_final.predict(X_test[-1:])
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Transformer + Walk-Forward", "Folds": len(fold_scores),
                    "Avg Fold MSE": round(float(np.mean(fold_scores)), 4),
                    "Forecast Horizon": horizon}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except Exception as e:
        return [{"Error": str(e)}]

def run_ensemble(symbol, horizon, df=None):
    return _run_with_memory_guard(_run_ensemble_inner, symbol, horizon, df=df)

def _run_ensemble_inner(symbol, horizon, df=None):
    try:
        from openbb_forecast.models.ensemble import StackingForecaster
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        SEQ_LEN = 20
        X, y = [], []
        mean_p = prices.mean(); std_p = prices.std() + 1e-9
        norm = (prices - mean_p) / std_p
        for i in range(len(norm) - SEQ_LEN - horizon):
            X.append(norm[i:i+SEQ_LEN])
            y.append(norm[i+SEQ_LEN:i+SEQ_LEN+horizon])
        X = np.array(X); y = np.array(y)
        split = int(len(X) * 0.8)
        X_train, y_train = X[:split], y[:split]
        ensemble = StackingForecaster()
        ensemble.fit(X_train, y_train)
        preds = ensemble.predict(X[-1:])
        pred_prices = preds[0] * std_p + mean_p
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Stacking Ensemble (XGB+RF+GBR)", "Forecast Horizon": horizon}]
        forecasts = []
        for i, p in enumerate(pred_prices):
            change = ((float(p) - last_price) / last_price) * 100
            forecasts.append({"Day": i+1, "Predicted Price": round(float(p), 2),
                              "Change %": round(change, 2),
                              "Signal": "BUY" if change > 1 else "SELL" if change < -1 else "HOLD"})
        return summary + forecasts
    except Exception as e:
        return [{"Error": str(e)}]

def run_dqn(symbol, episodes, df=None):
    return _run_with_memory_guard(_run_dqn_inner, symbol, episodes, df=df)

def _run_dqn_inner(symbol, episodes, df=None):
    try:
        from openbb_forecast.agents.double_dqn import DoubleDQNAgent
        from openbb_forecast.agents.environment import TradingEnvironment
        from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
        from openbb_forecast.risk.manager import RiskManager
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        split = int(len(prices) * 0.8)
        train_prices = prices[:split]
        cost_model = TransactionCostModel(commission_bps=10)
        try:
            risk_mgr = RiskManager(max_position_size=0.2, stop_loss_pct=0.05, max_drawdown_pct=0.15)
        except Exception:
            risk_mgr = None
        train_env = TradingEnvironment(prices=train_prices, window_size=30,
                                       initial_capital=10000, cost_model=cost_model,
                                       risk_manager=risk_mgr)
        agent = DoubleDQNAgent(state_size=train_env.state_size, action_size=train_env.action_size)
        rewards = agent.train(train_env, episodes=episodes)
        state = train_env.reset()
        action = agent.select_action(state)
        action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
        signal = action_map.get(int(action), "HOLD")
        last_price = float(prices[-1])
        metrics = calc_metrics([r / 10000 for r in rewards])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Double DQN + Risk Manager", "Episodes": episodes,
                    "Signal": signal, "Best Reward": round(float(max(rewards)), 2),
                    "Avg Reward": round(float(np.mean(rewards)), 2),
                    "Final Reward": round(float(rewards[-1]), 2),
                    **metrics}]
        episode_results = [{"Episode": i+1, "Reward": round(float(r), 2),
                            "Status": "Good" if r > 0 else "Bad"} for i, r in enumerate(rewards)]
        return summary + episode_results
    except Exception as e:
        return [{"Error": str(e)}]

def run_policy_gradient(symbol, episodes, df=None):
    return _run_with_memory_guard(_run_policy_gradient_inner, symbol, episodes, df=df)

def _run_policy_gradient_inner(symbol, episodes, df=None):
    try:
        from openbb_forecast.agents.policy_gradient import PolicyGradientAgent
        from openbb_forecast.agents.environment import TradingEnvironment
        from openbb_forecast.backtesting.transaction_costs import TransactionCostModel
        if df is None:
            df = fetch_yf(symbol)
        if df is None: return [{"Error": f"No data for {symbol}"}]
        prices = np.array(df["close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        split = int(len(prices) * 0.8)
        cost_model = TransactionCostModel(commission_bps=10)
        env = TradingEnvironment(prices=prices[:split], window_size=30,
                                  initial_capital=10000, cost_model=cost_model)
        agent = PolicyGradientAgent(state_size=env.state_size, action_size=env.action_size)
        rewards = agent.train(env, episodes=episodes)
        state = env.reset()
        action = agent.select_action(state)
        action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
        signal = action_map.get(int(action), "HOLD")
        last_price = float(prices[-1])
        summary = [{"Symbol": symbol.upper(), "Current Price": round(last_price, 2),
                    "Model": "Policy Gradient (REINFORCE)", "Episodes": episodes,
                    "Signal": signal, "Best Reward": round(float(max(rewards)), 2),
                    "Avg Reward": round(float(np.mean(rewards)), 2)}]
        return summary + [{"Episode": i+1, "Reward": round(float(r), 2),
                           "Status": "Good" if r > 0 else "Bad"} for i, r in enumerate(rewards)]
    except Exception as e:
        return [{"Error": str(e)}]


def score_usx_single(symbol, df):
    """Score a single stock using the 10-point USX system. Returns dict or None."""
    try:
        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        vol   = df["volume"]

        ema9  = float(ema(close, 9).iloc[-2])
        ema21_v = float(ema(close, 21).iloc[-2])
        daily_ema21 = float(ema(close, 21).iloc[-1])
        daily_bullish = float(close.iloc[-1]) > daily_ema21
        rsi_s   = rsi(close)
        rsi_val = float(rsi_s.iloc[-2])
        _, _, hist_s = macd(close)
        hist_now  = float(hist_s.iloc[-2])
        hist_prev = float(hist_s.iloc[-3])
        macd_rising = hist_now > 0 and hist_now > hist_prev
        atr_val = float(atr(df).iloc[-1])

        high_s  = df["high"]
        low_s   = df["low"]
        tr      = pd.concat([high_s - low_s,
                             (high_s - close.shift()).abs(),
                             (low_s  - close.shift()).abs()], axis=1).max(axis=1)
        atr14   = tr.ewm(alpha=1/14, min_periods=14).mean()
        dm_plus  = (high_s.diff()).clip(lower=0)
        dm_minus = (-low_s.diff()).clip(lower=0)
        di_plus  = (dm_plus.ewm(alpha=1/14, min_periods=14).mean() / atr14 * 100).iloc[-2]
        di_minus = (dm_minus.ewm(alpha=1/14, min_periods=14).mean() / atr14 * 100).iloc[-2]
        adx_approx = abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9) * 100
        adx_ok     = adx_approx >= 20 and di_plus > di_minus

        sma20    = close.rolling(20).mean()
        std20    = close.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_width = ((bb_upper - bb_lower) / sma20 * 100)
        bb_width_sma = bb_width.rolling(20).mean()
        bb_squeeze = float(bb_width.iloc[-2]) < float(bb_width_sma.iloc[-2])

        vol_ma    = vol.rolling(20).mean()
        vol_ratio = float(vol.iloc[-2] / vol_ma.iloc[-2]) if float(vol_ma.iloc[-2]) > 0 else 1.0
        vol_ok    = vol_ratio >= 1.0
        gap_pct = float(((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100)
        gap_ok  = 0 <= gap_pct <= 1.5

        hlc3   = (df["high"] + df["low"] + df["close"]) / 3
        vwap   = (hlc3 * vol).cumsum() / vol.cumsum()
        above_vwap = float(close.iloc[-2]) > float(vwap.iloc[-2])
        roc5    = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100) if len(close) >= 6 else 0
        rs_ok   = roc5 > 0

        s1  = 1 if above_vwap else 0
        s2  = 1 if daily_bullish else 0
        s3  = 1 if ema9 > ema21_v else 0
        s4  = 1 if macd_rising else 0
        s5  = 1 if 40 <= rsi_val <= 65 else 0
        s6  = 1 if adx_ok else 0
        s7  = 1 if bb_squeeze else 0
        s8  = 1 if rs_ok else 0
        s9  = 1 if vol_ok else 0
        s10 = 1 if gap_ok else 0
        score = s1+s2+s3+s4+s5+s6+s7+s8+s9+s10

        if score >= 9:   signal = "STRONG BUY"
        elif score >= 7: signal = "BUY"
        elif score >= 6: signal = "NEUTRAL"
        else:            signal = "NO TRADE"

        price = float(close.iloc[-1])
        sl_mult, tp1_mult, tp2_mult, tp3_mult = 1.5, 1.5, 2.5, 4.0
        sl  = round(price - sl_mult  * atr_val, 2)
        tp1 = round(price + tp1_mult * atr_val, 2)
        tp2 = round(price + tp2_mult * atr_val, 2)
        tp3 = round(price + tp3_mult * atr_val, 2)
        be  = round(price + 1.0 * atr_val, 2)
        risk_per_share = price - sl
        risk_capital   = settings.RISK_CAPITAL * (settings.RISK_PCT / 100)
        qty = int(risk_capital / risk_per_share) if risk_per_share > 0 else 0

        breakdown = []
        if s1: breakdown.append("VWAP")
        if s2: breakdown.append("Daily")
        if s3: breakdown.append("EMA")
        if s4: breakdown.append("MACD")
        if s5: breakdown.append(f"RSI {rsi_val:.0f}")
        if s6: breakdown.append("ADX")
        if s7: breakdown.append("Squeeze")
        if s8: breakdown.append("RS+")
        if s9: breakdown.append(f"Vol {vol_ratio:.1f}x")
        if s10: breakdown.append("Gap OK")

        return {
            "Symbol": symbol, "Price": round(price, 2), "Score": f"{score}/10",
            "Signal": signal, "RSI": round(rsi_val, 1), "ADX": round(adx_approx, 1),
            "BB Squeeze": "Yes" if bb_squeeze else "No", "Vol Ratio": round(vol_ratio, 2),
            "Gap %": round(gap_pct, 2), "Stop Loss": sl, "TP1 (50%)": tp1,
            "TP2 (30%)": tp2, "TP3 (20%)": tp3, "Breakeven": be, "Qty": qty,
            "RR1": round(tp1_mult/sl_mult, 2), "RR2": round(tp2_mult/sl_mult, 2),
            "RR3": round(tp3_mult/sl_mult, 2), "Confluence": ", ".join(breakdown),
            "_score_int": score,
        }
    except Exception as e:
        logger.error(f"USX {symbol}: {e}")
        return None

def _usx_one(symbol):
    df = fetch_yf(symbol)
    if df is None: return None
    return score_usx_single(symbol, df)

def run_usx_screener(min_score=7, direction="Long Only"):
    try:
        results = []
        with ThreadPoolExecutor(max_workers=settings.SCREENER_WORKERS) as pool:
            futures = {pool.submit(_usx_one, s): s for s in HALAL_STOCKS}
            for f in as_completed(futures):
                r = f.result()
                if r and r["_score_int"] >= min_score:
                    row = {k: v for k, v in r.items() if k != "_score_int"}
                    results.append(row)
        results.sort(key=lambda x: int(x["Score"].split("/")[0]), reverse=True)
        return results if results else [{"Message": "No stocks meet USX Pro criteria"}]
    except Exception as e:
        return [{"Error": str(e)}]


def _send_consensus_breakdown(symbol, verdict, confidence, price,
                              votes_buy, votes_sell, votes_hold,
                              sl, tp1, tp2, tp3, details):
    """Send full 14-tool consensus breakdown to Telegram."""
    from app.services.telegram_alert import send_message as tg_send, _is_duplicate_signal

    # Skip if already sent this symbol recently
    dedup_key = f"breakdown_{symbol}"
    if _is_duplicate_signal(symbol, f"breakdown_{verdict}"):
        return

    # Verdict icon
    icons = {
        "STRONG BUY": "[STRONG BUY]", "BUY": "[BUY]",
        "WEAK BUY": "[WEAK BUY]", "STRONG SELL": "[STRONG SELL]",
        "SELL": "[SELL]", "WEAK SELL": "[WEAK SELL]",
    }
    icon = icons.get(verdict, "[--]")

    # Header
    lines = [
        f"{icon} AI CONSENSUS: {symbol}",
        f"Verdict: {verdict} | Confidence: {confidence:.0f}%",
        f"Price: ${price:.2f}",
        f"Votes: BUY {votes_buy} | SELL {votes_sell} | HOLD {votes_hold}",
        f"",
        f"--- 14-TOOL BREAKDOWN ---",
    ]

    # Tool details
    for d in details:
        tool = d.get("Tool", "?")
        signal = d.get("Signal", "?")
        vote = d.get("Vote", "?")
        # Vote emoji
        if vote == "BUY":
            v_icon = "+"
        elif vote == "SELL":
            v_icon = "-"
        elif vote == "-":
            v_icon = "X"
        else:
            v_icon = "="
        lines.append(f"  [{v_icon}] {tool}: {signal}")

    # Levels
    lines.append(f"")
    lines.append(f"Entry: ${price:.2f}")
    lines.append(f"SL: ${sl:.2f} | TP1: ${tp1:.2f}")
    lines.append(f"TP2: ${tp2:.2f} | TP3: ${tp3:.2f}")

    from datetime import datetime
    lines.append(f"")
    lines.append(f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")

    tg_send("\n".join(lines))


def _vote_signal(sig_str):
    """Helper: convert signal string to (vote_str, buy_delta, sell_delta, hold_delta)."""
    if "STRONG BUY" in sig_str:
        return "BUY", 2, 0, 0
    elif "BUY" in sig_str:
        return "BUY", 1, 0, 0
    elif "SELL" in sig_str:
        return "SELL", 0, 1, 0
    return "HOLD", 0, 0, 1

def run_consensus(symbol, horizon=5, episodes=10):
    try:
        # --- Halal verification gate (MUST pass before any analysis) ---
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            logger.info(f"Consensus blocked for {symbol}: {halal_reason}")
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Error": f"Not halal-verified: {halal_reason}",
                     "Action": "DO NOT TRADE"}]

        votes_buy  = 0
        votes_sell = 0
        votes_hold = 0
        details    = []

        df = fetch_yf(symbol)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])

        # --- 1. Halal Screener ---
        try:
            r = analyze(symbol, df)
            if r:
                sig = r["swing_signal"]
                vote, b, s, h = _vote_signal(sig)
                votes_buy += b; votes_sell += s; votes_hold += h
                details.append({"Tool": "Halal Screener", "Signal": sig, "Vote": vote, "Score": r["swing_score"]})
        except Exception:
            details.append({"Tool": "Halal Screener", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 2. USX Pro (single symbol - no full scan) ---
        try:
            usx_r = score_usx_single(symbol, df)
            if usx_r and usx_r["_score_int"] >= 7:
                vote, b, s, h = _vote_signal(usx_r["Signal"])
                votes_buy += b; votes_sell += s; votes_hold += h
                details.append({"Tool": "USX Pro", "Signal": usx_r["Signal"], "Vote": vote, "Score": usx_r["Score"]})
            else:
                votes_hold += 1
                score_str = usx_r["Score"] if usx_r else "N/A"
                details.append({"Tool": "USX Pro", "Signal": f"Below Min ({score_str})", "Vote": "HOLD", "Score": 0})
        except Exception:
            details.append({"Tool": "USX Pro", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 3. Backtest ---
        try:
            import datetime
            end_date = datetime.date.today().strftime("%Y-%m-%d")
            start_date = (datetime.date.today() - datetime.timedelta(days=365*2)).strftime("%Y-%m-%d")
            bt = run_backtest(symbol, start_date, end_date, 100000, 1.0, 3)
            if bt and len(bt) > 0 and "Return %" in bt[0]:
                ret = float(bt[0]["Return %"])
                win_rate_v = float(bt[0].get("Win Rate %", 0))
                sharpe = float(bt[0].get("Sharpe Ratio", 0))
                sig = f"Return {ret:.1f}% WR {win_rate_v:.0f}%"
                if ret > 5 and win_rate_v > 50 and sharpe > 0.5:
                    votes_buy += 1; vote = "BUY"
                elif ret < -5 or (win_rate_v < 40 and sharpe < 0):
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                details.append({"Tool": "Backtest 2Y", "Signal": sig, "Vote": vote, "Score": round(ret, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "Backtest 2Y", "Signal": "No trades", "Vote": "HOLD", "Score": 0})
        except Exception:
            details.append({"Tool": "Backtest 2Y", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 4. Monte Carlo (reuse df) ---
        try:
            from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
            prices_arr = np.array(df["close"].values, dtype=np.float64).flatten()
            prices_arr = prices_arr[~np.isnan(prices_arr)]
            mc = MonteCarloSimulator(seed=42)
            result = mc.simulate(prices_arr, n_simulations=500, forecast_days=horizon)
            prob = float(result["summary"]["prob_profit"])
            exp_price = float(result["summary"]["expected_terminal_price"])
            chg = (exp_price - price) / price * 100
            if prob >= 0.60: votes_buy += 1; vote = "BUY"
            elif prob <= 0.45: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            details.append({"Tool": "Monte Carlo", "Signal": f"Prob {prob*100:.1f}% Exp {chg:+.1f}%", "Vote": vote, "Score": round(prob*100, 1)})
        except Exception:
            details.append({"Tool": "Monte Carlo", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 5. Bollinger Band Squeeze + Breakout ---
        try:
            close = df["close"]
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper_bb = sma20 + 2 * std20
            lower_bb = sma20 - 2 * std20
            bb_width = ((upper_bb - lower_bb) / sma20 * 100).iloc[-1]
            bb_width_prev = ((upper_bb - lower_bb) / sma20 * 100).iloc[-5]
            price_vs_upper = (price - float(upper_bb.iloc[-1])) / float(upper_bb.iloc[-1]) * 100
            # Squeeze (narrow bands) + breakout above = BUY
            if bb_width < 4 and price > float(upper_bb.iloc[-1]):
                votes_buy += 1; vote = "BUY"; sig = f"Squeeze Breakout (BW {bb_width:.1f}%)"
            elif bb_width < 4 and price < float(lower_bb.iloc[-1]):
                votes_sell += 1; vote = "SELL"; sig = f"Squeeze Breakdown (BW {bb_width:.1f}%)"
            elif price > float(sma20.iloc[-1]) and bb_width > bb_width_prev:
                votes_buy += 1; vote = "BUY"; sig = f"BB Expanding Up (BW {bb_width:.1f}%)"
            elif price < float(sma20.iloc[-1]) and bb_width > bb_width_prev:
                votes_sell += 1; vote = "SELL"; sig = f"BB Expanding Down (BW {bb_width:.1f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"BB Neutral (BW {bb_width:.1f}%)"
            details.append({"Tool": "Bollinger Bands", "Signal": sig, "Vote": vote, "Score": round(bb_width, 1)})
        except Exception:
            details.append({"Tool": "Bollinger Bands", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 6. Multi-Timeframe EMA (21/50/200 alignment) ---
        try:
            close = df["close"]
            ema21_val = float(ema(close, 21).iloc[-1])
            sma50_val = float(close.rolling(50).mean().iloc[-1])
            sma200_val = float(close.rolling(200).mean().iloc[-1])
            # Perfect alignment: price > EMA21 > SMA50 > SMA200
            if price > ema21_val > sma50_val > sma200_val:
                votes_buy += 2; vote = "BUY"  # double weight for strong trend
                sig = "Perfect Uptrend (P>21>50>200)"
            elif price > ema21_val > sma50_val:
                votes_buy += 1; vote = "BUY"
                sig = "Uptrend (P>21>50)"
            elif price < ema21_val < sma50_val < sma200_val:
                votes_sell += 2; vote = "SELL"
                sig = "Perfect Downtrend (P<21<50<200)"
            elif price < ema21_val < sma50_val:
                votes_sell += 1; vote = "SELL"
                sig = "Downtrend (P<21<50)"
            else:
                votes_hold += 1; vote = "HOLD"
                sig = "Mixed alignment"
            details.append({"Tool": "EMA Alignment", "Signal": sig, "Vote": vote, "Score": 0})
        except Exception:
            details.append({"Tool": "EMA Alignment", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 7. XGBoost Quick Forecast ---
        try:
            close = df["close"]
            # Feature engineering: returns, RSI, MACD, volume ratio
            rets = close.pct_change().dropna()
            rsi_s = rsi(close)
            _, _, macd_hist = macd(close)
            features = pd.DataFrame({
                "ret_1": rets, "ret_5": rets.rolling(5).mean(),
                "ret_10": rets.rolling(10).mean(), "rsi": rsi_s,
                "macd_h": macd_hist,
                "vol_ratio": df["volume"] / df["volume"].rolling(20).mean(),
            }).dropna()
            target = (close.shift(-horizon) > close).astype(int)  # 1 if price goes up
            features = features.iloc[:-horizon]
            target = target.iloc[features.index[0]:features.index[-1]+1]
            # Train/test split
            split_idx = int(len(features) * 0.8)
            X_train, X_test = features.iloc[:split_idx], features.iloc[split_idx:]
            y_train, y_test = target.iloc[:split_idx], target.iloc[split_idx:]
            import xgboost as xgb
            model = xgb.XGBClassifier(n_estimators=50, max_depth=3, use_label_encoder=False,
                                       eval_metric="logloss", verbosity=0)
            model.fit(X_train.values, y_train.values)
            # Predict on latest data
            latest = features.iloc[-1:].values
            prob_up = float(model.predict_proba(latest)[0][1])
            accuracy = float((model.predict(X_test.values) == y_test.values).mean() * 100)
            if prob_up > 0.6: votes_buy += 1; vote = "BUY"
            elif prob_up < 0.4: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            sig = f"P(up)={prob_up:.0%} Acc={accuracy:.0f}%"
            details.append({"Tool": "XGBoost", "Signal": sig, "Vote": vote, "Score": round(prob_up * 100, 1)})
            del model, X_train, X_test, y_train, y_test  # free memory
        except Exception:
            details.append({"Tool": "XGBoost", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 8. Momentum Score (Rate of Change + ADX) ---
        try:
            close = df["close"]
            roc_10 = float((price / close.iloc[-10] - 1) * 100)
            roc_20 = float((price / close.iloc[-20] - 1) * 100)
            roc_60 = float((price / close.iloc[-60] - 1) * 100)
            # ADX approximation
            high, low = df["high"], df["low"]
            plus_dm = high.diff().clip(lower=0)
            minus_dm = (-low.diff()).clip(lower=0)
            atr_14 = atr(df, 14)
            plus_di = (plus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            minus_di = (minus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            # Strong upward momentum
            if roc_10 > 2 and roc_20 > 3 and dx > 20 and plus_di > minus_di:
                votes_buy += 1; vote = "BUY"; sig = f"Strong Up (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            elif roc_10 < -2 and roc_20 < -3 and dx > 20 and minus_di > plus_di:
                votes_sell += 1; vote = "SELL"; sig = f"Strong Down (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Weak (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            details.append({"Tool": "Momentum", "Signal": sig, "Vote": vote, "Score": round(roc_10, 1)})
        except Exception:
            details.append({"Tool": "Momentum", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 9. Volume-Price Divergence ---
        try:
            close = df["close"]
            vol = df["volume"]
            price_chg_5d = float((price / close.iloc[-5] - 1) * 100)
            vol_chg_5d = float((vol.iloc[-5:].mean() / vol.iloc[-25:-5].mean() - 1) * 100)
            # Price up + volume up = confirmed strength
            if price_chg_5d > 1 and vol_chg_5d > 20:
                votes_buy += 1; vote = "BUY"; sig = f"Confirmed Up (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            # Price up + volume down = weak rally (divergence)
            elif price_chg_5d > 1 and vol_chg_5d < -10:
                votes_sell += 1; vote = "SELL"; sig = f"Weak Rally (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            # Price down + volume up = selling pressure
            elif price_chg_5d < -1 and vol_chg_5d > 20:
                votes_sell += 1; vote = "SELL"; sig = f"Sell Pressure (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            # Price down + volume down = exhaustion, potential bounce
            elif price_chg_5d < -1 and vol_chg_5d < -10:
                votes_buy += 1; vote = "BUY"; sig = f"Exhaustion (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            details.append({"Tool": "Volume-Price", "Signal": sig, "Vote": vote, "Score": round(price_chg_5d, 1)})
        except Exception:
            details.append({"Tool": "Volume-Price", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # === ML MODELS (re-enabled with 8GB RAM tier) ===

        # --- 10. LSTM Neural Network ---
        try:
            lstm_r = run_lstm(symbol, horizon, df=df)
            if lstm_r and len(lstm_r) > 1:
                # Forecasts are in items [1:] with "Predicted Price" and "Change %"
                last_forecast = lstm_r[-1]  # last day forecast
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "LSTM", "Signal": sig, "Vote": vote, "Score": round(pct_chg, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "LSTM", "Signal": "No forecast", "Vote": "HOLD", "Score": 0})
        except Exception:
            details.append({"Tool": "LSTM", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 11. Transformer Attention Network ---
        try:
            tf_r = run_transformer(symbol, horizon, df=df)
            if tf_r and len(tf_r) > 1:
                last_forecast = tf_r[-1]
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "Transformer", "Signal": sig, "Vote": vote, "Score": round(pct_chg, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "Transformer", "Signal": "No forecast", "Vote": "HOLD", "Score": 0})
        except Exception:
            details.append({"Tool": "Transformer", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 12. Stacking Ensemble (XGB + RF + GBM → Ridge) ---
        try:
            ens_r = run_ensemble(symbol, horizon, df=df)
            if ens_r and len(ens_r) > 1:
                last_forecast = ens_r[-1]
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "Ensemble", "Signal": sig, "Vote": vote, "Score": round(pct_chg, 1)})
            else:
                votes_hold += 1
                details.append({"Tool": "Ensemble", "Signal": "No forecast", "Vote": "HOLD", "Score": 0})
        except Exception:
            details.append({"Tool": "Ensemble", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 13. Double DQN Reinforcement Learning ---
        try:
            dqn_r = run_dqn(symbol, episodes, df=df)
            if dqn_r and len(dqn_r) > 0:
                # DQN returns [summary_dict, ...] with "Action" key
                summary_item = dqn_r[0]
                action = summary_item.get("Action", summary_item.get("Recommendation", ""))
                reward = summary_item.get("Total Reward", summary_item.get("Final Portfolio", 0))
                if "BUY" in str(action).upper():
                    votes_buy += 1; vote = "BUY"
                elif "SELL" in str(action).upper():
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"{action} (R={reward:.1f})" if isinstance(reward, (int, float)) else str(action)
                details.append({"Tool": "DQN", "Signal": sig, "Vote": vote, "Score": round(float(reward), 1) if isinstance(reward, (int, float)) else 0})
            else:
                votes_hold += 1
                details.append({"Tool": "DQN", "Signal": "No action", "Vote": "HOLD", "Score": 0})
        except Exception:
            details.append({"Tool": "DQN", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- 14. Policy Gradient (REINFORCE) ---
        try:
            pg_r = run_policy_gradient(symbol, episodes, df=df)
            if pg_r and len(pg_r) > 0:
                summary_item = pg_r[0]
                action = summary_item.get("Action", summary_item.get("Recommendation", ""))
                reward = summary_item.get("Total Reward", summary_item.get("Final Portfolio", 0))
                if "BUY" in str(action).upper():
                    votes_buy += 1; vote = "BUY"
                elif "SELL" in str(action).upper():
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"{action} (R={reward:.1f})" if isinstance(reward, (int, float)) else str(action)
                details.append({"Tool": "PolicyGrad", "Signal": sig, "Vote": vote, "Score": round(float(reward), 1) if isinstance(reward, (int, float)) else 0})
            else:
                votes_hold += 1
                details.append({"Tool": "PolicyGrad", "Signal": "No action", "Vote": "HOLD", "Score": 0})
        except Exception:
            details.append({"Tool": "PolicyGrad", "Signal": "ERROR", "Vote": "-", "Score": 0})

        # --- Final Verdict ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        # Verdict thresholds (14 tools, EMA gives 2x for strong trends → max ~16 votes)
        if votes_buy >= 10:        verdict, action_str = "STRONG BUY", "STRONG ENTER"
        elif votes_buy > votes_sell and confidence >= 60: verdict, action_str = "BUY", "ENTER"
        elif votes_buy > votes_sell and confidence >= 40: verdict, action_str = "WEAK BUY", "WEAK SIGNAL"
        elif votes_sell >= 10:     verdict, action_str = "STRONG SELL", "STRONG AVOID"
        elif votes_sell > votes_buy and confidence >= 60: verdict, action_str = "SELL", "AVOID"
        elif votes_sell > votes_buy:                      verdict, action_str = "WEAK SELL", "WEAK SELL"
        else:                      verdict, action_str = "NEUTRAL", "WAIT"

        sl  = round(price - 1.5 * atr_val, 2)
        tp1 = round(price + 1.5 * atr_val, 2)
        tp2 = round(price + 2.5 * atr_val, 2)
        tp3 = round(price + 4.0 * atr_val, 2)

        summary = [{
            "Symbol":       symbol.upper(),
            "Price":        round(price, 2),
            "Verdict":      verdict,
            "Confidence %": confidence,
            "Votes BUY":    votes_buy,
            "Votes SELL":   votes_sell,
            "Votes HOLD":   votes_hold,
            "Total Tools":  len(details),
            "Action":       action_str,
            "Stop Loss":    sl,
            "TP1":          tp1,
            "TP2":          tp2,
            "TP3":          tp3,
        }]

        # Record signal for audit trail (Phase 4.3: accuracy tracking)
        try:
            record_signal(
                symbol=symbol.upper(),
                signal_type="consensus",
                signal=verdict,
                score=confidence,
                price=round(price, 2),
                stop_loss=sl,
                take_profit=tp1,
                confidence=confidence,
                details={"votes_buy": votes_buy, "votes_sell": votes_sell,
                         "votes_hold": votes_hold, "tools": len(details)},
            )
        except Exception:
            pass  # non-critical

        # Telegram alert: chart + breakdown for STRONG BUY only
        try:
            if verdict == "STRONG BUY":
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    details=details,
                )
        except Exception:
            pass  # non-critical

        # Auto-trade execution (Stage 1: Paper Trading)
        try:
            trade_result = auto_trade_signal(
                symbol=symbol.upper(),
                verdict=verdict,
                confidence=confidence,
                price=round(price, 2),
                stop_loss=sl,
                take_profit=tp1,
                votes_buy=votes_buy,
                votes_sell=votes_sell,
                votes_hold=votes_hold,
                details={"tools": len(details)},
            )
            if trade_result:
                summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
                summary[0]["Trade_Reason"] = trade_result.get("reason", "")
        except Exception as e:
            logger.debug(f"Auto-trade hook error: {e}")

        return summary + details

    except Exception as e:
        return [{"Error": str(e)}]


# ============================================================================
# MULTI-STRATEGY CONSENSUS FUNCTIONS
# ============================================================================

def run_consensus_momentum(symbol, horizon=5):
    """Strategy A: Momentum Alpha — trend-following with 5 tools.

    Tools: EMA Alignment (2x), Momentum ROC+ADX, Backtest 2Y, XGBoost, Monte Carlo
    Entry: 4+ of 5 tools BUY + price > SMA50
    Exit: Trailing stop 3%, TP at 2.5x ATR
    """
    try:
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Strategy": "A-Momentum", "Error": f"Not halal: {halal_reason}"}]

        votes_buy, votes_sell, votes_hold = 0, 0, 0
        details = []

        df = fetch_yf(symbol)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])
        close = df["close"]

        # SMA50 gate — only buy if above 50-day SMA (trend filter)
        sma50_val = float(close.rolling(50).mean().iloc[-1])
        above_sma50 = price > sma50_val

        # --- Tool 1: EMA Alignment (2x weight for strong trends) ---
        try:
            ema21_val = float(ema(close, 21).iloc[-1])
            sma200_val = float(close.rolling(200).mean().iloc[-1])
            if price > ema21_val > sma50_val > sma200_val:
                votes_buy += 2; vote = "BUY"; sig = "Perfect Uptrend (P>21>50>200)"
            elif price > ema21_val > sma50_val:
                votes_buy += 1; vote = "BUY"; sig = "Uptrend (P>21>50)"
            elif price < ema21_val < sma50_val < sma200_val:
                votes_sell += 2; vote = "SELL"; sig = "Perfect Downtrend (P<21<50<200)"
            elif price < ema21_val < sma50_val:
                votes_sell += 1; vote = "SELL"; sig = "Downtrend (P<21<50)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = "Mixed alignment"
            details.append({"Tool": "EMA Alignment", "Signal": sig, "Vote": vote})
        except Exception:
            details.append({"Tool": "EMA Alignment", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 2: Momentum (ROC + ADX) ---
        try:
            roc_10 = float((price / close.iloc[-10] - 1) * 100)
            roc_20 = float((price / close.iloc[-20] - 1) * 100)
            high, low = df["high"], df["low"]
            plus_dm = high.diff().clip(lower=0)
            minus_dm = (-low.diff()).clip(lower=0)
            atr_14 = atr(df, 14)
            plus_di = (plus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            minus_di = (minus_dm.rolling(14).mean() / atr_14 * 100).iloc[-1]
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            if roc_10 > 2 and roc_20 > 3 and dx > 20 and plus_di > minus_di:
                votes_buy += 1; vote = "BUY"; sig = f"Strong Up (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            elif roc_10 < -2 and roc_20 < -3 and dx > 20 and minus_di > plus_di:
                votes_sell += 1; vote = "SELL"; sig = f"Strong Down (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Weak (ROC10={roc_10:+.1f}% ADX={dx:.0f})"
            details.append({"Tool": "Momentum", "Signal": sig, "Vote": vote})
        except Exception:
            details.append({"Tool": "Momentum", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 3: Backtest 2Y ---
        try:
            import datetime as _dt
            end_date = _dt.date.today().strftime("%Y-%m-%d")
            start_date = (_dt.date.today() - _dt.timedelta(days=365 * 2)).strftime("%Y-%m-%d")
            bt = run_backtest(symbol, start_date, end_date, 100000, 1.0, 3)
            if bt and len(bt) > 0 and "Return %" in bt[0]:
                ret = float(bt[0]["Return %"])
                win_rate_v = float(bt[0].get("Win Rate %", 0))
                sharpe = float(bt[0].get("Sharpe Ratio", 0))
                if ret > 5 and win_rate_v > 50 and sharpe > 0.5:
                    votes_buy += 1; vote = "BUY"
                elif ret < -5 or (win_rate_v < 40 and sharpe < 0):
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"Return {ret:.1f}% WR {win_rate_v:.0f}%"
                details.append({"Tool": "Backtest 2Y", "Signal": sig, "Vote": vote})
            else:
                votes_hold += 1
                details.append({"Tool": "Backtest 2Y", "Signal": "No trades", "Vote": "HOLD"})
        except Exception:
            details.append({"Tool": "Backtest 2Y", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 4: XGBoost ---
        try:
            rets = close.pct_change().dropna()
            rsi_s = rsi(close)
            _, _, macd_hist = macd(close)
            features = pd.DataFrame({
                "ret_1": rets, "ret_5": rets.rolling(5).mean(),
                "ret_10": rets.rolling(10).mean(), "rsi": rsi_s,
                "macd_h": macd_hist,
                "vol_ratio": df["volume"] / df["volume"].rolling(20).mean(),
            }).dropna()
            target = (close.shift(-horizon) > close).astype(int)
            features = features.iloc[:-horizon]
            target = target.iloc[features.index[0]:features.index[-1] + 1]
            split_idx = int(len(features) * 0.8)
            X_train, X_test = features.iloc[:split_idx], features.iloc[split_idx:]
            y_train, y_test = target.iloc[:split_idx], target.iloc[split_idx:]
            import xgboost as xgb
            model = xgb.XGBClassifier(n_estimators=50, max_depth=3, use_label_encoder=False,
                                       eval_metric="logloss", verbosity=0)
            model.fit(X_train.values, y_train.values)
            prob_up = float(model.predict_proba(features.iloc[-1:].values)[0][1])
            accuracy = float((model.predict(X_test.values) == y_test.values).mean() * 100)
            if prob_up > 0.6: votes_buy += 1; vote = "BUY"
            elif prob_up < 0.4: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            sig = f"P(up)={prob_up:.0%} Acc={accuracy:.0f}%"
            details.append({"Tool": "XGBoost", "Signal": sig, "Vote": vote})
            del model
        except Exception:
            details.append({"Tool": "XGBoost", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 5: Monte Carlo ---
        try:
            from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
            prices_arr = np.array(close.values, dtype=np.float64).flatten()
            prices_arr = prices_arr[~np.isnan(prices_arr)]
            mc = MonteCarloSimulator(seed=42)
            result = mc.simulate(prices_arr, n_simulations=500, forecast_days=horizon)
            prob = float(result["summary"]["prob_profit"])
            exp_price = float(result["summary"]["expected_terminal_price"])
            chg = (exp_price - price) / price * 100
            if prob >= 0.60: votes_buy += 1; vote = "BUY"
            elif prob <= 0.45: votes_sell += 1; vote = "SELL"
            else: votes_hold += 1; vote = "HOLD"
            details.append({"Tool": "Monte Carlo", "Signal": f"Prob {prob * 100:.1f}% Exp {chg:+.1f}%", "Vote": vote})
        except Exception:
            details.append({"Tool": "Monte Carlo", "Signal": "ERROR", "Vote": "-"})

        # --- Verdict (max ~7 votes with EMA 2x) ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        # STRONG requires 4+ BUY votes AND price above SMA50
        if votes_buy >= 4 and above_sma50:
            verdict = "STRONG BUY"
        elif votes_buy > votes_sell and above_sma50 and confidence >= 55:
            verdict = "BUY"
        elif votes_sell >= 4:
            verdict = "STRONG SELL"
        elif votes_sell > votes_buy and confidence >= 55:
            verdict = "SELL"
        elif votes_buy > votes_sell:
            verdict = "WEAK BUY"
        elif votes_sell > votes_buy:
            verdict = "WEAK SELL"
        else:
            verdict = "NEUTRAL"

        sl = round(price - 1.5 * atr_val, 2)
        tp1 = round(price + 2.5 * atr_val, 2)   # wider TP for momentum
        tp2 = round(price + 4.0 * atr_val, 2)
        tp3 = round(price + 6.0 * atr_val, 2)

        summary = [{
            "Symbol": symbol.upper(), "Strategy": "A-Momentum Alpha",
            "Price": round(price, 2), "Verdict": verdict,
            "Confidence %": confidence,
            "Votes BUY": votes_buy, "Votes SELL": votes_sell, "Votes HOLD": votes_hold,
            "SMA50 Filter": "ABOVE" if above_sma50 else "BELOW",
            "Stop Loss": sl, "TP1": tp1, "TP2": tp2, "TP3": tp3,
        }]

        # Telegram: chart + breakdown for STRONG BUY only
        if verdict == "STRONG BUY":
            try:
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=f"[A] Momentum: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=f"[A] Momentum: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell, votes_hold=votes_hold,
                    sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, details=details,
                )
            except Exception:
                pass

        # Auto-trade via Strategy A's account
        if "STRONG" in verdict:
            try:
                trade_result = auto_trade_signal(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, take_profit=tp1,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, details={"strategy": "A"},
                    strategy_id="A",
                )
                if trade_result:
                    summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
            except Exception:
                pass

        return summary + details

    except Exception as e:
        return [{"Error": str(e), "Strategy": "A-Momentum"}]


def run_consensus_reversion(symbol, horizon=3):
    """Strategy B: Mean Reversion — buy oversold stocks.

    Tools: Bollinger Bands, RSI, Volume-Price Divergence, Stochastic, OBV
    Entry: RSI < 35 + price near lower BB + volume confirmation
    Exit: Static SL 2%, TP when RSI > 60 or price hits middle BB
    """
    try:
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Strategy": "B-Reversion", "Error": f"Not halal: {halal_reason}"}]

        votes_buy, votes_sell, votes_hold = 0, 0, 0
        details = []

        df = fetch_yf(symbol)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])
        close = df["close"]

        # --- Tool 1: Bollinger Bands ---
        try:
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper_bb = sma20 + 2 * std20
            lower_bb = sma20 - 2 * std20
            mid_bb = float(sma20.iloc[-1])
            bb_width = ((upper_bb - lower_bb) / sma20 * 100).iloc[-1]
            lower_bb_val = float(lower_bb.iloc[-1])
            upper_bb_val = float(upper_bb.iloc[-1])

            # Mean reversion: BUY when price near/below lower band
            if price <= lower_bb_val:
                votes_buy += 1; vote = "BUY"; sig = f"At/Below Lower BB (${lower_bb_val:.2f})"
            elif price >= upper_bb_val:
                votes_sell += 1; vote = "SELL"; sig = f"At/Above Upper BB (${upper_bb_val:.2f})"
            else:
                pct_bb = (price - lower_bb_val) / (upper_bb_val - lower_bb_val) * 100
                if pct_bb < 25:
                    votes_buy += 1; vote = "BUY"; sig = f"Near Lower BB ({pct_bb:.0f}%)"
                elif pct_bb > 75:
                    votes_sell += 1; vote = "SELL"; sig = f"Near Upper BB ({pct_bb:.0f}%)"
                else:
                    votes_hold += 1; vote = "HOLD"; sig = f"Mid-range ({pct_bb:.0f}%)"
            details.append({"Tool": "Bollinger Bands", "Signal": sig, "Vote": vote})
        except Exception:
            mid_bb = price  # fallback
            details.append({"Tool": "Bollinger Bands", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 2: RSI ---
        try:
            rsi_val = float(rsi(close).iloc[-1])
            if rsi_val < 30:
                votes_buy += 1; vote = "BUY"; sig = f"Oversold RSI={rsi_val:.0f}"
            elif rsi_val < 40:
                votes_buy += 1; vote = "BUY"; sig = f"Weak RSI={rsi_val:.0f}"
            elif rsi_val > 70:
                votes_sell += 1; vote = "SELL"; sig = f"Overbought RSI={rsi_val:.0f}"
            elif rsi_val > 60:
                votes_sell += 1; vote = "SELL"; sig = f"Elevated RSI={rsi_val:.0f}"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral RSI={rsi_val:.0f}"
            details.append({"Tool": "RSI", "Signal": sig, "Vote": vote})
        except Exception:
            rsi_val = 50
            details.append({"Tool": "RSI", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 3: Volume-Price Divergence ---
        try:
            vol = df["volume"]
            price_chg_5d = float((price / close.iloc[-5] - 1) * 100)
            vol_chg_5d = float((vol.iloc[-5:].mean() / vol.iloc[-25:-5].mean() - 1) * 100)
            # For mean reversion: price down + high volume = capitulation (BUY)
            if price_chg_5d < -2 and vol_chg_5d > 30:
                votes_buy += 1; vote = "BUY"; sig = f"Capitulation (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            elif price_chg_5d > 2 and vol_chg_5d < -15:
                votes_sell += 1; vote = "SELL"; sig = f"Weak Rally (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            elif price_chg_5d < -1 and vol_chg_5d < -10:
                votes_buy += 1; vote = "BUY"; sig = f"Exhaustion (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral (P={price_chg_5d:+.1f}% V={vol_chg_5d:+.0f}%)"
            details.append({"Tool": "Volume-Price", "Signal": sig, "Vote": vote})
        except Exception:
            details.append({"Tool": "Volume-Price", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 4: Stochastic Oscillator ---
        try:
            from app.services.technical import stochastic
            pct_k, pct_d = stochastic(df, k_period=14, d_period=3)
            k_val = float(pct_k.iloc[-1])
            d_val = float(pct_d.iloc[-1])
            # Oversold + bullish crossover = BUY
            if k_val < 20 and k_val > d_val:
                votes_buy += 1; vote = "BUY"; sig = f"Oversold Crossover (K={k_val:.0f} D={d_val:.0f})"
            elif k_val < 25:
                votes_buy += 1; vote = "BUY"; sig = f"Oversold (K={k_val:.0f})"
            elif k_val > 80 and k_val < d_val:
                votes_sell += 1; vote = "SELL"; sig = f"Overbought Crossdown (K={k_val:.0f})"
            elif k_val > 75:
                votes_sell += 1; vote = "SELL"; sig = f"Overbought (K={k_val:.0f})"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"Neutral (K={k_val:.0f})"
            details.append({"Tool": "Stochastic", "Signal": sig, "Vote": vote})
        except Exception:
            details.append({"Tool": "Stochastic", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 5: OBV Trend ---
        try:
            from app.services.technical import obv
            obv_series = obv(df)
            obv_now = float(obv_series.iloc[-1])
            obv_10ago = float(obv_series.iloc[-10])
            obv_slope = (obv_now - obv_10ago) / abs(obv_10ago) * 100 if obv_10ago != 0 else 0
            price_chg_10 = float((price / close.iloc[-10] - 1) * 100)
            # OBV rising while price falling = accumulation (bullish divergence)
            if obv_slope > 5 and price_chg_10 < -1:
                votes_buy += 1; vote = "BUY"; sig = f"Accumulation (OBV={obv_slope:+.1f}% P={price_chg_10:+.1f}%)"
            elif obv_slope < -5 and price_chg_10 > 1:
                votes_sell += 1; vote = "SELL"; sig = f"Distribution (OBV={obv_slope:+.1f}% P={price_chg_10:+.1f}%)"
            elif obv_slope > 5:
                votes_buy += 1; vote = "BUY"; sig = f"OBV Rising ({obv_slope:+.1f}%)"
            elif obv_slope < -5:
                votes_sell += 1; vote = "SELL"; sig = f"OBV Falling ({obv_slope:+.1f}%)"
            else:
                votes_hold += 1; vote = "HOLD"; sig = f"OBV Flat ({obv_slope:+.1f}%)"
            details.append({"Tool": "OBV", "Signal": sig, "Vote": vote})
        except Exception:
            details.append({"Tool": "OBV", "Signal": "ERROR", "Vote": "-"})

        # --- Verdict ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        # Mean reversion requires oversold conditions
        if votes_buy >= 4:
            verdict = "STRONG BUY"
        elif votes_buy >= 3 and confidence >= 55:
            verdict = "BUY"
        elif votes_sell >= 4:
            verdict = "STRONG SELL"
        elif votes_sell >= 3 and confidence >= 55:
            verdict = "SELL"
        elif votes_buy > votes_sell:
            verdict = "WEAK BUY"
        elif votes_sell > votes_buy:
            verdict = "WEAK SELL"
        else:
            verdict = "NEUTRAL"

        # Static SL 2%, TP at middle Bollinger Band or +1.5 ATR
        sl = round(price * 0.98, 2)       # 2% static stop loss
        tp1 = round(mid_bb, 2)            # Target: middle BB (mean reversion target)
        tp2 = round(price + 1.5 * atr_val, 2)
        tp3 = round(price + 2.5 * atr_val, 2)

        summary = [{
            "Symbol": symbol.upper(), "Strategy": "B-Mean Reversion",
            "Price": round(price, 2), "Verdict": verdict,
            "Confidence %": confidence,
            "Votes BUY": votes_buy, "Votes SELL": votes_sell, "Votes HOLD": votes_hold,
            "RSI": round(rsi_val, 1) if 'rsi_val' in dir() else "N/A",
            "Stop Loss": sl, "TP1": tp1, "TP2": tp2, "TP3": tp3,
        }]

        if verdict == "STRONG BUY":
            try:
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=f"[B] Reversion: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=f"[B] Reversion: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell, votes_hold=votes_hold,
                    sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, details=details,
                )
            except Exception:
                pass

        if "STRONG" in verdict:
            try:
                trade_result = auto_trade_signal(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, take_profit=tp1,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, details={"strategy": "B"},
                    strategy_id="B",
                )
                if trade_result:
                    summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
            except Exception:
                pass

        return summary + details

    except Exception as e:
        return [{"Error": str(e), "Strategy": "B-Reversion"}]


def run_consensus_ml(symbol, horizon=7, episodes=5):
    """Strategy C: AI Ensemble — pure ML decision-making.

    Tools: LSTM, Transformer, Stacking Ensemble, Double DQN, Policy Gradient
    Entry: 4+ of 5 ML models vote BUY
    Exit: Trailing stop 2.5%, TP at models' average predicted price
    """
    try:
        is_halal, halal_reason = verify_halal(symbol)
        if not is_halal:
            return [{"Symbol": symbol.upper(), "Verdict": "BLOCKED",
                     "Strategy": "C-ML", "Error": f"Not halal: {halal_reason}"}]

        votes_buy, votes_sell, votes_hold = 0, 0, 0
        details = []
        predicted_prices = []

        df = fetch_yf(symbol)
        if df is None:
            return [{"Error": f"No data for {symbol}"}]
        price = float(df["close"].iloc[-1])
        atr_val = float(atr(df).iloc[-1])

        # --- Tool 1: LSTM ---
        try:
            lstm_r = run_lstm(symbol, horizon, df=df)
            if lstm_r and len(lstm_r) > 1:
                last_forecast = lstm_r[-1]
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                    predicted_prices.append(forecast_price)
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "LSTM", "Signal": sig, "Vote": vote})
            else:
                votes_hold += 1
                details.append({"Tool": "LSTM", "Signal": "No forecast", "Vote": "HOLD"})
        except Exception:
            details.append({"Tool": "LSTM", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 2: Transformer ---
        try:
            tf_r = run_transformer(symbol, horizon, df=df)
            if tf_r and len(tf_r) > 1:
                last_forecast = tf_r[-1]
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                    predicted_prices.append(forecast_price)
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "Transformer", "Signal": sig, "Vote": vote})
            else:
                votes_hold += 1
                details.append({"Tool": "Transformer", "Signal": "No forecast", "Vote": "HOLD"})
        except Exception:
            details.append({"Tool": "Transformer", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 3: Stacking Ensemble ---
        try:
            ens_r = run_ensemble(symbol, horizon, df=df)
            if ens_r and len(ens_r) > 1:
                last_forecast = ens_r[-1]
                pct_chg = float(last_forecast.get("Change %", 0))
                forecast_price = float(last_forecast.get("Predicted Price", price))
                if pct_chg > 2:
                    votes_buy += 1; vote = "BUY"
                    predicted_prices.append(forecast_price)
                elif pct_chg < -2:
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"${forecast_price:.2f} ({pct_chg:+.1f}%)"
                details.append({"Tool": "Ensemble", "Signal": sig, "Vote": vote})
            else:
                votes_hold += 1
                details.append({"Tool": "Ensemble", "Signal": "No forecast", "Vote": "HOLD"})
        except Exception:
            details.append({"Tool": "Ensemble", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 4: Double DQN ---
        try:
            dqn_r = run_dqn(symbol, episodes, df=df)
            if dqn_r and len(dqn_r) > 0:
                summary_item = dqn_r[0]
                action = summary_item.get("Action", summary_item.get("Recommendation", ""))
                reward = summary_item.get("Total Reward", summary_item.get("Final Portfolio", 0))
                if "BUY" in str(action).upper():
                    votes_buy += 1; vote = "BUY"
                elif "SELL" in str(action).upper():
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"{action} (R={reward:.1f})" if isinstance(reward, (int, float)) else str(action)
                details.append({"Tool": "DQN", "Signal": sig, "Vote": vote})
            else:
                votes_hold += 1
                details.append({"Tool": "DQN", "Signal": "No action", "Vote": "HOLD"})
        except Exception:
            details.append({"Tool": "DQN", "Signal": "ERROR", "Vote": "-"})

        # --- Tool 5: Policy Gradient ---
        try:
            pg_r = run_policy_gradient(symbol, episodes, df=df)
            if pg_r and len(pg_r) > 0:
                summary_item = pg_r[0]
                action = summary_item.get("Action", summary_item.get("Recommendation", ""))
                reward = summary_item.get("Total Reward", summary_item.get("Final Portfolio", 0))
                if "BUY" in str(action).upper():
                    votes_buy += 1; vote = "BUY"
                elif "SELL" in str(action).upper():
                    votes_sell += 1; vote = "SELL"
                else:
                    votes_hold += 1; vote = "HOLD"
                sig = f"{action} (R={reward:.1f})" if isinstance(reward, (int, float)) else str(action)
                details.append({"Tool": "PolicyGrad", "Signal": sig, "Vote": vote})
            else:
                votes_hold += 1
                details.append({"Tool": "PolicyGrad", "Signal": "No action", "Vote": "HOLD"})
        except Exception:
            details.append({"Tool": "PolicyGrad", "Signal": "ERROR", "Vote": "-"})

        # --- Verdict ---
        total = votes_buy + votes_sell + votes_hold
        if total == 0: total = 1
        confidence = round(max(votes_buy, votes_sell) / total * 100, 1)

        if votes_buy >= 4:
            verdict = "STRONG BUY"
        elif votes_buy >= 3 and confidence >= 55:
            verdict = "BUY"
        elif votes_sell >= 4:
            verdict = "STRONG SELL"
        elif votes_sell >= 3 and confidence >= 55:
            verdict = "SELL"
        elif votes_buy > votes_sell:
            verdict = "WEAK BUY"
        elif votes_sell > votes_buy:
            verdict = "WEAK SELL"
        else:
            verdict = "NEUTRAL"

        # TP at average ML predicted price or 2x ATR
        if predicted_prices:
            tp1 = round(sum(predicted_prices) / len(predicted_prices), 2)
        else:
            tp1 = round(price + 2.0 * atr_val, 2)
        sl = round(price - 1.5 * atr_val, 2)
        tp2 = round(price + 3.0 * atr_val, 2)
        tp3 = round(price + 5.0 * atr_val, 2)

        summary = [{
            "Symbol": symbol.upper(), "Strategy": "C-AI Ensemble",
            "Price": round(price, 2), "Verdict": verdict,
            "Confidence %": confidence,
            "Votes BUY": votes_buy, "Votes SELL": votes_sell, "Votes HOLD": votes_hold,
            "ML Predicted TP": tp1 if predicted_prices else "N/A",
            "Stop Loss": sl, "TP1": tp1, "TP2": tp2, "TP3": tp3,
        }]

        if verdict == "STRONG BUY":
            try:
                alert_signal_with_chart(
                    symbol=symbol.upper(), verdict=f"[C] AI: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, df=df,
                )
                _send_consensus_breakdown(
                    symbol=symbol.upper(), verdict=f"[C] AI: {verdict}",
                    confidence=confidence, price=round(price, 2),
                    votes_buy=votes_buy, votes_sell=votes_sell, votes_hold=votes_hold,
                    sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, details=details,
                )
            except Exception:
                pass

        if "STRONG" in verdict:
            try:
                trade_result = auto_trade_signal(
                    symbol=symbol.upper(), verdict=verdict,
                    confidence=confidence, price=round(price, 2),
                    stop_loss=sl, take_profit=tp1,
                    votes_buy=votes_buy, votes_sell=votes_sell,
                    votes_hold=votes_hold, details={"strategy": "C"},
                    strategy_id="C",
                )
                if trade_result:
                    summary[0]["Auto_Trade"] = "EXECUTED" if trade_result.get("executed") else "REJECTED"
            except Exception:
                pass

        return summary + details

    except Exception as e:
        return [{"Error": str(e), "Strategy": "C-ML"}]


def run_batch_consensus(min_swing_score=55, horizon=5, episodes=5, max_stocks=10):
    try:
        # الخطوة 1: جلب Active Buy Signals
        screener_results = run_screener()
        buy_signals = [r for r in screener_results if r.get("swing_score", 0) >= min_swing_score]
        
        if not buy_signals:
            return [{"Message": "No active buy signals found"}]
        
        # حد أقصى للسرعة
        buy_signals = buy_signals[:max_stocks]
        
        summary_header = [{
            "Info": f"Scanning {len(buy_signals)} stocks from Active Buy Signals",
            "Min Score": min_swing_score,
            "Horizon": f"{horizon} days",
            "Status": "Running..."
        }]
        
        results = []
        for stock in buy_signals:
            symbol = stock["symbol"]
            try:
                consensus = run_consensus(symbol, horizon=horizon, episodes=episodes)
                if consensus and len(consensus) > 0:
                    row = consensus[0].copy()
                    row["Swing Score"] = stock.get("swing_score", 0)
                    row["ATR Pct"] = stock.get("atr_pct", 0)
                    results.append(row)
            except Exception as e:
                results.append({
                    "Symbol": symbol,
                    "Verdict": "ERROR",
                    "Confidence %": 0,
                    "Votes BUY": 0,
                    "Votes SELL": 0,
                    "Votes HOLD": 0,
                    "Action": f"Error: {str(e)[:50]}",
                    "Swing Score": stock.get("swing_score", 0),
                })
        
        # ترتيب حسب الثقة والتصويت
        results.sort(key=lambda x: (
            x.get("Votes BUY", 0) - x.get("Votes SELL", 0),
            x.get("Confidence %", 0)
        ), reverse=True)
        
        return summary_header + results

    except Exception as e:
        return [{"Error": str(e)}]


# Pipeline uses same S&P 500 halal universe
RUSSELL_1000_HALAL = HALAL_STOCKS

def _quick_filter_one(symbol):
    """Quick filter for pipeline stage 1."""
    try:
        df = fetch_yf(symbol)
        if df is None: return None
        close = df["close"]
        ema21_val = float(ema(close, 21).iloc[-1])
        ema9_val  = float(ema(close, 9).iloc[-1])
        rsi_val   = float(rsi(close).iloc[-1])
        vol_ma    = float(df["volume"].rolling(20).mean().iloc[-1])
        vol_ratio = float(df["volume"].iloc[-1] / vol_ma) if vol_ma > 0 else 0
        price     = float(close.iloc[-1])
        score = sum([price > ema21_val, ema9_val > ema21_val, 30 <= rsi_val <= 75, vol_ratio >= 0.8])
        if score >= 2:
            return {"symbol": symbol, "price": round(price,2), "rsi": round(rsi_val,1), "quick_score": score}
    except Exception as e:
        logger.debug(f"Quick filter {symbol}: {e}")
    return None

def run_pipeline(min_confidence=40, max_final=15, horizon=5, episodes=5):
    try:
        # Stage 1: Quick filter (concurrent)
        quick_results = []
        with ThreadPoolExecutor(max_workers=settings.SCREENER_WORKERS) as pool:
            futures = {pool.submit(_quick_filter_one, s): s for s in RUSSELL_1000_HALAL}
            for f in as_completed(futures):
                r = f.result()
                if r: quick_results.append(r)
        quick_results.sort(key=lambda x: x["quick_score"], reverse=True)
        top100 = quick_results[:100]

        # المرحلة 2: Halal Screener
        screener_results = []
        for item in top100:
            try:
                df = fetch_yf(item["symbol"])
                if df is None: continue
                r = analyze(item["symbol"], df)
                if r and r["swing_score"] >= 45:
                    screener_results.append(r)
            except Exception as e:
                logger.debug(f"Pipeline screener {item['symbol']}: {e}")
                continue
        screener_results.sort(key=lambda x: x["swing_score"], reverse=True)
        top30 = screener_results[:30]

        # المرحلة 3: USX Pro
        top30_symbols = [r["symbol"] for r in top30]
        usx_filtered = []
        try:
            usx_all = run_usx_screener(min_score=5)
            usx_filtered = [x for x in usx_all if x.get("Symbol") in top30_symbols]
            usx_filtered.sort(key=lambda x: int(x.get("Score","0/10").split("/")[0]), reverse=True)
            top15 = [x["Symbol"] for x in usx_filtered[:15]]
        except Exception as e:
            logger.warning(f"Pipeline USX stage failed: {e}")
            top15 = top30_symbols[:15]

        # المرحلة 4: AI Consensus
        final_results = []
        for symbol in top15:
            try:
                consensus = run_consensus(symbol, horizon=horizon, episodes=episodes)
                if consensus and len(consensus) > 0:
                    row = consensus[0].copy()
                    screener_info = next((r for r in screener_results if r["symbol"] == symbol), {})
                    usx_info = next((r for r in usx_filtered if r.get("Symbol") == symbol), {})
                    row["Swing Score"] = screener_info.get("swing_score", 0)
                    row["USX Score"]   = usx_info.get("Score", "N/A")
                    row["RSI"]         = screener_info.get("rsi", 0)
                    row["Signals"]     = screener_info.get("signals", "")
                    final_results.append(row)
            except Exception as e:
                final_results.append({"Symbol": symbol, "Verdict": "ERROR", "Confidence %": 0, "Action": str(e)[:30], "Swing Score": 0})

        final_results.sort(key=lambda x: (
            x.get("Votes BUY", 0) - x.get("Votes SELL", 0),
            x.get("Confidence %", 0)
        ), reverse=True)

        strong_buy = [r for r in final_results if "STRONG BUY" in r.get("Verdict","")]
        buy        = [r for r in final_results if r.get("Verdict","") == "BUY"]
        weak_buy   = [r for r in final_results if r.get("Verdict","") == "WEAK BUY"]

        header = [{
            "Pipeline":         "Russell 1000 Halal → Full AI Pipeline",
            "Total Scanned":    len(RUSSELL_1000_HALAL),
            "After Quick Filter": len(top100),
            "After Screener":   len(top30),
            "After USX Pro":    len(top15),
            "Final Results":    len(final_results),
            "STRONG BUY":       len(strong_buy),
            "BUY":              len(buy),
            "WEAK BUY":         len(weak_buy),
            "Best Stock":       final_results[0]["Symbol"] if final_results else "None",
            "Best Confidence":  f'{final_results[0].get("Confidence %", 0)}%' if final_results else "0%",
            "Best Action":      final_results[0].get("Action","") if final_results else "",
        }]

        return header + final_results

    except Exception as e:
        return [{"Error": str(e)}]

@app.get("/widgets.json")
async def widgets():
    return {
        # ===== ROW 1: Portfolio Overview (top bar) =====
        "portfolio_summary": {
            "name": "Portfolio Summary",
            "description": "Equity, cash, buying power, daily P&L",
            "category": "Portfolio", "type": "table",
            "endpoint": "/portfolio/summary",
            "gridData": {"w": 10, "h": 4},
        },
        "open_positions": {
            "name": "Open Positions",
            "description": "Current holdings with unrealized P&L",
            "category": "Portfolio", "type": "table",
            "endpoint": "/portfolio/positions",
            "gridData": {"w": 10, "h": 4},
        },

        # ===== ROW 2: Buy Signals + Screener (main trading view) =====
        "buy_signals": {
            "name": "Active Buy Signals",
            "description": "Halal stocks with swing score >= 55",
            "category": "Screener", "type": "table",
            "endpoint": "/buys",
            "gridData": {"w": 10, "h": 9},
        },
        "usx_pro": {
            "name": "USX Pro Screener",
            "description": "10-point swing system (top picks only)",
            "category": "Screener", "type": "table",
            "endpoint": "/usx",
            "gridData": {"w": 10, "h": 9},
            "params": [{"paramName": "min_score", "value": "7", "label": "Min Score (1-10)", "type": "text", "show": True}],
        },

        # ===== ROW 3: Deep Analysis (single stock) =====
        "ai_consensus": {
            "name": "AI Consensus (9 Tools)",
            "description": "Combined verdict from all AI models",
            "category": "Analysis", "type": "table",
            "endpoint": "/consensus",
            "gridData": {"w": 10, "h": 9},
            "params": [
                {"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                {"paramName": "horizon", "value": "5", "label": "Forecast Days", "type": "text", "show": True},
                {"paramName": "episodes", "value": "10", "label": "RL Episodes", "type": "text", "show": True},
            ],
        },
        "halal_check": {
            "name": "AAOIFI Halal Check",
            "description": "Sharia compliance: debt, interest, liquidity ratios",
            "category": "Analysis", "type": "table",
            "endpoint": "/halal_status",
            "gridData": {"w": 10, "h": 5},
            "params": [{"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True}],
        },

        # ===== ROW 4: Backtest & Forecast =====
        "backtest": {
            "name": "Backtest + Risk Metrics",
            "description": "Historical strategy test: Sharpe, Sortino, VaR, drawdown",
            "category": "Analysis", "type": "table",
            "endpoint": "/backtest",
            "gridData": {"w": 10, "h": 9},
            "params": [
                {"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                {"paramName": "start_date", "value": "2022-01-01", "label": "Start Date", "type": "text", "show": True},
                {"paramName": "end_date", "value": "2024-12-31", "label": "End Date", "type": "text", "show": True},
            ],
        },
        "monte_carlo": {
            "name": "Monte Carlo Forecast",
            "description": "1000 simulated price paths with probabilities",
            "category": "Analysis", "type": "table",
            "endpoint": "/monte_carlo",
            "gridData": {"w": 10, "h": 9},
            "params": [
                {"paramName": "symbol", "value": "AAPL", "label": "Symbol", "type": "text", "show": True},
                {"paramName": "days", "value": "30", "label": "Days", "type": "text", "show": True},
                {"paramName": "simulations", "value": "1000", "label": "Simulations", "type": "text", "show": True},
            ],
        },

        # ===== ROW 5: Signal Tracking =====
        "signal_accuracy": {
            "name": "Signal Accuracy Report",
            "description": "Win rates, avg returns, profit factor by source",
            "category": "Analytics", "type": "table",
            "endpoint": "/signals/accuracy",
            "gridData": {"w": 10, "h": 6},
            "params": [{"paramName": "period", "value": "30", "label": "Lookback Days", "type": "text", "show": True}],
        },
        "signal_history": {
            "name": "Signal History",
            "description": "Recorded signals with outcomes (WIN/LOSS)",
            "category": "Analytics", "type": "table",
            "endpoint": "/signals/history",
            "gridData": {"w": 10, "h": 6},
            "params": [{"paramName": "symbol", "value": "", "label": "Symbol (blank=all)", "type": "text", "show": True}],
        },

        # ===== EGX: Egyptian Exchange (separate section) =====
        "egx_watchlist": {
            "name": "EGX Watchlist",
            "description": "Uploaded Egyptian stocks with data status",
            "category": "EGX Egypt", "type": "table",
            "endpoint": "/egx/watchlist",
            "gridData": {"w": 10, "h": 5},
        },
        "egx_analyze": {
            "name": "EGX Analysis (7 Tools)",
            "description": "V9 Score + Bollinger + StochRSI + OBV + ADX + Backtest + Monte Carlo",
            "category": "EGX Egypt", "type": "table",
            "endpoint": "/egx/analyze",
            "gridData": {"w": 10, "h": 9},
            "params": [{"paramName": "symbol", "value": "COMI", "label": "EGX Symbol", "type": "text", "show": True}],
        },
        "egx_scan": {
            "name": "EGX Full Scan",
            "description": "Scan all uploaded Egyptian stocks for signals",
            "category": "EGX Egypt", "type": "table",
            "endpoint": "/egx/scan",
            "gridData": {"w": 10, "h": 9},
        },
        "egx_backtest": {
            "name": "EGX Backtest (V9)",
            "description": "ATR-based backtest with win rate, profit factor, expectancy",
            "category": "EGX Egypt", "type": "table",
            "endpoint": "/egx/backtest",
            "gridData": {"w": 10, "h": 9},
            "params": [{"paramName": "symbol", "value": "COMI", "label": "EGX Symbol", "type": "text", "show": True}],
        },
        "egx_optimize": {
            "name": "EGX Optimizer",
            "description": "Find best SL/TP/Score/Volume params",
            "category": "EGX Egypt", "type": "table",
            "endpoint": "/egx/optimize",
            "gridData": {"w": 10, "h": 6},
            "params": [{"paramName": "symbol", "value": "COMI", "label": "EGX Symbol", "type": "text", "show": True}],
        },
        "egx_signals": {
            "name": "EGX Signal History",
            "description": "Past EGX signals with V9 scores and tool votes",
            "category": "EGX Egypt", "type": "table",
            "endpoint": "/egx/signals",
            "gridData": {"w": 10, "h": 6},
            "params": [{"paramName": "symbol", "value": "", "label": "Symbol (blank=all)", "type": "text", "show": True}],
        },
    }

# ============================================================
# UNIFIED BACKGROUND CACHE — ALL endpoints serve cached results
# OpenBB Pro has ~30s widget timeout. No endpoint should ever
# make it wait. Everything computes in background, serves instantly.
# ============================================================
_bg_cache = {}          # {"endpoint_key": result}
_cache_status = {}   # {"endpoint_key": "idle"|"running"|"done"}
_cache_time = {}     # {"endpoint_key": timestamp}
_cache_lock = threading.Lock()
_BG_CACHE_TTL = settings.BG_CACHE_TTL

def _cache_key(endpoint, **params):
    parts = [endpoint] + [f"{k}={v}" for k, v in sorted(params.items())]
    return "|".join(parts)

def _get_cached(key):
    with _cache_lock:
        cached = _bg_cache.get(key)
        status = _cache_status.get(key, "idle")
        ts = _cache_time.get(key, 0)
    if cached and (time.time() - ts) > _BG_CACHE_TTL:
        return cached, "stale"
    return cached, status

def _bg_compute(key, func, args=(), kwargs=None):
    with _cache_lock:
        if _cache_status.get(key) == "running":
            return
        _cache_status[key] = "running"
    try:
        result = func(*args, **(kwargs or {}))
        with _cache_lock:
            _bg_cache[key] = result
            _cache_status[key] = "done"
            _cache_time[key] = time.time()
    except Exception as e:
        logger.error(f"Background compute {key} failed: {e}")
        with _cache_lock:
            _bg_cache[key] = [{"Error": str(e)}]
            _cache_status[key] = "done"
            _cache_time[key] = time.time()

def _serve_or_compute(key, func, args=(), kwargs=None, msg="Computing in background..."):
    """Return cached result instantly, or start background compute and return status message."""
    cached, status = _get_cached(key)
    if status == "stale":
        threading.Thread(target=_bg_compute, args=(key, func, args, kwargs), daemon=True).start()
        return cached  # serve stale while refreshing
    if cached:
        return cached
    if status != "running":
        threading.Thread(target=_bg_compute, args=(key, func, args, kwargs), daemon=True).start()
    return [{"Status": msg, "Info": "Data is being computed. Refresh the widget in 1-3 minutes."}]

# --- Screener-based endpoints (screener, buys, watchlist) ---
@app.get("/screener")
async def screener():
    key = _cache_key("screener")
    return _serve_or_compute(key, run_screener, msg="Computing halal screener...")

@app.get("/buys")
async def buys():
    key = _cache_key("screener")
    cached, status = _get_cached(key)
    if cached and isinstance(cached, list):
        return [r for r in cached if r.get("swing_score", 0) >= 55]
    if status != "running":
        threading.Thread(target=_bg_compute, args=(key, run_screener), daemon=True).start()
    return [{"Status": "Computing buy signals...", "Info": "Refresh in 1-2 minutes."}]

@app.get("/watchlist")
async def watchlist():
    key = _cache_key("screener")
    cached, status = _get_cached(key)
    if cached and isinstance(cached, list):
        return [r for r in cached if 35 <= r.get("swing_score", 0) < 55]
    if status != "running":
        threading.Thread(target=_bg_compute, args=(key, run_screener), daemon=True).start()
    return [{"Status": "Computing watchlist...", "Info": "Refresh in 1-2 minutes."}]

# --- Single-symbol endpoints ---
@app.get("/backtest")
async def backtest(symbol: str = "AAPL", start_date: str = "2022-01-01", end_date: str = "2024-12-31", portfolio: float = 100000, risk_pct: float = 1.0, hold_days: int = 3):
    s = validate_symbol(symbol)
    validate_date(start_date); validate_date(end_date)
    validate_range(portfolio, "portfolio", 1000, 10_000_000)
    validate_range(risk_pct, "risk_pct", 0.1, 10.0)
    validate_range(hold_days, "hold_days", 1, 60)
    key = _cache_key("backtest", symbol=s, start=start_date, end=end_date, portfolio=portfolio, risk=risk_pct, hold=hold_days)
    return _serve_or_compute(key, run_backtest, args=(s, start_date, end_date, portfolio, risk_pct, hold_days), msg=f"Computing backtest for {s}...")

@app.get("/monte_carlo")
async def monte_carlo(symbol: str = "AAPL", days: int = 30, simulations: int = 1000):
    s = validate_symbol(symbol)
    validate_range(days, "days", 1, 365)
    validate_range(simulations, "simulations", 100, 10000)
    key = _cache_key("monte_carlo", symbol=s, days=days, sims=simulations)
    return _serve_or_compute(key, run_monte_carlo, args=(s, days, simulations), msg=f"Running Monte Carlo for {s}...")

@app.get("/lstm")
async def lstm(symbol: str = "AAPL", horizon: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    if not check_rate_limit("lstm", 3):
        return [{"Status": "Rate limited", "Info": "Too many LSTM requests. Try again in 1 minute."}]
    key = _cache_key("lstm", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_lstm, args=(s, horizon), msg=f"Running LSTM for {s}...")

@app.get("/transformer")
async def transformer(symbol: str = "AAPL", horizon: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    if not check_rate_limit("transformer", 3):
        return [{"Status": "Rate limited", "Info": "Too many Transformer requests. Try again in 1 minute."}]
    key = _cache_key("transformer", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_transformer, args=(s, horizon), msg=f"Running Transformer for {s}...")

@app.get("/ensemble")
async def ensemble(symbol: str = "AAPL", horizon: int = 5):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("ensemble", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_ensemble, args=(s, horizon), msg=f"Running Ensemble for {s}...")

@app.get("/dqn")
async def dqn(symbol: str = "AAPL", episodes: int = 20):
    s = validate_symbol(symbol)
    validate_range(episodes, "episodes", 1, 100)
    if not check_rate_limit("dqn", 2):
        return [{"Status": "Rate limited", "Info": "Too many DQN requests. Try again in 1 minute."}]
    key = _cache_key("dqn", symbol=s, episodes=episodes)
    return _serve_or_compute(key, run_dqn, args=(s, episodes), msg=f"Running DQN for {s}...")

@app.get("/policy_gradient")
async def policy_gradient(symbol: str = "AAPL", episodes: int = 20):
    s = validate_symbol(symbol)
    validate_range(episodes, "episodes", 1, 100)
    if not check_rate_limit("policy_gradient", 2):
        return [{"Status": "Rate limited", "Info": "Too many Policy Gradient requests. Try again in 1 minute."}]
    key = _cache_key("policy_gradient", symbol=s, episodes=episodes)
    return _serve_or_compute(key, run_policy_gradient, args=(s, episodes), msg=f"Running Policy Gradient for {s}...")

@app.get("/usx")
async def usx(min_score: int = 7):
    validate_range(min_score, "min_score", 1, 10)
    key = _cache_key("usx", min_score=min_score)
    return _serve_or_compute(key, run_usx_screener, args=(min_score,), msg="Computing USX screener...")

@app.get("/consensus")
async def consensus(symbol: str = "AAPL", horizon: int = 5, episodes: int = 10):
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    validate_range(episodes, "episodes", 1, 50)
    # No rate limiter — the background cache already deduplicates work
    key = _cache_key("consensus", symbol=s, horizon=horizon, episodes=episodes)
    return _serve_or_compute(key, run_consensus, args=(s, horizon, episodes), msg=f"Computing AI consensus for {s}...")

@app.get("/batch_consensus")
async def batch_consensus(min_swing_score: int = 55, horizon: int = 5, episodes: int = 5, max_stocks: int = 10):
    key = _cache_key("batch_consensus", min=min_swing_score, h=horizon, ep=episodes, max=max_stocks)
    return _serve_or_compute(key, run_batch_consensus, args=(min_swing_score, horizon, episodes, max_stocks), msg="Computing batch consensus...")

@app.get("/pipeline")
async def pipeline(min_confidence: int = 40, max_final: int = 15, horizon: int = 5, episodes: int = 5):
    key = _cache_key("pipeline", conf=min_confidence, max=max_final, h=horizon, ep=episodes)
    return _serve_or_compute(key, run_pipeline, args=(min_confidence, max_final, horizon, episodes), msg="Computing full pipeline...")

# --- Refresh endpoints (clear cache + recompute) ---
@app.get("/refresh_consensus")
async def refresh_consensus(symbol: str = "AAPL"):
    s = validate_symbol(symbol)
    key = _cache_key("consensus", symbol=s, horizon=5, episodes=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    threading.Thread(target=_bg_compute, args=(key, run_consensus, (s, 5, 10)), daemon=True).start()
    return {"Status": f"Consensus refresh started for {s}."}

@app.get("/refresh_batch")
async def refresh_batch():
    key = _cache_key("batch_consensus", min=55, h=5, ep=5, max=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    threading.Thread(target=_bg_compute, args=(key, run_batch_consensus, (55, 5, 5, 10)), daemon=True).start()
    return [{"Status": "Batch consensus refresh started"}]

@app.get("/refresh_pipeline")
async def refresh_pipeline():
    key = _cache_key("pipeline", conf=40, max=15, h=5, ep=5)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    threading.Thread(target=_bg_compute, args=(key, run_pipeline, (40, 15, 5, 5)), daemon=True).start()
    return [{"Status": "Pipeline refresh started"}]

# --- Pre-compute screener + USX + consensus on startup so widgets load instantly ---
def _precompute_consensus_for_top_buys():
    """After screener finishes, pre-warm consensus for top buy signals."""
    # Wait for screener cache to be ready (poll every 10s, max 5 min)
    screener_key = _cache_key("screener")
    for _ in range(30):
        cached, status = _get_cached(screener_key)
        if cached and isinstance(cached, list) and len(cached) > 0:
            break
        time.sleep(10)
    else:
        return  # screener never finished
    # Get top 1 buy signal and pre-warm its consensus (limit to 1 to avoid OOM on small containers)
    buys = [r for r in cached if r.get("swing_score", 0) >= 55][:1]
    for stock in buys:
        sym = stock["symbol"]
        key = _cache_key("consensus", symbol=sym, horizon=5, episodes=10)
        logger.info(f"Pre-warming consensus for {sym}...")
        _bg_compute(key, run_consensus, (sym, 5, 10))

@app.on_event("startup")
async def precompute_on_startup():
    # Initialize database tables
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.warning(f"Database init failed (non-fatal, using in-memory caches): {e}")
    logger.info("Pre-computing screener and USX data on startup...")
    threading.Thread(target=_bg_compute, args=(_cache_key("screener"), run_screener), daemon=True).start()
    # Wait for screener to finish before starting USX to avoid memory pressure
    def _delayed_usx():
        screener_key = _cache_key("screener")
        for _ in range(60):
            cached, status = _get_cached(screener_key)
            if cached and isinstance(cached, list) and len(cached) > 0:
                break
            time.sleep(5)
        _bg_compute(_cache_key("usx", min_score=7), run_usx_screener, (7,))
    threading.Thread(target=_delayed_usx, daemon=True).start()

    # Start the automated scheduler (scans every 30 min during market hours)
    try:
        from app.services.scheduler import start_scheduler
        start_scheduler()
        logger.info("Automated scheduler started successfully")
    except Exception as e:
        logger.warning(f"Scheduler failed to start (non-fatal): {e}")

# ============================================================
# PHASE 2: AAOIFI Halal Screening Endpoints
# ============================================================

@app.get("/halal_status")
async def halal_status(symbol: str = "AAPL"):
    """Get AAOIFI Halal compliance status for a single symbol."""
    s = validate_symbol(symbol)
    if not settings.FMP_API_KEY:
        return [{"Error": "FMP_API_KEY not configured. Get a free key at https://site.financialmodelingprep.com/developer/docs"}]
    result = get_halal_status(s)
    if result is None:
        return [{"Error": f"Could not retrieve fundamental data for {s}"}]
    # Format for OpenBB widget display
    status = "HALAL" if result.get("is_halal") else "HARAM"
    return [{
        "Symbol": s,
        "Status": status,
        "Company": result.get("company_name", s),
        "Sector": result.get("sector", ""),
        "Debt / MCap %": result.get("debt_ratio", 0),
        "Debt Pass": "YES" if result.get("debt_pass") else "NO",
        "Interest / Rev %": result.get("interest_ratio", 0),
        "Interest Pass": "YES" if result.get("interest_pass") else "NO",
        "Haram Sector": "YES" if result.get("haram_revenue") else "NO",
        "Sector Pass": "YES" if result.get("haram_pass") else "NO",
        "Liquidity / MCap %": result.get("liquidity_ratio", 0),
        "Liquidity Pass": "YES" if result.get("liquidity_pass") else "NO",
        "Screens Passed": f"{result.get('screens_passed', 0)}/4",
        "Threshold": "Debt<33%, Interest<5%, No Haram Sector, Liquidity<33%",
    }]

@app.get("/halal_verify/{symbol}")
async def halal_verify(symbol: str):
    """Quick halal verification gate check for any symbol.

    Returns whether the stock is allowed for trading.
    Uses the 3-layer defense: sector exclusion → FMP AAOIFI → fail-closed.
    """
    s = validate_symbol(symbol)
    is_halal, reason = verify_halal(s)
    return [{
        "Symbol": s,
        "Allowed": is_halal,
        "Status": "HALAL - OK TO TRADE" if is_halal else "BLOCKED",
        "Reason": reason,
        "Gate": "verify_halal()",
    }]


@app.get("/halal_blocked")
async def halal_blocked():
    """List all stocks currently blocked by the halal verification gate."""
    blocked = []
    for sym in sorted(_VERIFIED_HARAM):
        blocked.append({"Symbol": sym, "Reason": "Verified haram (AAOIFI)"})
    for sym in sorted(_HARAM_EXCLUDE):
        blocked.append({"Symbol": sym, "Reason": "Sector exclusion (banks/insurance/alcohol/etc)"})
    return blocked if blocked else [{"Message": "No stocks blocked yet (gate runs on first access)"}]


@app.get("/screening_report")
async def screening_report():
    """Get all AAOIFI screening results from the database."""
    results = get_screening_report()
    if not results:
        return [{"Message": "No screening results yet. Use /screen_stocks to start screening, or /halal_status/{symbol} for a single stock."}]
    return results

@app.get("/screen_stocks")
async def screen_stocks_endpoint(max_stocks: int = 80):
    """Trigger batch AAOIFI screening for all stocks in HALAL_STOCKS + RUSSELL_1000_HALAL."""
    if not settings.FMP_API_KEY:
        return [{"Error": "FMP_API_KEY not configured. Get a free key at https://site.financialmodelingprep.com/developer/docs"}]
    validate_range(max_stocks, "max_stocks", 1, 200)
    # Combine all known symbols, deduplicate
    all_symbols = list(set(HALAL_STOCKS + RUSSELL_1000_HALAL))
    # Run in background to avoid timeout
    key = _cache_key("screening_batch", max=max_stocks)
    def _run_batch():
        return [batch_screen(all_symbols, max_per_run=max_stocks)]
    return _serve_or_compute(key, _run_batch, msg=f"Screening up to {max_stocks} stocks (3 API calls each)... Refresh in a few minutes.")


# ============================================================
# MULTI-STRATEGY ENDPOINTS
# ============================================================

@app.get("/strategies")
async def list_strategies():
    """List all configured strategies and their settings."""
    from app.config import STRATEGY_CONFIGS
    result = []
    for sid, cfg in STRATEGY_CONFIGS.items():
        result.append({
            "id": cfg.strategy_id,
            "name": cfg.name,
            "max_positions": cfg.max_positions,
            "position_pct": cfg.position_pct,
            "trailing_stop": cfg.trailing_stop_enabled,
            "trailing_stop_pct": cfg.trailing_stop_pct,
            "static_sl_pct": cfg.static_sl_pct,
            "min_confidence": cfg.min_confidence,
            "configured": bool(cfg.alpaca_api_key),
        })
    return result if result else [{"message": "No strategies configured. Set ALPACA_API_KEY_A/B/C env vars."}]


@app.get("/strategy/{strategy_id}/account")
async def strategy_account(strategy_id: str):
    """Get account info for a specific strategy."""
    sid = strategy_id.upper()
    from app.config import STRATEGY_CONFIGS
    if sid not in STRATEGY_CONFIGS:
        return [{"Error": f"Strategy {sid} not configured"}]
    account = alpaca_get_account(strategy_id=sid)
    if not account:
        return [{"Error": f"Cannot fetch account for strategy {sid}"}]
    account["strategy"] = f"{sid}: {STRATEGY_CONFIGS[sid].name}"
    return [account]


@app.get("/strategy/{strategy_id}/positions")
async def strategy_positions(strategy_id: str):
    """Get positions for a specific strategy."""
    sid = strategy_id.upper()
    from app.config import STRATEGY_CONFIGS
    if sid not in STRATEGY_CONFIGS:
        return [{"Error": f"Strategy {sid} not configured"}]
    positions = alpaca_get_positions(strategy_id=sid)
    if not positions:
        return [{"message": f"No open positions for strategy {sid}: {STRATEGY_CONFIGS[sid].name}"}]
    for p in positions:
        p["strategy"] = f"{sid}: {STRATEGY_CONFIGS[sid].name}"
    return positions


@app.get("/strategy/{strategy_id}/scan")
async def strategy_scan(strategy_id: str, symbol: str = "AAPL"):
    """Manually run a strategy consensus for a symbol."""
    sid = strategy_id.upper()
    if sid == "A":
        return run_consensus_momentum(symbol)
    elif sid == "B":
        return run_consensus_reversion(symbol)
    elif sid == "C":
        return run_consensus_ml(symbol)
    else:
        return [{"Error": f"Unknown strategy {sid}. Use A, B, or C."}]


@app.get("/strategies/comparison")
async def strategy_comparison():
    """Get side-by-side comparison of all strategy accounts."""
    from app.config import STRATEGY_CONFIGS
    result = []
    total_equity = 0
    total_pnl = 0
    for sid in ("A", "B", "C"):
        cfg = STRATEGY_CONFIGS.get(sid)
        if not cfg:
            continue
        account = alpaca_get_account(strategy_id=sid)
        positions = alpaca_get_positions(strategy_id=sid)
        if account:
            equity = account.get("equity", 0)
            last_eq = account.get("last_equity", 0)
            pnl = equity - last_eq if last_eq > 0 else 0
            total_equity += equity
            total_pnl += pnl
            result.append({
                "strategy_id": sid,
                "name": cfg.name,
                "equity": round(equity, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / last_eq * 100, 2) if last_eq > 0 else 0,
                "positions": len(positions),
                "max_positions": cfg.max_positions,
                "position_symbols": [p["symbol"] for p in positions],
            })
    if result:
        result.append({
            "total_equity": round(total_equity, 2),
            "total_pnl": round(total_pnl, 2),
        })
    return result if result else [{"message": "No strategies configured"}]


# ============================================================
# PHASE 3: Alpaca Portfolio Integration (Read-Only)
# ============================================================

@app.get("/portfolio/summary")
async def portfolio_summary():
    """Get Alpaca account summary: equity, cash, buying power, P&L."""
    # Use default key, or fall back to Strategy A key
    has_key = settings.ALPACA_API_KEY or settings.ALPACA_API_KEY_A
    if not has_key:
        return [{"Error": "Alpaca API keys not configured"}]
    sid = "A" if settings.ALPACA_API_KEY_A else None
    account = alpaca_get_account(strategy_id=sid)
    if not account:
        return [{"Error": "Could not connect to Alpaca"}]
    # Calculate daily P&L
    daily_pl = account["equity"] - account["last_equity"]
    daily_pl_pct = (daily_pl / account["last_equity"] * 100) if account["last_equity"] > 0 else 0
    return [{
        "Equity": f"${account['equity']:,.2f}",
        "Cash": f"${account['cash']:,.2f}",
        "Buying Power": f"${account['buying_power']:,.2f}",
        "Portfolio Value": f"${account['portfolio_value']:,.2f}",
        "Long Mkt Value": f"${account['long_market_value']:,.2f}",
        "Daily P&L": f"${daily_pl:+,.2f}",
        "Daily P&L %": f"{daily_pl_pct:+.2f}%",
        "Day Trades": account["daytrade_count"],
        "Status": account["status"],
        "Currency": account["currency"],
    }]

@app.get("/portfolio/positions")
async def portfolio_positions():
    """Get all open positions with unrealized P&L."""
    has_key = settings.ALPACA_API_KEY or settings.ALPACA_API_KEY_A
    if not has_key:
        return [{"Error": "Alpaca API keys not configured"}]
    sid = "A" if settings.ALPACA_API_KEY_A else None
    positions = alpaca_get_positions(strategy_id=sid)
    if not positions:
        return [{"Message": "No open positions"}]
    result = []
    for p in positions:
        result.append({
            "Symbol": p["symbol"],
            "Qty": p["qty"],
            "Side": p["side"].upper(),
            "Avg Entry": f"${p['avg_entry_price']:.2f}",
            "Current": f"${p['current_price']:.2f}",
            "Market Value": f"${p['market_value']:,.2f}",
            "Cost Basis": f"${p['cost_basis']:,.2f}",
            "Unrealized P&L": f"${p['unrealized_pl']:+,.2f}",
            "P&L %": f"{p['unrealized_plpc']:+.2f}%",
            "Today %": f"{p['change_today']:+.2f}%",
        })
    return result

@app.get("/portfolio/orders")
async def portfolio_orders(status: str = "all", limit: int = 20):
    """Get recent Alpaca orders."""
    has_key = settings.ALPACA_API_KEY or settings.ALPACA_API_KEY_A
    if not has_key:
        return [{"Error": "Alpaca API keys not configured"}]
    sid = "A" if settings.ALPACA_API_KEY_A else None
    orders = alpaca_get_orders(status=status, limit=limit, strategy_id=sid)
    if not orders:
        return [{"Message": "No orders found"}]
    return orders

@app.get("/portfolio/history")
async def portfolio_history(period: str = "1M"):
    """Get portfolio equity curve history."""
    has_key = settings.ALPACA_API_KEY or settings.ALPACA_API_KEY_A
    if not has_key:
        return [{"Error": "Alpaca API keys not configured"}]
    sid = "A" if settings.ALPACA_API_KEY_A else None
    valid_periods = ["1D", "1W", "1M", "3M", "1A", "all"]
    if period not in valid_periods:
        return [{"Error": f"Invalid period. Use one of: {valid_periods}"}]
    data = alpaca_get_portfolio_history(period=period, strategy_id=sid)
    if not data:
        return [{"Error": "Could not fetch portfolio history"}]
    summary = [{
        "Period": period,
        "Base Value": f"${data['base_value']:,.2f}" if data["base_value"] else "N/A",
        "Data Points": len(data["history"]),
    }]
    return summary + data["history"]


# ============================================================
# PHASE 4: Signal Tracking, Alerts & Monitoring
# ============================================================

@app.get("/signals/accuracy")
async def signals_accuracy(period: int = 30):
    """Signal accuracy report: hit rates, avg returns by source."""
    validate_range(period, "period", 1, 365)
    # Trigger outcome checking first
    try:
        threading.Thread(target=check_signal_outcomes, args=(5,), daemon=True).start()
    except Exception:
        pass
    return get_accuracy_report(period_days=period)

@app.get("/signals/history")
async def signals_history(symbol: str = "", limit: int = 50):
    """Recent signal history with outcomes."""
    s = validate_symbol(symbol) if symbol else None
    validate_range(limit, "limit", 1, 500)
    return get_signal_history(symbol=s, limit=limit)

@app.get("/signals/check_outcomes")
async def signals_check_outcomes(days: int = 5):
    """Manually trigger outcome checking for mature signals."""
    validate_range(days, "days", 1, 60)
    result = check_signal_outcomes(lookback_days=days)
    return [{"Action": "Outcome check complete", **result}]

@app.get("/telegram/test")
async def telegram_test():
    """Send a test message to Telegram."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return [{"Error": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not configured in .env"}]
    ok = tg_send("Test message from Halal Trading Bot\n\nTelegram alerts are working!")
    return [{"Status": "sent" if ok else "failed", "Bot Token": f"...{settings.TELEGRAM_BOT_TOKEN[-6:]}", "Chat ID": settings.TELEGRAM_CHAT_ID}]


@app.get("/telegram/test_chart")
async def telegram_test_chart(symbol: str = "AAPL"):
    """Test chart generation + Telegram photo sending. Returns diagnostic info."""
    symbol = validate_symbol(symbol)
    result = {"symbol": symbol, "steps": []}

    # Step 1: Fetch data
    try:
        df = fetch_market_data(symbol, period="2y")
        if df is None or len(df) < 30:
            result["steps"].append({"fetch_data": "FAILED", "reason": "No data or < 30 bars"})
            return result
        result["steps"].append({"fetch_data": "OK", "bars": len(df)})
    except Exception as e:
        result["steps"].append({"fetch_data": "ERROR", "error": str(e)})
        return result

    # Step 2: Generate chart
    try:
        from app.services.chart_generator import generate_signal_chart
        price = float(df["close"].iloc[-1])
        chart_bytes = generate_signal_chart(
            df=df, symbol=symbol, verdict="STRONG BUY",
            entry_price=price, stop_loss=round(price * 0.97, 2),
            tp1=round(price * 1.04, 2), tp2=round(price * 1.07, 2),
            tp3=round(price * 1.10, 2), confidence=78.0,
            votes_buy=7, votes_sell=1, votes_hold=1,
        )
        if chart_bytes:
            result["steps"].append({"generate_chart": "OK", "size_kb": round(len(chart_bytes) / 1024, 1)})
        else:
            result["steps"].append({"generate_chart": "FAILED", "reason": "Returned None"})
            return result
    except Exception as e:
        import traceback
        result["steps"].append({"generate_chart": "ERROR", "error": str(e), "traceback": traceback.format_exc()[-500:]})
        return result

    # Step 3: Send to Telegram
    try:
        ok = tg_send_photo(chart_bytes, caption=f"Test chart for {symbol} @ ${price:.2f}")
        result["steps"].append({"send_photo": "OK" if ok else "FAILED"})
    except Exception as e:
        result["steps"].append({"send_photo": "ERROR", "error": str(e)})

    return result

@app.get("/telegram/daily_summary")
async def telegram_daily_summary():
    """Manually trigger daily summary to Telegram."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return [{"Error": "Telegram not configured"}]
    screener_key = _cache_key("screener")
    cached, _ = _get_cached(screener_key)
    if not cached or not isinstance(cached, list):
        return [{"Error": "Screener data not available yet"}]
    portfolio = alpaca_get_account()
    portfolio_info = None
    if portfolio:
        daily_pl = portfolio["equity"] - portfolio["last_equity"]
        portfolio_info = {"equity": portfolio["equity"], "daily_pl": daily_pl}
    ok = alert_daily_summary(cached, portfolio_info)
    return [{"Status": "sent" if ok else "failed"}]


def _run_post_market_scan_bg(top_n: int, min_score: int):
    """Background worker for post-market scan (runs in thread).

    Memory-safe: flushes model cache between each stock,
    limits to top_n stocks to prevent OOM on Railway free tier.
    """
    try:
        # Step 1: Run screener to find top stocks
        screener_results = run_screener()
        if not screener_results:
            tg_send("POST-MARKET SCAN\n\nScreener returned no results.")
            return

        # Filter for strong scores
        top_stocks = [r for r in screener_results if r.get("swing_score", 0) >= min_score]
        top_stocks.sort(key=lambda x: x.get("swing_score", 0), reverse=True)
        top_stocks = top_stocks[:min(top_n, 5)]  # hard cap at 5 to prevent OOM

        if not top_stocks:
            tg_send(f"POST-MARKET SCAN\n\nNo stocks with score >= {min_score} found.\n{len(screener_results)} stocks scanned.")
            return

        # Step 2: Send header
        tg_send(
            f"POST-MARKET ANALYSIS\n"
            f"{len(screener_results)} stocks scanned\n"
            f"{len(top_stocks)} top signals (score >= {min_score})\n"
            f"\nAnalyzing with AI consensus..."
        )

        # Step 3: Run consensus on each — this triggers chart alerts automatically
        results = []
        for i, stock in enumerate(top_stocks):
            symbol = stock["symbol"]
            try:
                # Flush caches BEFORE each consensus to prevent OOM
                _flush_all_caches()

                consensus = run_consensus(symbol, horizon=5, episodes=3)  # fewer episodes to save memory
                if consensus and not consensus[0].get("Error"):
                    summary = consensus[0]
                    results.append({
                        "symbol": symbol,
                        "verdict": summary.get("Verdict", "N/A"),
                        "confidence": summary.get("Confidence %", 0),
                    })
                else:
                    results.append({"symbol": symbol, "error": "Consensus failed"})
            except Exception as e:
                logger.error(f"Post-market scan {symbol}: {e}")
                results.append({"symbol": symbol, "error": str(e)})

            # Aggressive memory cleanup between stocks
            _flush_all_caches()
            time.sleep(3)

        # Step 4: Summary
        strong = [r for r in results if "STRONG" in r.get("verdict", "")]
        summary_lines = []
        for r in results:
            if "error" not in r:
                summary_lines.append(f"  {r['symbol']}: {r['verdict']} ({r['confidence']:.0f}%)")

        tg_send(
            f"SCAN COMPLETE\n\n"
            f"Analyzed: {len(top_stocks)} stocks\n"
            f"STRONG signals: {len(strong)}\n"
            f"\nResults:\n" + "\n".join(summary_lines) if summary_lines else "No signals"
        )

    except Exception as e:
        logger.error(f"Post-market scan failed: {e}")
        tg_send(f"POST-MARKET SCAN ERROR\n\n{str(e)[:200]}")


@app.get("/api/v1/post_market_scan")
async def post_market_scan(top_n: int = 5, min_score: int = 55):
    """Post-market analysis: scan top stocks, send chart alerts via Telegram.

    Runs in the BACKGROUND — returns immediately. Results sent to Telegram.

    Runs the screener, picks the top N stocks by swing score,
    runs consensus on each, and sends STRONG signals with professional
    charts to Telegram (entry, SL, TP lines + candlestick + arrows).

    Call this after market close (4 PM ET) for next-day trade ideas.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        return [{"Error": "Telegram not configured"}]

    # Launch in background thread — don't block the HTTP response
    thread = threading.Thread(
        target=_run_post_market_scan_bg,
        args=(top_n, min_score),
        daemon=True,
    )
    thread.start()

    return [{
        "Status": "Post-market scan started",
        "top_n": top_n,
        "min_score": min_score,
        "Message": "Results will be sent to Telegram. Check your bot.",
    }]


# ══════════════════════════════════════════════════════════════════════
# AUTO-TRADING ENDPOINTS (Stage 1: Paper Trading)
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/v1/trading/status")
async def trading_status():
    """Get auto-trading status: enabled/disabled, risk dashboard, PDT tracker."""
    account = alpaca_get_account()
    if not account:
        return {"error": "Cannot connect to Alpaca"}
    positions = alpaca_get_positions()
    risk = get_risk_status(account, positions)
    risk["auto_trade_enabled"] = settings.AUTO_TRADE_ENABLED
    risk["min_confidence"] = settings.MIN_TRADE_CONFIDENCE
    risk["trade_risk_pct"] = settings.TRADE_RISK_PCT
    risk["max_position_pct"] = settings.MAX_POSITION_PCT
    return risk


@app.get("/api/v1/trading/history")
async def trading_history(limit: int = 50):
    """Get auto-trade execution history."""
    return get_trade_history(limit=limit)


@app.get("/api/v1/trading/performance")
async def trading_performance():
    """Get trading performance report: win rate, Sharpe, drawdown, profit factor."""
    return get_performance_report()


@app.post("/api/v1/trading/enable")
async def trading_enable():
    """Enable auto-trading (paper account only)."""
    settings.AUTO_TRADE_ENABLED = True
    tg_send("AUTO-TRADING ENABLED\n\nPaper account auto-execution is now active.\nOnly STRONG BUY/SELL signals will be executed.")
    return {"auto_trade_enabled": True, "message": "Paper trading auto-execution enabled"}


@app.post("/api/v1/trading/disable")
async def trading_disable():
    """Disable auto-trading (emergency stop)."""
    settings.AUTO_TRADE_ENABLED = False
    tg_send("AUTO-TRADING DISABLED\n\nAuto-execution has been stopped.\nAll existing positions remain open.")
    return {"auto_trade_enabled": False, "message": "Auto-trading disabled"}


# ══════════════════════════════════════════════════════════════════════
# PARAMETER OPTIMIZATION ENDPOINT
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/v1/optimize")
async def optimize_parameters(n_stocks: int = 10):
    """Run parameter sweep optimization on historical data.

    Tests different consensus thresholds, SL/TP multipliers, and tool
    parameters to find optimal values. Results show improvement vs current defaults.
    Runs in background — returns immediately, results sent to Telegram.
    """
    def _run_optimizer():
        try:
            from app.services.optimizer import optimize_params
            result = optimize_params(n_samples=n_stocks)
            if "error" not in result:
                msg = (
                    f"OPTIMIZER RESULTS\n\n"
                    f"Stocks tested: {len(result['stocks_tested'])}\n\n"
                    f"CURRENT vs OPTIMAL:\n"
                    f"Win Rate: {result['baseline_performance']['win_rate']}% -> {result['optimal_performance']['win_rate']}%\n"
                    f"Profit Factor: {result['baseline_performance']['profit_factor']} -> {result['optimal_performance']['profit_factor']}\n"
                    f"Sharpe: {result['baseline_performance']['sharpe']} -> {result['optimal_performance']['sharpe']}\n\n"
                    f"Best Params:\n"
                    f"Strong BUY votes: {result['optimal_params']['strong_buy_votes']}\n"
                    f"Min confidence: {result['optimal_params']['min_confidence']}%\n"
                    f"SL mult: {result['optimal_params']['sl_atr_mult']}x ATR\n"
                    f"TP1 mult: {result['optimal_params']['tp1_atr_mult']}x ATR\n"
                    f"BB squeeze: {result['optimal_params']['bb_squeeze_width']}%\n"
                    f"Momentum ROC: {result['optimal_params']['momentum_roc_threshold']}%"
                )
                tg_send(msg)
        except Exception as e:
            logger.error(f"Optimizer error: {e}")

    threading.Thread(target=_run_optimizer, daemon=True).start()
    return [{"Status": "Optimizer started", "Message": "Results will be sent to Telegram"}]


# ════════���═════════════════��═══════════════════════════════════════════
# INTRADAY DATA ENDPOINT
# ══════════════════════════════════════════���═══════════════════════════

@app.get("/api/v1/intraday/{symbol}")
async def intraday_bars(symbol: str, timeframe: str = "15Min", days: int = 5):
    """Get intraday bars for a symbol (15Min default).

    Supports: 1Min, 5Min, 15Min, 1Hour.
    Includes basic technical overlay: EMA9, VWAP approximation, RSI.
    """
    s = validate_symbol(symbol)
    valid_tf = {"1Min", "5Min", "15Min", "1Hour"}
    if timeframe not in valid_tf:
        return [{"Error": f"Invalid timeframe. Use: {valid_tf}"}]

    df = fetch_alpaca_intraday(s, timeframe=timeframe, days_back=days)
    if df is None or df.empty:
        return [{"Error": f"No intraday data for {s}"}]

    # Add technical overlays
    close = df["close"]
    df["ema9"] = ema(close, 9)
    df["rsi14"] = rsi(close, 14)

    # VWAP approximation (cumulative volume-weighted average price)
    df["vwap"] = (df["close"] * df["volume"]).cumsum() / df["volume"].cumsum()

    # Format for response
    bars = []
    for _, row in df.tail(200).iterrows():
        bars.append({
            "date": row["date"].isoformat(),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row["volume"]),
            "ema9": round(float(row["ema9"]), 2) if pd.notna(row["ema9"]) else None,
            "rsi14": round(float(row["rsi14"]), 1) if pd.notna(row["rsi14"]) else None,
            "vwap": round(float(row["vwap"]), 2) if pd.notna(row["vwap"]) else None,
        })
    return bars


@app.get("/health")
async def health():
    checks = {"openbb_forecast": False, "market_data": False, "database": False}
    try:
        from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator
        from openbb_forecast.models.lstm import LSTMForecaster
        import torch
        checks["openbb_forecast"] = True
    except Exception:
        pass
    try:
        df = fetch_yf("AAPL", period="1y")
        checks["market_data"] = df is not None and len(df) > 0
    except Exception:
        pass
    try:
        from app.db.database import engine
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["database"] = True
    except Exception:
        pass
    alpaca_configured = bool(
        (settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY) or
        (settings.ALPACA_API_KEY_A and settings.ALPACA_SECRET_KEY_A)
    )
    fmp_configured = bool(settings.FMP_API_KEY)
    telegram_configured = bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)
    all_ok = all(checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "version": "17.0.0",
        "widgets": 10,
        "stocks": len(HALAL_STOCKS),
        "data_source": "alpaca+yfinance" if alpaca_configured else "yfinance",
        "halal_screening": "fmp_live" if fmp_configured else "hardcoded_lists",
        "telegram": "active" if telegram_configured else "not_configured",
        "database": "connected" if checks["database"] else "unavailable",
        "auto_trading": "enabled" if settings.AUTO_TRADE_ENABLED else "disabled",
        "config": "loaded",
        "dependencies": checks,
    }
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
