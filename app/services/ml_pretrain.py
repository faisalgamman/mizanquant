"""Pre-train DQN and PG models for the Universe and cache results in DB.

Moves RL training out of the request path (S0.3). A background worker
pre-computes DQN/PolicyGradient results for all active Universe symbols
and stores them in ``ModelResultsCache``. API requests and consensus
calls then read from the DB cache instead of blocking on on-the-fly
training from scratch.

Usage (standalone):
    python -m app.worker.ml_pretrain [--max-symbols 200] [--episodes 10]

Usage (scheduler):
    from app.services.ml_pretrain import pretrain_universe
    pretrain_universe(max_symbols=200, episodes=10)
"""

from __future__ import annotations

import argparse
import logging
import time

from app.background.cache_manager import db_cache_get, db_cache_set
from app.db.models import Universe
from app.db.database import SessionLocal

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None

logger = logging.getLogger("worker")

_PRETRAIN_EPISODES = 20
_PRETRAIN_TTL = 6 * 3600  # 6 hours


def _pretrain_key(model: str, symbol: str) -> str:
    return f"pretrained_{model}|symbol={symbol.upper()}"


def pretrain_dqn(symbol: str, episodes: int = _PRETRAIN_EPISODES) -> list | None:
    """Train DQN for *symbol* and persist result in ``ModelResultsCache``.

    Returns the cached result if already fresh, otherwise trains and caches.
    """
    from halal_screener import run_dqn

    key = _pretrain_key("dqn", symbol)
    existing = db_cache_get(key)
    if existing is not None:
        return existing
    try:
        result = run_dqn(symbol.upper(), episodes)
        if result and not any(
            isinstance(r, dict) and "Error" in r for r in result
        ):
            db_cache_set(key, "dqn", result, ttl=_PRETRAIN_TTL)
            logger.info("Pre-trained DQN for %s (%d ep.)", symbol, episodes)
        return result
    except Exception as exc:
        logger.error("DQN pretrain failed for %s: %s", symbol, exc)
        if sentry_sdk:
            sentry_sdk.capture_exception(exc)
        return None


def pretrain_policy_gradient(
    symbol: str, episodes: int = _PRETRAIN_EPISODES
) -> list | None:
    """Train Policy Gradient for *symbol* and persist in ``ModelResultsCache``."""
    from halal_screener import run_policy_gradient

    key = _pretrain_key("policy_gradient", symbol)
    existing = db_cache_get(key)
    if existing is not None:
        return existing
    try:
        result = run_policy_gradient(symbol.upper(), episodes)
        if result and not any(
            isinstance(r, dict) and "Error" in r for r in result
        ):
            db_cache_set(key, "policy_gradient", result, ttl=_PRETRAIN_TTL)
            logger.info("Pre-trained Policy Gradient for %s (%d ep.)", symbol, episodes)
        return result
    except Exception as exc:
        logger.error("PG pretrain failed for %s: %s", symbol, exc)
        if sentry_sdk:
            sentry_sdk.capture_exception(exc)
        return None


def pretrain_symbol(
    symbol: str, episodes: int = _PRETRAIN_EPISODES
) -> tuple[list | None, list | None]:
    """Pre-train both DQN and Policy Gradient for a single symbol."""
    dqn = pretrain_dqn(symbol, episodes)
    pg = pretrain_policy_gradient(symbol, episodes)
    return dqn, pg


def pretrain_universe(
    max_symbols: int = 200,
    episodes: int = 10,
    on_batch_done: callable | None = None,
) -> int:
    """Iterate active Universe symbols and pre-train DQN + PG for each.

    Returns the number of models successfully cached.
    """
    from halal_screener import _universe_symbols

    symbols = _universe_symbols()[:max_symbols]
    if not symbols:
        logger.warning("Universe is empty — skipping pretrain")
        return 0

    logger.info(
        "Pre-training RL models for %d symbols (episodes=%d)",
        len(symbols),
        episodes,
    )
    trained = 0
    for idx, sym in enumerate(symbols):
        dqn_r, pg_r = pretrain_symbol(sym, episodes)
        if dqn_r is not None:
            trained += 1
        if pg_r is not None:
            trained += 1
        if on_batch_done is not None:
            on_batch_done(idx + 1, len(symbols), trained)
        time.sleep(1)  # rate-limit to avoid OOM
    logger.info("Pre-training complete: %d models cached", trained)
    return trained


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-train RL models for the trading universe"
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=200,
        help="Max symbols to train (default: 200)",
    )
    parser.add_argument(
        "--episodes",
        type=int,
        default=10,
        help="Training episodes per symbol (default: 10)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from app.db.database import init_db
    init_db()

    logger.info("Starting RL pretrain: max_symbols=%d episodes=%d", args.max_symbols, args.episodes)

    trained = pretrain_universe(
        max_symbols=args.max_symbols,
        episodes=args.episodes,
    )
    logger.info("Done — %d models cached", trained)


if __name__ == "__main__":
    main()
