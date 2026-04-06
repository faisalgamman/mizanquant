"""EGX API routes — all under /egx/ prefix.

Endpoints:
  POST /egx/upload              — Upload CSV file(s) with EGX stock data
  GET  /egx/watchlist           — List all uploaded EGX symbols
  GET  /egx/data/{symbol}       — Get stored OHLCV data for a symbol
  GET  /egx/analyze?symbol=X    — Run full 7-tool consensus analysis
  GET  /egx/backtest?symbol=X   — Run backtest with V9 strategy
  GET  /egx/optimize?symbol=X   — Run parameter optimization
  GET  /egx/scan                — Scan all watchlist symbols
  GET  /egx/chart/{symbol}      — Generate chart image (PNG)
  GET  /egx/debug?symbol=X      — Debug: verify query param reception
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, UploadFile, HTTPException, Request
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
    # Flat table for OpenBB widgets
    return [{
        "Symbol": w["symbol"],
        "Name": w.get("name_ar") or "-",
        "Sector": w.get("sector") or "-",
        "Added": (w["added_at"] or "")[:10],
        "Last Analyzed": (w["last_analyzed"] or "-")[:10] if w.get("last_analyzed") else "-",
    } for w in items] if items else [{"Symbol": s} for s in symbols]


@router.get("/data/{symbol}")
async def egx_data(symbol: str, limit: int = 100):
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

@router.get("/analyze")
async def egx_analyze(symbol: str = "COMI", send_telegram: bool = True):
    """Run full 7-tool consensus analysis on an EGX stock.

    Tools: V9 Score, Bollinger, StochRSI, OBV, ADX, Backtest, Monte Carlo.
    Sends Telegram alert with chart for STRONG signals.
    """
    logger.info(f"EGX analyze called — symbol={symbol}")
    df = get_stock_data(symbol.upper())
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol} — upload CSV first via /egx/upload_page")

    cfg = EgxStrategyConfig()
    # Limit data for faster response
    if len(df) > 300:
        df = df.tail(300).copy().reset_index(drop=True)
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

    # Flat table for OpenBB widgets
    tools = result.get("tools", {})
    bt = result.get("backtest_stats") or {}
    mc = result.get("monte_carlo") or {}
    return [
        {
            "Symbol": result["symbol"],
            "Requested": symbol,
            "Verdict": result["verdict"],
            "Confidence": f"{result['confidence']:.0f}%",
            "V9 Score": f"{result.get('v9_score', 0)}/6",
            "Price (EGP)": result["price"],
            "Stop Loss": result["stop_loss"],
            "TP1": result["tp1"],
            "TP2": result.get("tp2"),
            "TP3": result.get("tp3"),
            "R:R": f"1:{result.get('risk_reward', 0)}",
            "RSI": result.get("rsi"),
            "ADX": result.get("adx"),
            "V9 Score Tool": tools.get("V9 Score", "-"),
            "Bollinger": tools.get("Bollinger", "-"),
            "StochRSI": tools.get("StochRSI", "-"),
            "OBV": tools.get("OBV", "-"),
            "ADX Trend": tools.get("ADX Trend", "-"),
            "Backtest": tools.get("Backtest", "-"),
            "Monte Carlo": tools.get("Monte Carlo", "-"),
            "BT Win Rate": f"{bt.get('win_rate', 0):.0f}%" if bt else "-",
            "BT PF": bt.get("profit_factor", "-"),
            "MC Prob": f"{mc.get('prob_profit', 0)*100:.0f}%" if mc else "-",
        }
    ]


@router.get("/backtest")
async def egx_backtest(symbol: str = "COMI"):
    """Run V9 backtest on an EGX stock and return performance metrics."""
    logger.info(f"EGX backtest called — symbol={symbol}")
    df = get_stock_data(symbol.upper())
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    cfg = EgxStrategyConfig()
    trades = run_backtest(df, symbol.upper(), cfg)
    stats = analyze_performance(trades)

    # Save to DB
    _save_backtest(symbol.upper(), stats, cfg)

    # Flat table for OpenBB: summary row + last 15 trades
    rows = [{
        "Symbol": symbol.upper(),
        "Type": "SUMMARY",
        "Total Trades": stats.get("total_trades", 0),
        "Wins": stats.get("wins", 0),
        "Losses": stats.get("losses", 0),
        "Win Rate": f"{stats.get('win_rate', 0):.1f}%",
        "Total PnL (EGP)": f"{stats.get('total_pnl', 0):,.0f}",
        "Profit Factor": stats.get("profit_factor", 0),
        "Expectancy": f"{stats.get('expectancy', 0):,.0f}",
        "R:R": stats.get("risk_reward", 0),
        "Max DD": f"{stats.get('max_drawdown', 0):,.0f}",
        "Avg Hold": f"{stats.get('avg_hold_days', 0):.1f}d",
        "SL ATR": cfg.sl_atr_mult,
        "TP ATR": cfg.tp_atr_mult,
    }]
    for t in trades[-15:]:
        rows.append({
            "Symbol": t.symbol,
            "Type": "LONG" if t.direction == 1 else "SHORT",
            "Entry": f"{t.entry_price:.2f}",
            "Exit": f"{t.exit_price:.2f}" if t.exit_price else "-",
            "PnL (EGP)": f"{t.pnl:,.0f}",
            "PnL %": f"{t.pnl_pct:.1f}%",
            "Exit Reason": t.exit_reason,
            "Score": t.score,
            "Hold": f"{t.hold_days}d",
        })
    return rows


@router.get("/optimize")
async def egx_optimize(symbol: str = "COMI"):
    """Run parameter optimization (72 combinations) for an EGX stock."""
    logger.info(f"EGX optimize called — symbol={symbol}")
    df = get_stock_data(symbol.upper())
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    result = optimize_parameters(df, symbol.upper())
    bc = result.get("best_config", {})
    bs = result.get("best_stats", {})
    # Flat table for OpenBB
    return [{
        "Symbol": symbol.upper(),
        "SL ATR": bc.get("sl_atr_mult", "-"),
        "TP ATR": bc.get("tp_atr_mult", "-"),
        "Min Score": bc.get("min_score", "-"),
        "Vol Surge": bc.get("vol_surge", "-"),
        "Total Trades": bs.get("total_trades", 0),
        "Win Rate": f"{bs.get('win_rate', 0):.1f}%",
        "Total PnL (EGP)": f"{bs.get('total_pnl', 0):,.0f}",
        "Profit Factor": bs.get("profit_factor", 0),
        "Expectancy": f"{bs.get('expectancy', 0):,.0f}",
        "R:R": bs.get("risk_reward", 0),
        "Combos Tested": result.get("combinations_tested", 72),
    }]


@router.get("/scan")
async def egx_scan(send_telegram: bool = True):
    """Scan all watchlist symbols and return consensus for each.

    Sends Telegram summary + individual STRONG signal alerts.
    """
    symbols = get_all_symbols()
    if not symbols:
        return [{"Status": "No EGX symbols uploaded", "Info": "Use /egx/upload_page to upload CSV files"}]

    cfg = EgxStrategyConfig()
    results = []

    for symbol in symbols:
        try:
            df = get_stock_data(symbol, min_rows=5)
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

    # Flat table for OpenBB
    return [{
        "Symbol": r["symbol"],
        "Verdict": r["verdict"],
        "Confidence": f"{r.get('confidence', 0):.0f}%",
        "V9": f"{r.get('v9_score', 0)}/6",
        "Price (EGP)": r.get("price", 0),
        "SL": r.get("stop_loss", 0),
        "TP1": r.get("tp1", 0),
        "R:R": f"1:{r.get('risk_reward', 0)}",
        "RSI": r.get("rsi"),
        "ADX": r.get("adx"),
        "Buy": r.get("votes_buy", 0),
        "Sell": r.get("votes_sell", 0),
        "Hold": r.get("votes_hold", 0),
    } for r in results] if results else [{"Status": "No signals found"}]


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

        # Flat table for OpenBB
        return [{
            "Symbol": s.symbol,
            "Signal": s.signal,
            "Direction": s.direction,
            "V9 Score": s.score,
            "Price (EGP)": s.price,
            "Stop Loss": s.stop_loss,
            "Take Profit": s.take_profit,
            "Confidence": f"{s.confidence:.0f}%" if s.confidence else "-",
            "Date": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "-",
        } for s in signals] if signals else [{"Status": "No signals recorded yet"}]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Debug — verify query params are received
# ---------------------------------------------------------------------------

@router.get("/debug")
async def egx_debug(request: Request, symbol: str = "COMI"):
    """Debug endpoint to verify query parameter reception."""
    raw = request.query_params.get("symbol", "NOT_SENT")
    all_symbols = get_all_symbols()
    return [{
        "received_symbol": symbol,
        "raw_query_param": raw,
        "all_query_params": str(dict(request.query_params)),
        "url": str(request.url),
        "symbols_in_db": ", ".join(all_symbols) if all_symbols else "NONE",
    }]


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
