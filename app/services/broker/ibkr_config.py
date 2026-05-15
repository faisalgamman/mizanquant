"""Single source of truth for IBKR (Interactive Brokers) connection config.

Root-cause fix for the Railway misconfiguration where IBKR_PORT=4004 (invalid)
and IBKR_HOST pointed to a stopped service went unnoticed.

Every caller that needs IBKR host/port MUST use `get_ibkr_config()` instead of
reading os.environ directly — this guarantees:
  - one unified default (4002, not the inconsistent 7497 some callers used)
  - validation: invalid ports (like 4004) are rejected with a clear log error
  - a single place to evolve the logic
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("screener")

# The ONLY valid IBKR socket ports.
VALID_IBKR_PORTS = {
    4001: "IB Gateway — Live",
    4002: "IB Gateway — Paper",
    7496: "TWS Desktop — Live",
    7497: "TWS Desktop — Paper",
}

# Unified default — Railway IB Gateway paper trading.
DEFAULT_IBKR_PORT = 4002
DEFAULT_IBKR_HOST = "127.0.0.1"
DEFAULT_IBKR_CLIENT_ID = 1


def get_ibkr_config() -> dict:
    """Return validated IBKR connection config. Single source for ALL callers.

    Returns a dict: {"host": str, "port": int, "client_id": int, "mode": str}
    Invalid IBKR_PORT values are rejected and replaced with DEFAULT_IBKR_PORT.
    """
    host = os.environ.get("IBKR_HOST", DEFAULT_IBKR_HOST).strip() or DEFAULT_IBKR_HOST

    raw_port = os.environ.get("IBKR_PORT", str(DEFAULT_IBKR_PORT)).strip()
    try:
        port = int(raw_port)
    except (ValueError, TypeError):
        logger.error(
            "IBKR_PORT=%r is not a number — falling back to default %d",
            raw_port, DEFAULT_IBKR_PORT,
        )
        port = DEFAULT_IBKR_PORT

    # Port validation — reject anything not in the known-good set.
    if port not in VALID_IBKR_PORTS:
        logger.error(
            "IBKR_PORT=%d is invalid! Allowed ports: %s. Falling back to %d.",
            port, sorted(VALID_IBKR_PORTS.keys()), DEFAULT_IBKR_PORT,
        )
        port = DEFAULT_IBKR_PORT

    raw_client_id = os.environ.get("IBKR_CLIENT_ID", str(DEFAULT_IBKR_CLIENT_ID)).strip()
    try:
        client_id = int(raw_client_id)
    except (ValueError, TypeError):
        logger.error(
            "IBKR_CLIENT_ID=%r is not a number — falling back to %d",
            raw_client_id, DEFAULT_IBKR_CLIENT_ID,
        )
        client_id = DEFAULT_IBKR_CLIENT_ID

    return {
        "host": host,
        "port": port,
        "client_id": client_id,
        "mode": VALID_IBKR_PORTS.get(port, "unknown"),
    }
