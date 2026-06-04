# AGENTS.md — MizanQuant

> **AI agent context file.** Read this before working on the project.
> Last updated: 2026-06-04

---

## 1. PROJECT IDENTITY

**MizanQuant** is a Halal algorithmic trading platform. It screens US equities for Shariah compliance (AAOIFI), runs deep-learning forecast models, executes paper-trades via Alpaca/IBKR, and presents a real-time dashboard.

- **Primary user:** Faisal (Arabic-speaking quant trader)
- **Language convention:** Respond in Arabic when the user writes in Arabic
- **Host:** Windows 11. Project files accessed via WSL at .
- **Server:** FastAPI + Uvicorn on port 6910 (, 7,500+ lines)
- **DB:** SQLAlchemy 2.0 (SQLite dev / PostgreSQL Railway)
- **Brokers:** Alpaca (paper) + IBKR (ib_insync)

---

## 2. FILE LAYOUT (key files only)

### Core
| File | Role |
|------|------|
|  | Main FastAPI server — all endpoints, _enrich_one, scheduler |
|  | pydantic-settings — all feature flags + credentials |
|  | AIAgent class — bilingual trading copilot (Anthropic Claude) |
|  | SQLAlchemy SessionLocal, engine |
|  | All DB models (TradeHistory, AgentDecision, TradingRulebook, Universe, etc.) |

### Services — Selection & Conviction (Chapter 1)
| File | Role |
|------|------|
|  | compute_conviction, detect_confirmations, apply_strong_buy_gate, suggested_position_size |
|  | Point-in-time backtest: run_selection_backtest() — CURRENT vs ENHANCED |
|  | get_source_weights, get_overall_health, adaptive_gate_delta |
|  | deflated_sharpe, permutation test, reality check |

### Services — Signal Capture (Chapters 2-3)
| File | Role |
|------|------|
|  | detect_pullback, detect_breakout, detect_reversal, detect_gap_go, detect_archetype |
|  | Stage 1 filter, composite bridge, intraday confirm, archetype UNION |
|  | USX V4 scoring, near-miss collection, earnings gate, df injection |
|  | apply_context_shadow, get_symbol_sectors, context multiplier |
|  | Gate logic, adaptive gate governor |
|  | fetch() — IBKR->Alpaca->Tiingo->yfinance chain; fetch_alpaca_batch, fetch_alpaca_intraday |

### Services — Agent Learning Loop
| File | Role |
|------|------|
|  | store_rationale, trigger_reflection (24h cooldown), _extract_rules, get_active_rules, scan_and_reflect |
|  | Claude chat handler — _build_system_prompt() with dynamic rule injection |
|  | 11 tools: get_deep_picks, get_accuracy_report, record_recommendation, etc. |
|  | execute_buy/sell — agent_decision_id passed to TradeHistory |

### Supporting
| File | Role |
|------|------|
|  | Position sizing |
|  | 7x daily signal scans |
|  | get_accuracy_report, check_signal_outcomes |
|  | HALAL_STOCKS_FALLBACK — 657 halal symbols |
|  | Stop-loss, target, hold horizon |
|  | Telegram alerts |
|  | AAOIFI compliance checks |

---

## 3. FEATURE FLAGS (all OFF by default)

All new features are gated behind independent flags that default to . **No flag is raised without backtest validation** (DSR >= 0.95, permutation p < 0.05, reality check LB > 0, walk-forward >= 3 windows).

### Chapter 1 — Conviction Engine
| Flag | Controls | Status |
|------|----------|--------|
|  | Context-conditioned ranking | OFF (backtest proved anti-predictive: delta -18.85%) |
|  | Kelly sizing + risk-adjusted ranking | OFF |
|  | Auto-tighten min/strong gates on drawdown | OFF |

