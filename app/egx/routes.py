"""EGX API routes — all under /egx/ prefix.

Endpoints:
  POST /egx/upload          — Upload CSV file(s) with EGX stock data
  GET  /egx/watchlist        — List all uploaded EGX symbols
  GET  /egx/data/{symbol}    — Get stored OHLCV data for a symbol
  GET  /egx/analyze/{symbol} — Run full 7-tool consensus analysis
  GET  /egx/backtest/{symbol}— Run backtest with V9 strategy
  GET  /egx/optimize/{symbol}— Run parameter optimization
  GET  /egx/scan             — Scan all watchlist symbols
  GET  /egx/chart/{symbol}   — Generate chart image (PNG)
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, UploadFile, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.db.database import SessionLocal
from app.egx.data import load_csv, save_to_db, get_stock_data, get_watchlist, get_all_symbols
from app.egx.indicators import compute_indicators
from app.egx.scoring import EgxStrategyConfig, generate_signals
from app.egx.backtest import run_backtest, analyze_performance, optimize_parameters
from app.egx.consensus import run_consensus
from app.egx.charts import generate_egx_chart
from app.egx.alerts import alert_egx_signal, alert_egx_scan_summary
from app.egx.models import EgxSignalHistory, EgxBacktestResult, EgxWatchlist

logger = logging.getLogger("egx")

router = APIRouter(prefix="/egx", tags=["EGX - Egyptian Exchange"])


# ---------------------------------------------------------------------------
# Upload Page (drag & drop UI)
# ---------------------------------------------------------------------------

@router.get("/upload_page", response_class=HTMLResponse)
async def upload_page():
    """Simple drag-and-drop upload page for EGX CSV files."""
    return """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<title>EGX - رفع بيانات الأسهم المصرية</title>
