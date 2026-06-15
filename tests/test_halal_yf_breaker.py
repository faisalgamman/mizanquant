"""yfinance fundamentals circuit breaker in halal_screening.

When Yahoo blocks the server IP every yfinance fundamentals call burns its full
timeout; on a 657-symbol screen that turns a ~12-min scan into an endless crawl.
The breaker fails fast after N consecutive misses so the scan COMPLETES (degraded)
instead of hanging — and self-heals on success / after the cooldown.
"""
import app.services.halal_screening as hs


def _reset():
    with hs._YF_CB_LOCK:
        hs._YF_CB["fails"] = 0
        hs._YF_CB["until"] = 0.0


def test_breaker_opens_after_threshold_misses(monkeypatch):
    _reset()
    # Every yfinance attempt misses (timeout → None).
    monkeypatch.setattr("app.services.market_context._run_with_timeout",
                        lambda fn, timeout, fallback: None)
    for _ in range(hs._YF_CB_THRESHOLD):
        assert hs._yf_fallback("AAA") is None
    # Threshold reached → circuit is now OPEN.
    assert hs._yf_cb_is_open()


def test_open_breaker_short_circuits_without_calling_yf(monkeypatch):
    _reset()
    with hs._YF_CB_LOCK:
        hs._YF_CB["until"] = hs.time.time() + 999  # force open

    def _boom(*a, **k):
        raise AssertionError("yfinance must NOT be called while breaker is open")

    monkeypatch.setattr("app.services.market_context._run_with_timeout", _boom)
    assert hs._yf_fallback("BBB") is None  # returns immediately, no yf call


def test_success_resets_failures(monkeypatch):
    _reset()
    seq = [None, None, {"profile": {}, "bs": {}, "income": {}}]
    monkeypatch.setattr("app.services.market_context._run_with_timeout",
                        lambda fn, timeout, fallback: seq.pop(0))
    hs._yf_fallback("C")            # miss → fails=1
    hs._yf_fallback("C")            # miss → fails=2
    assert hs._yf_fallback("C") is not None  # hit → fails reset to 0
    with hs._YF_CB_LOCK:
        assert hs._YF_CB["fails"] == 0
