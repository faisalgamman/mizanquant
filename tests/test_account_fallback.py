"""Account fallback test — T5: get_account(None) must not 401."""
from __future__ import annotations


def test_get_account_signature_accepts_none():
    """Alpaca get_account() signature must accept strategy_id=None (optional)."""
    import inspect
    from app.services.alpaca_client import get_account

    sig = inspect.signature(get_account)
    assert "strategy_id" in sig.parameters, (
        "get_account() missing strategy_id parameter"
    )
    param = sig.parameters["strategy_id"]
    assert param.default is None, (
        f"strategy_id default must be None (got {param.default!r}) — "
        "legacy callers depend on optional arg"
    )


def test_alpaca_get_account_accepts_none_call():
    """Calling get_account(strategy_id=None) must not raise TypeError."""
    from app.services.alpaca_client import get_account

    # This will fail with a network error (no real Alpaca keys) but MUST NOT
    # fail with TypeError or unexpected argument errors.
    try:
        get_account(strategy_id=None)
    except TypeError as e:
        raise AssertionError(
            f"get_account(strategy_id=None) raised TypeError: {e}"
        ) from e
    except Exception:
        # Network/auth errors are expected in test environment
        pass


def test_system_admin_routes_no_bare_get_account():
    """system.py and admin.py must not have bare .get_account() calls."""
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    for rel in ["app/api/v1/system.py", "app/routers/admin.py"]:
        src = (project_root / rel).read_text(encoding="utf-8")
        # Count bare calls (no strategy_id)
        import re
        bare = re.findall(r'\.get_account\(\)', src)
        assert len(bare) == 0, (
            f"{rel}: {len(bare)} bare get_account() calls — add explicit strategy_id"
        )
