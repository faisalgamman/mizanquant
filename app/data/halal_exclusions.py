"""Static halal-screening exclusion tables (pure data, no behaviour).

Extracted verbatim from the ``halal_screener`` monolith (M-E) so the lists live
in a named, independently-importable module. ``halal_screener`` re-exports these
under their original private names (``_HARAM_EXCLUDE`` / ``_SP500_DELISTED_HALAL``)
so every existing import keeps resolving unchanged.

These are SECTOR-level exclusions; each surviving symbol still passes full AAOIFI
screening in ``verify_halal``. The mutable runtime set of FMP-confirmed haram
tickers (``_VERIFIED_HARAM``) stays in ``halal_screener`` — it is state, not data.
"""

# S&P 500 names excluded at the sector level (interest-based finance, insurance,
# alcohol/tobacco, gambling, weapons, haram media, leveraged utilities/REITs,
# credit-interest fintech). Final halal status is still decided per-symbol.
HARAM_EXCLUDE = {
    # Major banks & financial services (interest-based income)
    "BAC", "C", "COF", "CFG", "FITB", "GS", "HBAN", "JPM", "KEY", "MS", "MTB",
    "PNC", "RF", "SCHW", "STT", "TFC", "USB", "WFC", "BK", "BEN", "BLK", "BX",
    "CBOE", "CME", "ICE", "IVZ", "KKR", "MSCI", "NDAQ", "SPGI", "TROW",
    # Insurance
    "ACGL", "AFL", "AIG", "AIZ", "ALL", "AJG", "BRO", "CB", "CI", "CINF",
    "CNC", "COR", "EG", "ELV", "ERIE", "GL", "HIG", "HUM", "L", "MET",
    "PFG", "PGR", "PRU", "RJF", "TRV", "UNH", "WRB", "WTW",
    # Alcohol & tobacco
    "BF.B", "STZ", "TAP", "MO", "PM", "SAM",
    # Gambling & casinos
    "WYNN", "LVS", "MGM", "CCL", "NCLH", "RCL",
    # Weapons/defense (controversial — kept some dual-use industrials)
    "LMT", "NOC", "GD", "RTX", "HII", "LHX", "BA",
    # Conventional media with haram content
    "FOX", "FOXA", "NFLX", "DIS", "WBD", "LYV",
    # Utilities — almost all fail AAOIFI debt screen (>33% debt/market cap)
    "AEE", "AEP", "AES", "ATO", "CEG", "CMS", "CNP", "D", "DTE", "DUK", "ED",
    "EIX", "ES", "ETR", "EVRG", "EXC", "FE", "LNT", "NEE", "NI", "NRG", "PCG",
    "PEG", "PPL", "SO", "SRE", "VST", "WEC", "XEL",
    # REITs — interest-based income structure, high leverage
    "AMT", "ARE", "AVB", "BXP", "CCI", "CPT", "DLR", "DOC", "EQIX", "EQR",
    "ESS", "EXR", "FRT", "HST", "INVH", "IRM", "KIM", "MAA", "O", "PLD",
    "PSA", "REG", "SBAC", "SPG", "UDR", "VICI", "VTR", "WELL",
    # Payment processors / fintech — significant interest income from credit
    "AXP", "SYF", "CPAY",
}

# Survivorship-bias mitigation: halal-compliant names removed from the S&P 500 in
# 2023-2025. Including them in backtests prevents inflating historical
# performance by testing only current winners.
SP500_DELISTED_HALAL = [
    # Removed 2024-2025
    "ATVI",  # Activision (acquired by MSFT)
    "DISH",  # Dish Network (merged with EchoStar)
    "FRC",   # First Republic (failed - but was in S&P)
    "SIVB",  # SVB Financial (failed)
    "LUMN",  # Lumen Technologies (removed)
    "VFC",   # VF Corporation (removed)
    "ALK",   # Alaska Air (removed)
    "NCLH",  # Norwegian Cruise (moved to haram)
    "SEE",   # Sealed Air (removed)
    "NWL",   # Newell Brands (removed)
    "OGN",   # Organon (removed)
    "PARA",  # Paramount (removed / merged)
    "SEDG",  # SolarEdge (removed)
    "ENPH",  # Enphase (removed)
    "BBWI",  # Bath & Body Works (removed)
    "CTLT",  # Catalent (acquired)
    "AAL",   # American Airlines (removed)
    # Removed 2023
    "TWTR",  # Twitter (acquired by Musk)
    "SBNY",  # Signature Bank (failed)
    "DISCA",  # Discovery (merged into WBD)
    "XLNX",  # Xilinx (acquired by AMD)
    "CERN",  # Cerner (acquired by Oracle)
    "FBHS",  # Fortune Brands Home (reorganized)
    "RE",    # Everest Group (ticker changed)
    "NLSN",  # Nielsen (taken private)
    "DRE",   # Duke Realty (acquired)
    "VIAC",  # ViacomCBS (now PARA)
]