<style>
  body { font-family: Arial, sans-serif; background: #0d1117; color: #c9d1d9; max-width: 800px; margin: 40px auto; padding: 20px; }
  h1 { color: #58a6ff; text-align: center; }
  .drop-zone { border: 3px dashed #30363d; border-radius: 12px; padding: 60px 20px; text-align: center; cursor: pointer; transition: all 0.3s; margin: 20px 0; }
  .drop-zone:hover, .drop-zone.active { border-color: #58a6ff; background: #161b22; }
  .drop-zone h2 { color: #8b949e; margin: 0 0 10px; }
  .drop-zone p { color: #6e7681; margin: 0; }
  input[type=file] { display: none; }
  #results { margin-top: 20px; }
  .result { background: #161b22; border-radius: 8px; padding: 15px; margin: 10px 0; border-right: 4px solid #238636; }
  .result.error { border-right-color: #f85149; }
  .result .symbol { color: #58a6ff; font-weight: bold; font-size: 18px; }
  .result .info { color: #8b949e; margin-top: 5px; }
  .loading { color: #d29922; }
  .btn { background: #238636; color: white; border: none; padding: 12px 30px; border-radius: 8px; cursor: pointer; font-size: 16px; margin-top: 10px; }
  .btn:hover { background: #2ea043; }
  .btn:disabled { background: #30363d; cursor: not-allowed; }
  .stats { display: flex; gap: 20px; justify-content: center; margin: 20px 0; }
  .stat { background: #161b22; padding: 15px 25px; border-radius: 8px; text-align: center; }
  .stat .num { font-size: 28px; color: #58a6ff; font-weight: bold; }
  .stat .label { color: #8b949e; font-size: 14px; }
</style>
</head>
<body>
<h1>EGX - رفع بيانات الأسهم المصرية</h1>
<p style="text-align:center; color:#8b949e;">ارفع ملفات CSV للأسهم المصرية (يدعم الأعمدة العربية والإنجليزية)</p>

<div class="drop-zone" id="dropZone" onclick="fileInput.click()">
  <h2>اسحب الملفات هنا أو اضغط للاختيار</h2>
  <p>CSV files - يمكن رفع عدة ملفات دفعة واحدة</p>
</div>
<input type="file" id="fileInput" multiple accept=".csv">

<div id="stats" style="display:none" class="stats">
  <div class="stat"><div class="num" id="totalFiles">0</div><div class="label">ملفات</div></div>
  <div class="stat"><div class="num" id="totalRows">0</div><div class="label">صفوف</div></div>
  <div class="stat"><div class="num" id="totalSymbols">0</div><div class="label">أسهم</div></div>
</div>

<div id="results"></div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const results = document.getElementById('results');
let totalFiles=0, totalRows=0, allSymbols=new Set();

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('active'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('active'));
dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('active'); handleFiles(e.dataTransfer.files); });
fileInput.addEventListener('change', e => handleFiles(e.target.files));

async function handleFiles(files) {
  for (const file of files) {
    if (!file.name.endsWith('.csv')) continue;
    const div = document.createElement('div');
    div.className = 'result';
    div.innerHTML = '<span class="loading">جاري رفع ' + file.name + '...</span>';
    results.prepend(div);

    const form = new FormData();
    form.append('file', file);
    try {
      const resp = await fetch('/egx/upload', { method: 'POST', body: form });
      const data = await resp.json();
      if (resp.ok) {
        const syms = data.symbols || [];
        syms.forEach(s => allSymbols.add(s));
        totalFiles++;
        totalRows += data.total_rows || 0;
        div.innerHTML = '<div class="symbol">' + syms.join(', ') + '</div>' +
          '<div class="info">' + file.name + ' — ' + (data.rows_inserted||0) + ' صف جديد, ' + (data.rows_updated||0) + ' محدث</div>';
      } else {
        div.className = 'result error';
        div.innerHTML = '<div class="symbol">خطأ: ' + file.name + '</div><div class="info">' + (data.detail||'Unknown error') + '</div>';
      }
    } catch(e) {
      div.className = 'result error';
      div.innerHTML = '<div class="symbol">خطأ: ' + file.name + '</div><div class="info">' + e.message + '</div>';
    }
    document.getElementById('stats').style.display='flex';
    document.getElementById('totalFiles').textContent = totalFiles;
    document.getElementById('totalRows').textContent = totalRows;
    document.getElementById('totalSymbols').textContent = allSymbols.size;
  }
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_csv(file: UploadFile = File(...)):
    """Upload a CSV file with EGX stock data (Arabic or English columns).

    Accepts single-symbol or multi-symbol CSV files.
    Arabic columns (الرمز, فتح, أعلى, etc.) are auto-detected.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    try:
        df = load_csv(content, filename=file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = save_to_db(df)
    return {
        "status": "ok",
        "filename": file.filename,
        **result,
    }


@router.post("/upload_multi")
async def upload_multiple_csvs(files: list[UploadFile] = File(...)):
    """Upload multiple CSV files at once."""
    results = []
    for file in files:
        if not file.filename.endswith(".csv"):
            results.append({"filename": file.filename, "error": "Not a CSV file"})
            continue

        content = await file.read()
        try:
            df = load_csv(content, filename=file.filename)
            result = save_to_db(df)
            results.append({"filename": file.filename, "status": "ok", **result})
        except Exception as e:
            results.append({"filename": file.filename, "error": str(e)})

    return {"uploaded": len(results), "results": results}


# ---------------------------------------------------------------------------
# Watchlist & Data
# ---------------------------------------------------------------------------

@router.get("/watchlist")
async def egx_watchlist():
    """List all EGX symbols with uploaded data."""
    items = get_watchlist()
    symbols = get_all_symbols()
    return {
        "total": len(symbols),
        "symbols": symbols,
        "watchlist": items,
    }


@router.get("/data/{symbol}")
async def egx_data(symbol: str, limit: int = Query(default=100, ge=1, le=5000)):
    """Get stored OHLCV data for an EGX symbol."""
    df = get_stock_data(symbol.upper(), min_rows=1)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    df_tail = df.tail(limit)
    records = df_tail.to_dict(orient="records")
    # Convert dates to strings
    for r in records:
        if "date" in r and hasattr(r["date"], "isoformat"):
            r["date"] = r["date"].isoformat()

    return {
        "symbol": symbol.upper(),
        "total_rows": len(df),
        "returned": len(records),
        "data": records,
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@router.get("/analyze/{symbol}")
async def egx_analyze(
    symbol: str,
    send_telegram: bool = Query(default=True),
):
    """Run full 7-tool consensus analysis on an EGX stock.

    Tools: V9 Score, Bollinger, StochRSI, OBV, ADX, Backtest, Monte Carlo.
    Sends Telegram alert with chart for STRONG signals.
    """
    df = get_stock_data(symbol.upper())
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol} (need >= 50 rows)")

    cfg = EgxStrategyConfig()
    result = run_consensus(df, symbol.upper(), cfg)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Save signal to DB
    _save_signal(result)

    # Send Telegram for STRONG signals
    if send_telegram and "STRONG" in result.get("verdict", ""):
        chart_bytes = None
        try:
            chart_bytes = generate_egx_chart(
                df=df,
                symbol=symbol.upper(),
                verdict=result["verdict"],
                entry_price=result["price"],
                stop_loss=result["stop_loss"],
                tp1=result["tp1"],
                tp2=result.get("tp2"),
                tp3=result.get("tp3"),
                confidence=result["confidence"],
                votes_buy=result["votes_buy"],
                votes_sell=result["votes_sell"],
                votes_hold=result["votes_hold"],
                v9_score=result.get("v9_score", 0),
            )
        except Exception as e:
            logger.error(f"Chart generation failed: {e}")

        alert_egx_signal(symbol.upper(), result, chart_bytes)

    return result


@router.get("/backtest/{symbol}")
async def egx_backtest(symbol: str):
    """Run V9 backtest on an EGX stock and return performance metrics."""
    df = get_stock_data(symbol.upper())
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    cfg = EgxStrategyConfig()
    trades = run_backtest(df, symbol.upper(), cfg)
    stats = analyze_performance(trades)

    # Save to DB
    _save_backtest(symbol.upper(), stats, cfg)

    return {
        "symbol": symbol.upper(),
        "config": {
            "sl_atr_mult": cfg.sl_atr_mult,
            "tp_atr_mult": cfg.tp_atr_mult,
            "min_score": cfg.min_score,
            "vol_surge": cfg.vol_surge,
        },
        "performance": stats,
        "trades": [t.to_dict() for t in trades[-20:]],  # Last 20 trades
        "total_trade_count": len(trades),
    }


@router.get("/optimize/{symbol}")
async def egx_optimize(symbol: str):
    """Run parameter optimization (72 combinations) for an EGX stock."""
    df = get_stock_data(symbol.upper())
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    result = optimize_parameters(df, symbol.upper())
    return {
        "symbol": symbol.upper(),
        **result,
    }


@router.get("/scan")
async def egx_scan(send_telegram: bool = Query(default=True)):
    """Scan all watchlist symbols and return consensus for each.

    Sends Telegram summary + individual STRONG signal alerts.
    """
    symbols = get_all_symbols()
    if not symbols:
        return {"error": "No EGX symbols uploaded. Use POST /egx/upload first."}

    cfg = EgxStrategyConfig()
    results = []

    for symbol in symbols:
        try:
            df = get_stock_data(symbol, min_rows=50)
            if df is None:
                continue

            result = run_consensus(df, symbol, cfg)
            if "error" not in result:
                results.append(result)
                _save_signal(result)

                # Telegram for STRONG signals
                if send_telegram and "STRONG" in result.get("verdict", ""):
                    chart_bytes = None
                    try:
                        chart_bytes = generate_egx_chart(
                            df=df,
                            symbol=symbol,
                            verdict=result["verdict"],
                            entry_price=result["price"],
                            stop_loss=result["stop_loss"],
                            tp1=result["tp1"],
                            tp2=result.get("tp2"),
                            tp3=result.get("tp3"),
                            confidence=result["confidence"],
                            votes_buy=result["votes_buy"],
                            votes_sell=result["votes_sell"],
                            votes_hold=result["votes_hold"],
                            v9_score=result.get("v9_score", 0),
                        )
                    except Exception:
                        pass
                    alert_egx_signal(symbol, result, chart_bytes)

        except Exception as e:
            logger.error(f"EGX scan error for {symbol}: {e}")

    # Sort by confidence
    results.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    # Send summary
    if send_telegram and results:
        alert_egx_scan_summary(results)

    return {
        "scanned": len(symbols),
        "signals": len(results),
        "strong_signals": len([r for r in results if "STRONG" in r.get("verdict", "")]),
        "results": results,
    }


@router.get("/chart/{symbol}")
async def egx_chart(symbol: str):
    """Generate and return a chart image for an EGX stock."""
    from fastapi.responses import Response

    df = get_stock_data(symbol.upper())
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    cfg = EgxStrategyConfig()
    result = run_consensus(df, symbol.upper(), cfg)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    chart_bytes = generate_egx_chart(
        df=df,
        symbol=symbol.upper(),
        verdict=result["verdict"],
        entry_price=result["price"],
        stop_loss=result["stop_loss"],
        tp1=result["tp1"],
        tp2=result.get("tp2"),
        tp3=result.get("tp3"),
        confidence=result["confidence"],
        votes_buy=result["votes_buy"],
        votes_sell=result["votes_sell"],
        votes_hold=result["votes_hold"],
        v9_score=result.get("v9_score", 0),
    )

    if not chart_bytes:
        raise HTTPException(status_code=500, detail="Chart generation failed")

    return Response(content=chart_bytes, media_type="image/png")


@router.get("/signals")
async def egx_signal_history(symbol: Optional[str] = None, limit: int = 50):
    """Get EGX signal history from database."""
    db = SessionLocal()
    try:
        query = db.query(EgxSignalHistory).order_by(EgxSignalHistory.created_at.desc())
        if symbol:
            query = query.filter_by(symbol=symbol.upper())
        signals = query.limit(limit).all()

        return [{
            "id": s.id,
            "symbol": s.symbol,
            "direction": s.direction,
            "signal": s.signal,
            "score": s.score,
            "price": s.price,
            "stop_loss": s.stop_loss,
            "take_profit": s.take_profit,
            "confidence": s.confidence,
            "tool_votes": s.tool_votes,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        } for s in signals]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_signal(result: dict):
    """Save consensus result to EgxSignalHistory."""
    db = SessionLocal()
    try:
        verdict = result.get("verdict", "NEUTRAL")
        if verdict == "NEUTRAL":
            return

        direction = "LONG" if "BUY" in verdict else "SHORT"
        db.add(EgxSignalHistory(
            symbol=result["symbol"],
            direction=direction,
            signal=verdict,
            score=result.get("v9_score", 0),
            price=result.get("price"),
            stop_loss=result.get("stop_loss"),
            take_profit=result.get("tp1"),
            confidence=result.get("confidence"),
            tool_votes=result.get("tools"),
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save EGX signal: {e}")
    finally:
        db.close()


def _save_backtest(symbol: str, stats: dict, cfg: EgxStrategyConfig):
    """Save backtest result to DB."""
    db = SessionLocal()
    try:
        db.add(EgxBacktestResult(
            symbol=symbol,
            total_trades=stats.get("total_trades", 0),
            wins=stats.get("wins", 0),
            losses=stats.get("losses", 0),
            win_rate=stats.get("win_rate", 0),
            total_pnl=stats.get("total_pnl", 0),
            profit_factor=stats.get("profit_factor", 0),
            expectancy=stats.get("expectancy", 0),
            avg_hold_days=stats.get("avg_hold_days", 0),
            config_json={
                "sl_atr_mult": cfg.sl_atr_mult,
                "tp_atr_mult": cfg.tp_atr_mult,
                "min_score": cfg.min_score,
                "vol_surge": cfg.vol_surge,
            },
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save EGX backtest: {e}")
    finally:
        db.close()
