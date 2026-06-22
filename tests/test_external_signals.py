"""external_signals: shared analyst/insider/earnings used by the card AND the agent."""
from app.services import external_signals as es


def test_analyst_bearish_and_insider_heavy_sell(monkeypatch):
    from app.services import finnhub_client as fc
    from app.services import reference_data as rd
    monkeypatch.setattr(fc.finnhub_client, "get_recommendation",
        lambda s: [{"strongBuy": 1, "buy": 1, "hold": 11, "sell": 6, "strongSell": 2,
                    "period": "2026-06-01"}], raising=False)
    monkeypatch.setattr(fc.finnhub_client, "get_insider_transactions",
        lambda s: [{"transactionDate": "2026-06-10", "transactionCode": "S",
                    "change": -41932, "transactionPrice": 269.5, "name": "Exec X"}], raising=False)
    monkeypatch.setattr(fc.finnhub_client, "get_next_earnings", lambda s: None, raising=False)
    monkeypatch.setattr(rd, "get_earnings_date", lambda s: None, raising=False)

    out = es.get_external_signals("EXPD")
    assert out["analyst"]["known"] and out["analyst"]["rating"] == "hold"
    assert out["analyst"]["buy"] == 2 and out["analyst"]["sell"] == 8 and out["analyst"]["bearish"] is True
    assert out["insider"]["known"] and out["insider"]["heavy_sell"] is True
    assert out["insider"]["sell_value"] >= 1_000_000
    assert out["earnings"]["known"] is False


def test_all_unknown_when_no_data(monkeypatch):
    from app.services import finnhub_client as fc
    from app.services import reference_data as rd
    monkeypatch.setattr(fc.finnhub_client, "get_recommendation", lambda s: [], raising=False)
    monkeypatch.setattr(fc.finnhub_client, "get_insider_transactions", lambda s: [], raising=False)
    monkeypatch.setattr(fc.finnhub_client, "get_next_earnings", lambda s: None, raising=False)
    monkeypatch.setattr(rd, "get_earnings_date", lambda s: None, raising=False)
    out = es.get_external_signals("ZZZZ")
    assert out["analyst"]["known"] is False
    assert out["insider"]["known"] is False
    assert out["earnings"]["known"] is False
