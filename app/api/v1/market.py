from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter

from app.services.memory_cache import memory_cache

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-market"])


@router.get("/market/context")
async def v1_market_context(force_refresh: bool = False):
    from app.services.market_context import get_market_context

    async def compute():
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(get_market_context, False),
                timeout=12.0,
            )
        except asyncio.TimeoutError:
            logger.warning("/api/v1/market/context exceeded 12s — returning stale shell")
            return {
                "vix": {}, "spy_regime": {}, "breadth": {}, "credit": {}, "liquidity": {},
                "stale": True, "error": "timeout",
                "cached_at": datetime.utcnow().isoformat(),
            }

    return await memory_cache.get_or_compute(
        "market:context", 300, compute, force_refresh=force_refresh
    )


@router.get("/market/status")
async def v1_market_status(force_refresh: bool = False):
    from app.services.market_context import get_market_status
    return get_market_status(force_refresh=force_refresh)


@router.get("/sectors/performance")
async def v1_sector_performance(force_refresh: bool = False):
    from app.services.sector_analysis import get_sector_performance

    async def compute():
        return get_sector_performance(force_refresh=False)

    return await memory_cache.get_or_compute(
        "sectors:performance", 600, compute, force_refresh=force_refresh
    )


# Map a registered model name → display name + design-system family class.
_MODEL_FAMILY = {
    "turtle":                  ("Turtle Trend",    "classic"),
    "moving_average":          ("Moving Average",  "classic"),
    "evolution_strategy":      ("Evolution Strat", "deep-rl"),
    "neuro_evolution_novelty": ("Neuro-Evolution", "deep-rl"),
    "transformer":             ("Transformer",     "transformer"),
    "ensemble":                ("Ensemble",        "ensemble"),
    "gru":                     ("GRU-Seq2Seq",     "gru"),
    "lstm_vae":                ("LSTM-VAE",        "lstm"),
    "dilated_cnn":             ("Dilated CNN",     "cnn"),
    "arima":                   ("ARIMA",           "statistical"),
}


@router.get("/models/leaderboard")
async def v1_models_leaderboard(force_refresh: bool = False):
    """Model leaderboard — real Sharpe / win-rate from the model registry.

    Reads model_registry/<name>/{production,staging}.json. Returns rows
    sorted by Sharpe desc, shaped for the Overview Models panel.
    """
    def _compute():
        from app.services import model_registry as mr

        rows = []
        reg_dir = mr._MODEL_REGISTRY_DIR
        if not reg_dir.exists():
            return []
        for entry in sorted(reg_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            prod = mr.resolve(name, "production")
            staging = mr.resolve(name, "staging")
            chosen = prod or staging
            if not chosen:
                continue
            metrics = chosen.get("metrics", {}) or {}
            display, fam = _MODEL_FAMILY.get(name, (name.replace("_", " ").title(), "classic"))
            rows.append({
                "name": display,
                "fam": fam,
                "status": "production" if prod else "staging",
                "sharpe": round(float(metrics.get("sharpe", 0) or 0), 2),
                "win": round(float(metrics["win_rate"]), 1) if metrics.get("win_rate") is not None else None,
                "trades": metrics.get("trades"),
                "avg_return": metrics.get("avg_return"),
                "version": chosen.get("version"),
                "spark": [],
            })
        rows.sort(key=lambda r: r["sharpe"], reverse=True)
        return rows

    async def compute():
        return await asyncio.to_thread(_compute)

    return await memory_cache.get_or_compute(
        "models:leaderboard", 300, compute, force_refresh=force_refresh
    )
