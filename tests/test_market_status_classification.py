"""Test that market status classification handles VIX×pctile correctly."""
from unittest.mock import patch
from app.services.market_context import get_market_status


def test_low_vix_high_pctile_is_not_extreme():
    """VIX=17 + pctile=85% should be RISK ON or CAUTION, not CREDIT STRESS."""
    with patch("app.services.market_context.get_vix_context") as mock_vix, \
         patch("app.services.market_context.get_credit_ratio") as mock_credit:
        mock_vix.return_value = {"vix": 17.5, "vix_pctile": 85.0}
        mock_credit.return_value = {"classification": "ok", "daily_change_pct": 0.1}
        result = get_market_status(force_refresh=True)
        assert result["status"] in ("RISK ON", "CAUTION"), \
            f"Expected RISK ON/CAUTION but got {result['status']}"


def test_high_vix_low_pctile_is_caution_or_stress():
    """VIX=35 + pctile=40% should still be CAUTION/STRESS due to absolute value."""
    with patch("app.services.market_context.get_vix_context") as mock_vix, \
         patch("app.services.market_context.get_credit_ratio") as mock_credit:
        mock_vix.return_value = {"vix": 35.0, "vix_pctile": 40.0}
        mock_credit.return_value = {"classification": "ok", "daily_change_pct": 0.0}
        result = get_market_status(force_refresh=True)
        assert result["status"] in ("CAUTION", "CREDIT STRESS")


def test_very_high_vix_is_extreme_fear():
    """VIX > 70 should always be EXTREME FEAR regardless of pctile."""
    with patch("app.services.market_context.get_vix_context") as mock_vix, \
         patch("app.services.market_context.get_credit_ratio") as mock_credit:
        mock_vix.return_value = {"vix": 72.0, "vix_pctile": 50.0}
        mock_credit.return_value = {"classification": "ok", "daily_change_pct": 0.0}
        result = get_market_status(force_refresh=True)
        assert result["status"] == "EXTREME FEAR"
