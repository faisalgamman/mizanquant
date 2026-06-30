"""Gold instruments — classification + a gold-specific Shariah note.

Gold has its OWN ruling, not the equity debt-ratio screen: AAOIFI Shariah
Standard 57 (Gold) requires a spot trade with possession (qabd). So:

  • Paper-gold ETFs (a claim on a trust, no physical possession) are DEBATED and
    avoided by many scholars — we must NOT stamp them "compliant" via the equity
    screen. They get a gold-specific 'uncertain' note.
  • Gold MINER equities are ordinary shares: the debt/receivables ratios DO apply,
    so they flow through the normal halal + fundamental pipeline unchanged — a
    halal-friendly way to get gold exposure.
"""

from __future__ import annotations

# Paper-gold / physically-backed ETFs (still a fund claim, not personal qabd).
GOLD_ETFS = {"GLD", "IAU", "SGOL", "GLDM", "IAUM", "BAR", "AAAU", "OUNZ", "GLTR", "PHYS"}
# Liquid, halal-screenable gold MINER equities (the normal equity screen applies).
GOLD_MINERS = {"NEM", "GOLD", "AEM", "KGC", "AU", "GFI", "HMY", "RGLD", "WPM", "FNV", "PAAS"}

_ETF_NOTE = (
    "ذهب ورقي (مطالبة على صندوق) — مختلَف فيه شرعاً؛ معيار AAOIFI 57 يشترط الفور "
    "والقبض. الأنسب: ذهب مادي/مخصّص أو أسهم مناجم. فلتر الأسهم (نِسب الدَّيْن) لا "
    "ينطبق على سلعة."
)
_MINER_NOTE = "سهم منجم ذهب — يمرّ بالفلتر الشرعي والأساسي كسهم عادي (تعرّض حلال للذهب)."


def gold_instrument(symbol: str) -> dict | None:
    """Classify a gold-related symbol.

    Returns None for non-gold symbols. For gold ETFs → a dict carrying an
    'uncertain' three-state verdict + the paper-gold note (the equity screen is
    bypassed). For gold miners → kind='miner' with halal_verdict None, signalling
    "use the normal equity screen".
    """
    s = (symbol or "").upper().strip()
    if s in GOLD_ETFS:
        return {"is_gold": True, "kind": "etf", "halal_verdict": "uncertain",
                "fundamentals_apply": False, "note": _ETF_NOTE}
    if s in GOLD_MINERS:
        return {"is_gold": True, "kind": "miner", "halal_verdict": None,
                "fundamentals_apply": True, "note": _MINER_NOTE}
    return None


__all__ = ["gold_instrument", "GOLD_ETFS", "GOLD_MINERS"]