### Chapter 3 — Archetypes & Signal Quality
| Flag | Controls | Status |
|------|----------|--------|
|  | Pullback detector in live signal funnel | OFF |
|  | Breakout detector in live signal funnel | OFF |
|  | Reversal detector in live signal funnel | OFF |
|  | Enrich signals with deep-picks fields + 3-confirmation gate | OFF |
|  | 15-min intraday confirmation + gap_go filter | OFF |
|  | Block symbols with missing earnings data | True (safe default) |

### Backtest Findings
- **SELECTION_CONDITIONING_LIVE should remain OFF permanently** — ENHANCED (context-conditioned) underperforms CURRENT (raw technical) by -18.85% mean return with DSR dropping from 1.000 to 0.098.
- Archetype detectors (pullback/breakout/reversal) tested but results are identical across archetypes with current small sample sizes.

---

## 4. CODING CONVENTIONS

### File operations
- **CRLF line endings** () — all project files use Windows line endings. When editing from WSL, normalize to LF first, then convert back to CRLF before writing.
- ** tool fails on  paths** — use  + / instead.
- ** tool fails on paths with spaces** — use  + Python for edits.
- All terminal commands from WSL need  due to space in "TECH VALLEY" path.

### Code style
- **Pure functions** where possible — no side effects, batch fetching.
- **Surgical Coding** — touch only required files, preserve existing code style.
- Always run  after every edit.
- Service files use  (NOT ).

### DB conventions
-  -> FK to  (nullable).
- Agent reflection has 24h cooldown to prevent premature conclusions.
- Inferred rules are injected dynamically into SYSTEM_PROMPT via  before every agent conversation.

---

## 5. RECENT ADDITIONS (May 2026)

1. **Conviction Engine** — conviction_engine.py, validation_harness.py, model_weights.py
2. **Signal Archetypes** — 4 detectors (pullback, breakout, reversal, gap_go)
3. **Composite Bridge** — deep-picks enrichment + 3-confirmation STRONG BUY gate
4. **Intraday Layer** — 15-min confirmation + gap_go UNION
5. **Scanner Improvements** — trade_plan (stop/tp/hold), sort_by=rank_value, exit_guidance, near-miss endpoint
6. **Agent Learning Loop** — AgentDecision/TradingRulebook tables, agent_reflection.py, dynamic SYSTEM_PROMPT, 11 Claude tools
7. **Sector fallback fix** —  now tries: Universe DB -> FMPCache DB -> FMP live -> yfinance

---

## 6. KEY ENDPOINTS

| Endpoint | Purpose |
|----------|---------|
|  | Top halal picks with trade_plan, exit_guidance |
|  | Risk-adjusted ranking |
|  | Symbols that nearly passed (score 60-64) |
|  | Paired CURRENT vs ENHANCED backtest |
|  | Scanner progress |
|  | Trigger halal universe scan |

---

## 7. KNOWN PITFALLS

1. **FMP/Tiingo often down** — data providers have rate limits. Alpaca is the reliable fallback.
2. **yfinance circuit breaker** — 3-stage (closed→throttle→open). 50 failures → THROTTLE (1s delay). 150 → OPEN (skip all, 5 min). Auto-blacklists delisted symbols. No more 48% scan loss.
3. **Screener hangs** — typically rate limit on last ~50 symbols. Scanner can still serve partial results.
4. **WSL <-> Windows networking** — WSL can reach Windows host at  (gateway IP), not .
5. **No hot-reload** — server must be restarted after code changes.
6. **Alpaca feed parameter** — DO NOT hardcode feed=iex/sip. Omit the feed param entirely; Alpaca auto-selects best available. feed=iex caused 404 for NVDA, AMD + 300 other symbols on paper accounts.
7. **fetch_alpaca_batch bug (FIXED)** — had NameError (valid_syms undefined) + missing url variable. Now fixed with yfinance fallback for paper mode.
8. **httpx log spam** — httpx + httpcore loggers set to WARNING in workspace_server.py (was INFO, flooded logs with HTTP requests).
