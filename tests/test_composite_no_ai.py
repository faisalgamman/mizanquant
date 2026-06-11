"""Phase 1: deep-picks composite excludes the unvalidated AI slice.

Tests the REAL renormalizer used by /api/screener/deep-picks (parts list of
(value, max) pairs — tech 30 / fund 25 / halal 10 / sentiment 20 when real),
plus a source tripwire so the AI slice can't silently rejoin the composite.
"""
import os
import re
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from app.workspace_server import _composite_from_parts  # noqa: E402


class TestCompositeFromParts:

    def test_full_marks_without_sentiment_is_100(self):
        # tech 30/30 + fund 25/25 + halal 10/10 → 65/65
        assert _composite_from_parts([(30, 30), (25, 25), (10, 10)]) == 100

    def test_full_marks_with_sentiment_is_100(self):
        assert _composite_from_parts([(30, 30), (25, 25), (10, 10), (20, 20)]) == 100

    def test_half_marks_is_50(self):
        assert _composite_from_parts([(15, 30), (12.5, 25), (5, 10)]) == 50

    def test_parts_only_no_ai_argument(self):
        """The AI slice is excluded BY CONSTRUCTION — the renormalizer only sees
        the parts the caller passes, and deep-picks never passes (ai, 15)."""
        parts = [(24, 30), (20, 25), (8, 10)]
        assert _composite_from_parts(parts) == round((24 + 20 + 8) / 65 * 100)

    def test_empty_parts_is_0(self):
        assert _composite_from_parts([]) == 0

    def test_caps_at_100(self):
        assert _composite_from_parts([(40, 30), (30, 25), (15, 10)]) == 100


class TestAiSliceStaysOut:

    def test_deep_picks_parts_exclude_ai(self):
        """Tripwire: the deep-picks parts list must NOT contain the AI slice.

        Phase-0 measured the AI sub-score's source models at ~coin-flip accuracy;
        Phase 1 removed `(ai, 15)` from the composite parts. If someone re-adds
        it without out-of-sample validation, this test fails loudly.
        """
        src_path = os.path.join(_PROJ, "app", "workspace_server.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"parts\s*=\s*\[(.*?)\]", src)
        assert m, "deep-picks parts list not found — composite structure changed, update this test"
        assert "(ai" not in m.group(1).replace(" ", ""), (
            "AI slice found back inside the composite parts — it must stay 0-weight "
            "until validated out-of-sample (Phase-0 measured its models at coin-flip)."
        )
        assert "ai_counted" in src, "ai_counted honesty flag missing from the deep-picks payload"
