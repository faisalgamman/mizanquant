"""Consensus, Pipeline, Ready-to-Trade endpoints"""
from fastapi import APIRouter
from halal_screener import OperatorAPIKey

router = APIRouter(tags=["Consensus"])


@router.get("/consensus")
async def consensus(symbol: str = "AAPL", horizon: int = 5, episodes: int = 10):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    validate_range(episodes, "episodes", 1, 50)
    key = _cache_key("consensus", symbol=s, horizon=horizon, episodes=episodes)
    return _serve_or_compute(key, run_consensus, args=(s, horizon, episodes), msg=f"Computing AI consensus for {s}...")


@router.get("/consensus_momentum")
async def consensus_momentum(symbol: str = "AAPL", horizon: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus_momentum)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("consensus_momentum", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_consensus_momentum, args=(s, horizon), msg=f"Computing momentum consensus for {s}...")


@router.get("/consensus_reversion")
async def consensus_reversion(symbol: str = "AAPL", horizon: int = 3):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus_reversion)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("consensus_reversion", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_consensus_reversion, args=(s, horizon), msg=f"Computing reversion consensus for {s}...")


@router.get("/consensus_ml")
async def consensus_ml(symbol: str = "AAPL", horizon: int = 7, episodes: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_consensus_ml)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    validate_range(episodes, "episodes", 1, 50)
    key = _cache_key("consensus_ml", symbol=s, horizon=horizon, episodes=episodes)
    return _serve_or_compute(key, run_consensus_ml, args=(s, horizon, episodes), msg=f"Computing ML consensus for {s}...")


@router.get("/batch_consensus")
async def batch_consensus(min_swing_score: int = 55, horizon: int = 5, episodes: int = 5, max_stocks: int = 10):
    from halal_screener import (_serve_or_compute, _cache_key, run_batch_consensus)
    key = _cache_key("batch_consensus", min=min_swing_score, h=horizon, ep=episodes, max=max_stocks)
    return _serve_or_compute(key, run_batch_consensus, args=(min_swing_score, horizon, episodes, max_stocks), msg="Computing batch consensus...")


@router.get("/pipeline")
async def pipeline(min_confidence: int = 40, max_final: int = 15, horizon: int = 5, episodes: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, run_pipeline)
    key = _cache_key("pipeline", conf=min_confidence, max=max_final, h=horizon, ep=episodes)
    return _serve_or_compute(key, run_pipeline, args=(min_confidence, max_final, horizon, episodes), msg="Computing full pipeline...")


@router.get("/ready")
async def ready_to_trade(min_swing: int = 55, max_stocks: int = 25):
    from halal_screener import (_serve_or_compute, _cache_key, run_ready_to_trade)
    key = _cache_key("ready_to_trade", min=min_swing, max=max_stocks)
    return _serve_or_compute(key, run_ready_to_trade, args=(min_swing, max_stocks),
                            msg="Analyzing top stocks through full AI pipeline... Pre-market scan runs at 9:00 AM ET.")


@router.get("/refresh_ready")
async def refresh_ready(min_swing: int = 55, max_stocks: int = 25, x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _cache_key, _cache_lock, _bg_cache, _cache_status, _bg_compute, run_ready_to_trade
    _require_api_key(x_api_key)
    key = _cache_key("ready_to_trade", min=min_swing, max=max_stocks)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_ready_to_trade, (min_swing, max_stocks)), daemon=True).start()
    return [{"Status": "Ready-to-Trade refresh started."}]


@router.get("/refresh_consensus")
async def refresh_consensus(symbol: str = "AAPL", x_api_key: OperatorAPIKey = None):
    from halal_screener import (_require_api_key, validate_symbol, _cache_key, _cache_lock,
        _bg_cache, _cache_status, _bg_compute, run_consensus)
    _require_api_key(x_api_key)
    s = validate_symbol(symbol)
    key = _cache_key("consensus", symbol=s, horizon=5, episodes=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_consensus, (s, 5, 10)), daemon=True).start()
    return {"Status": f"Consensus refresh started for {s}."}


@router.get("/refresh_batch")
async def refresh_batch(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _cache_key, _cache_lock, _bg_cache, _cache_status, _bg_compute, run_batch_consensus
    _require_api_key(x_api_key)
    key = _cache_key("batch_consensus", min=55, h=5, ep=5, max=10)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_batch_consensus, (55, 5, 5, 10)), daemon=True).start()
    return [{"Status": "Batch consensus refresh started"}]


@router.get("/refresh_pipeline")
async def refresh_pipeline(x_api_key: OperatorAPIKey = None):
    from halal_screener import _require_api_key, _cache_key, _cache_lock, _bg_cache, _cache_status, _bg_compute, run_pipeline
    _require_api_key(x_api_key)
    key = _cache_key("pipeline", conf=40, max=15, h=5, ep=5)
    with _cache_lock:
        _bg_cache.pop(key, None)
        _cache_status[key] = "idle"
    from threading import Thread
    Thread(target=_bg_compute, args=(key, run_pipeline, (40, 15, 5, 5)), daemon=True).start()
    return [{"Status": "Pipeline refresh started"}]
