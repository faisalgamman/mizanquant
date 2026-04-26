# Out-of-Scope — Intentionally Deferred

This is the standing register of work that is **clearly useful but
deliberately not in the current scope**. Each item here is a
significant architectural decision in its own right and deserves an
independent work cycle. Tracked here so nothing is forgotten and
nothing leaks in as scope creep.

The Chan refactor (Phases 1–8) is complete. Anything below this line
is post-graduation work.

| #  | Item | Why deferred | Re-open trigger |
|----|------|--------------|-----------------|
| 1  | **Pairs trading (Strategy D)** built on Phase 5 cointegration toolkit | Phase 5 shipped the math. A live pairs strategy still needs: a chosen pair, real-time spread tracking, z-score entry/exit logic, hedge-ratio drift management, position-level risk wiring | After A/B/C graduate on paper |
| 2  | **Hidden Markov Model regime detector** (deeper replacement for `app/services/regime.py`) | Current detector (VIX + SPY EMA + yield spread) is operationally sufficient. HMM needs training, stability validation, and an interpretation layer | When paper exposes regime tail-risk the current detector misses |
| 3  | **Walk-forward optimization** instead of single-window in-sample tuning | Phase 1.5 optimizer applies costs and DSR but is still in-sample. WFA guarantees out-of-sample robustness but costs 5–10× compute | After ≥6 months of continuous paper history is collected |
| 4  | **Interactive Brokers backend** | Phase A/B/C plan proposed (broker abstraction + IBKR Web API). Not yet started — awaiting user decisions on Pro/Lite, replace-vs-parallel, and timeline | When user answers the three IB scoping questions |
| 5  | **Order-book / L2 microstructure signals** (Chan §7.5) | Requires Level-2 depth-of-book data; Alpaca does not provide this as standard | When migrating to IB Pro (which exposes DOM) |
| 6  | **Detailed transaction-cost model** (elastic slippage, time-varying spread) | Current 20bps round-trip flat is a defensible conservative estimate. Refining it requires TAQ-grade tick data | When paper-vs-live divergence shows up on large trades |
| 7  | **Survivorship-free universe rebuild from Russell 1000 historical** | Phase 1.5 uses a fixed snapshot — acceptable for testing. Full fix needs CRSP/Refinitiv historical constituents | When formal backtest is compared against live account |
| 8  | **Cross-strategy correlation-aware Kelly** | Phase 2 sizes each strategy independently. Multi-strategy capital needs a covariance-adjusted Kelly across A+B+C | When A+B+C run on a single shared capital pool |
| 9  | **Anomaly / data-quality alerts** (gap detection, stale price, split-adjusted breaks) | Currently caught by hand when results look wrong | After the first paper-trade data incident |
| 10 | **Reproducibility seal** (git SHA + data hash recorded with every backtest result) | Useful for audit but not operationally critical today | When the first external review is requested |

## The Standing Rule

**Do not pull anything from this register before A/B/C have graduated
on paper (Phase 8 `paper_trade_status`).**

Adding complexity before the foundation is verified is the over-fitting
trap Chan warns about in Ch.2 — only there it manifests as
over-engineering rather than over-parameterization. Same disease.
