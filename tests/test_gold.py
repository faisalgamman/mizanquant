"""Gold instrument classification — the gold-specific Shariah handling."""

from app.services.gold import gold_instrument


def test_gold_etf_is_uncertain_and_bypasses_equity_screen():
    g = gold_instrument("GLD")
    assert g is not None
    assert g["kind"] == "etf"
    assert g["halal_verdict"] == "uncertain"      # paper gold → debated, never a hard verdict
    assert g["fundamentals_apply"] is False        # commodity, not an operating company
    assert "قبض" in g["note"]                       # cites possession (AAOIFI 57)


def test_gold_miner_uses_normal_equity_screen():
    g = gold_instrument("NEM")
    assert g is not None
    assert g["kind"] == "miner"
    assert g["halal_verdict"] is None              # signal: use the equity screen
    assert g["fundamentals_apply"] is True


def test_non_gold_symbol_returns_none():
    assert gold_instrument("AAPL") is None
    assert gold_instrument("") is None
    assert gold_instrument(None) is None


def test_case_insensitive():
    assert gold_instrument("iau")["kind"] == "etf"
    assert gold_instrument("  gold  ")["kind"] == "miner"
