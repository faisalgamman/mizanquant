"""ML forecast & RL endpoints — LSTM, Transformer, Ensemble, DQN, Policy Gradient
Each endpoint is wrapped with try/except so missing model weights or import
failures return a graceful error instead of a 500 crash.
"""
from fastapi import APIRouter
import logging

router = APIRouter(tags=["Forecast"])
logger = logging.getLogger("forecast_router")


def _safe_import(names: list[str]):
    """Import forecast functions safely — returns (funcs_dict, error_msg)."""
    from halal_screener import (
        _serve_or_compute, _cache_key, validate_symbol, validate_range, check_rate_limit,
    )
    base = {
        "_serve_or_compute": _serve_or_compute,
        "_cache_key": _cache_key,
        "validate_symbol": validate_symbol,
        "validate_range": validate_range,
        "check_rate_limit": check_rate_limit,
    }
    for name in names:
        try:
            base[name] = getattr(__import__("halal_screener", fromlist=[name]), name)
        except (AttributeError, ImportError) as e:
            logger.warning(f"Forecast router: cannot import {name}: {e}")
            base[name] = None
    return base


@router.get("/lstm")
async def lstm(symbol: str = "AAPL", horizon: int = 5):
    try:
        fns = _safe_import(["run_lstm"])
        if not fns.get("run_lstm"):
            return [{"Error": "LSTM module not available — missing dependencies or weights"}]
        s = fns["validate_symbol"](symbol)
        fns["validate_range"](horizon, "horizon", 1, 30)
        if not fns["check_rate_limit"]("lstm", 3):
            return [{"Status": "Rate limited", "Info": "Too many LSTM requests. Try again in 1 minute."}]
        key = fns["_cache_key"]("lstm", symbol=s, horizon=horizon)
        return fns["_serve_or_compute"](key, fns["run_lstm"], args=(s, horizon), msg=f"Running LSTM for {s}...")
    except Exception as e:
        logger.error(f"LSTM endpoint failed: {e}")
        return [{"Error": f"LSTM endpoint error: {e}"}]


@router.get("/transformer")
async def transformer(symbol: str = "AAPL", horizon: int = 5):
    try:
        fns = _safe_import(["run_transformer"])
        if not fns.get("run_transformer"):
            return [{"Error": "Transformer module not available — missing dependencies or weights"}]
        s = fns["validate_symbol"](symbol)
        fns["validate_range"](horizon, "horizon", 1, 30)
        if not fns["check_rate_limit"]("transformer", 3):
            return [{"Status": "Rate limited", "Info": "Too many Transformer requests. Try again in 1 minute."}]
        key = fns["_cache_key"]("transformer", symbol=s, horizon=horizon)
        return fns["_serve_or_compute"](key, fns["run_transformer"], args=(s, horizon), msg=f"Running Transformer for {s}...")
    except Exception as e:
        logger.error(f"Transformer endpoint failed: {e}")
        return [{"Error": f"Transformer endpoint error: {e}"}]


@router.get("/ensemble")
async def ensemble(symbol: str = "AAPL", horizon: int = 5):
    try:
        fns = _safe_import(["run_ensemble"])
        if not fns.get("run_ensemble"):
            return [{"Error": "Ensemble module not available — missing dependencies or weights"}]
        s = fns["validate_symbol"](symbol)
        fns["validate_range"](horizon, "horizon", 1, 30)
        key = fns["_cache_key"]("ensemble", symbol=s, horizon=horizon)
        return fns["_serve_or_compute"](key, fns["run_ensemble"], args=(s, horizon), msg=f"Running Ensemble for {s}...")
    except Exception as e:
        logger.error(f"Ensemble endpoint failed: {e}")
        return [{"Error": f"Ensemble endpoint error: {e}"}]


@router.get("/dqn")
async def dqn(symbol: str = "AAPL", episodes: int = 20):
    try:
        fns = _safe_import(["run_dqn"])
        if not fns.get("run_dqn"):
            return [{"Error": "DQN module not available — missing dependencies or weights"}]
        s = fns["validate_symbol"](symbol)
        fns["validate_range"](episodes, "episodes", 1, 100)
        if not fns["check_rate_limit"]("dqn", 2):
            return [{"Status": "Rate limited", "Info": "Too many DQN requests. Try again in 1 minute."}]
        key = fns["_cache_key"]("dqn", symbol=s, episodes=episodes)
        return fns["_serve_or_compute"](key, fns["run_dqn"], args=(s, episodes), msg=f"Running DQN for {s}...")
    except Exception as e:
        logger.error(f"DQN endpoint failed: {e}")
        return [{"Error": f"DQN endpoint error: {e}"}]


@router.get("/policy_gradient")
async def policy_gradient(symbol: str = "AAPL", episodes: int = 20):
    try:
        fns = _safe_import(["run_policy_gradient"])
        if not fns.get("run_policy_gradient"):
            return [{"Error": "Policy Gradient module not available — missing dependencies or weights"}]
        s = fns["validate_symbol"](symbol)
        fns["validate_range"](episodes, "episodes", 1, 100)
        if not fns["check_rate_limit"]("policy_gradient", 2):
            return [{"Status": "Rate limited", "Info": "Too many Policy Gradient requests. Try again in 1 minute."}]
        key = fns["_cache_key"]("policy_gradient", symbol=s, episodes=episodes)
        return fns["_serve_or_compute"](key, fns["run_policy_gradient"], args=(s, episodes), msg=f"Running Policy Gradient for {s}...")
    except Exception as e:
        logger.error(f"Policy Gradient endpoint failed: {e}")
        return [{"Error": f"Policy Gradient endpoint error: {e}"}]
