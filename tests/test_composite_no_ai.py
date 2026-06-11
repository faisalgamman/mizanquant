"""
Tests for Phase 1: _composite_from_parts — AI slice zeroed + renormalized.
"""
import sys
import os

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from app.workspace_server import _composite_from_parts  # noqa: E402


class TestCompositeNoAi:

    def test_max_all_three_with_sent_equals_100(self):
        """Full marks (30T + 25F + 20S) = 75/75 * 100 = 100. AI value irrelevant."""
        result = _composite_from_parts(30.0, 25.0, 20.0, 15.0)
        assert result == 100.0, f"Expected 100.0, got {result}"

    def test_half_each_equals_50(self):
        """Half marks (15T + 12.5F + 10S) = 37.5/75 * 100 = 50.0. AI ignored."""
        result = _composite_from_parts(15.0, 12.5, 10.0, 15.0)
        assert result == 50.0, f"Expected 50.0, got {result}"

    def test_ai_value_irrelevant(self):
        """AI=0 or AI=15 gives same composite."""
        a = _composite_from_parts(24.0, 20.0, 16.0, 0.0)
        b = _composite_from_parts(24.0, 20.0, 16.0, 15.0)
        assert a == b, f"AI should not affect composite: {a} != {b}"

    def test_no_sentiment_uses_55_denominator(self):
        """Without sentiment (sent=None): 30T + 25F = 55/55 = 100."""
        result = _composite_from_parts(30.0, 25.0, -1.0, 15.0)  # sent=-1 → treated as absent
        assert result == 100.0, f"Expected 100.0, got {result}"

    def test_sent_none_uses_55(self):
        """sent=None falls back to 55 denominator."""
        result = _composite_from_parts(27.5, 27.5, None, 10.0)  # 27.5+27.5=55/55=100
        assert result == 100.0, f"Expected 100.0, got {result}"

    def test_zero_all(self):
        """All zeros => 0."""
        result = _composite_from_parts(0.0, 0.0, 0.0, 0.0)
        assert result == 0.0
