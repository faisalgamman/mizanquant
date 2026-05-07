"""ML forecast & RL endpoints — LSTM, Transformer, Ensemble, DQN, Policy Gradient"""
from fastapi import APIRouter

router = APIRouter(tags=["Forecast"])


@router.get("/lstm")
async def lstm(symbol: str = "AAPL", horizon: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, check_rate_limit, run_lstm)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    if not check_rate_limit("lstm", 3):
        return [{"Status": "Rate limited", "Info": "Too many LSTM requests. Try again in 1 minute."}]
    key = _cache_key("lstm", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_lstm, args=(s, horizon), msg=f"Running LSTM for {s}...")


@router.get("/transformer")
async def transformer(symbol: str = "AAPL", horizon: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, check_rate_limit, run_transformer)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    if not check_rate_limit("transformer", 3):
        return [{"Status": "Rate limited", "Info": "Too many Transformer requests. Try again in 1 minute."}]
    key = _cache_key("transformer", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_transformer, args=(s, horizon), msg=f"Running Transformer for {s}...")


@router.get("/ensemble")
async def ensemble(symbol: str = "AAPL", horizon: int = 5):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, run_ensemble)
    s = validate_symbol(symbol)
    validate_range(horizon, "horizon", 1, 30)
    key = _cache_key("ensemble", symbol=s, horizon=horizon)
    return _serve_or_compute(key, run_ensemble, args=(s, horizon), msg=f"Running Ensemble for {s}...")


@router.get("/dqn")
async def dqn(symbol: str = "AAPL", episodes: int = 20):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, check_rate_limit, run_dqn)
    s = validate_symbol(symbol)
    validate_range(episodes, "episodes", 1, 100)
    if not check_rate_limit("dqn", 2):
        return [{"Status": "Rate limited", "Info": "Too many DQN requests. Try again in 1 minute."}]
    key = _cache_key("dqn", symbol=s, episodes=episodes)
    return _serve_or_compute(key, run_dqn, args=(s, episodes), msg=f"Running DQN for {s}...")


@router.get("/policy_gradient")
async def policy_gradient(symbol: str = "AAPL", episodes: int = 20):
    from halal_screener import (_serve_or_compute, _cache_key, validate_symbol,
        validate_range, check_rate_limit, run_policy_gradient)
    s = validate_symbol(symbol)
    validate_range(episodes, "episodes", 1, 100)
    if not check_rate_limit("policy_gradient", 2):
        return [{"Status": "Rate limited", "Info": "Too many Policy Gradient requests. Try again in 1 minute."}]
    key = _cache_key("policy_gradient", symbol=s, episodes=episodes)
    return _serve_or_compute(key, run_policy_gradient, args=(s, episodes), msg=f"Running Policy Gradient for {s}...")
