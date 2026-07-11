"""Tests for core.market_summary: rule-based summary correctness and never-blank guarantee."""

from core.market_summary import MarketSnapshot, rule_based_summary, summarize_market


def test_rule_based_summary_mentions_all_provided_indices():
    snapshot = MarketSnapshot(nifty_pct=0.012, sensex_pct=-0.005, banknifty_pct=0.02)
    summary = rule_based_summary(snapshot)

    assert "Nifty 50" in summary and "up 1.2%" in summary
    assert "Sensex" in summary and "down 0.5%" in summary
    assert "Bank Nifty" in summary and "up 2.0%" in summary


def test_rule_based_summary_handles_missing_indices_gracefully():
    snapshot = MarketSnapshot()
    summary = rule_based_summary(snapshot)

    assert summary
    assert "not available" in summary.lower() or "isn't available" in summary.lower()


def test_rule_based_summary_includes_movers():
    snapshot = MarketSnapshot(nifty_pct=0.01, top_gainer=("WIPRO.NS", 0.05), top_loser=("ITC.NS", -0.03))
    summary = rule_based_summary(snapshot)

    assert "WIPRO.NS" in summary
    assert "up 5.0%" in summary
    assert "ITC.NS" in summary
    assert "down 3.0%" in summary


def test_rule_based_summary_omits_loser_when_it_is_actually_a_gain():
    # top_loser is "the smallest 1D% in the watchlist" -- if even that is positive, don't
    # mislabel it as a loss.
    snapshot = MarketSnapshot(top_loser=("TCS.NS", 0.01))
    summary = rule_based_summary(snapshot)

    assert "TCS.NS" not in summary


def test_summarize_market_falls_back_to_rule_based_without_api_key(monkeypatch):
    monkeypatch.setattr("core.market_summary.GEMINI_API_KEY", "")
    snapshot = MarketSnapshot(nifty_pct=0.01)

    summary, used_gemini = summarize_market(snapshot)

    assert used_gemini is False
    assert summary


def test_summarize_market_falls_back_when_gemini_raises(monkeypatch):
    monkeypatch.setattr("core.market_summary.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "core.market_summary._gemini_summary",
        lambda snapshot: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    snapshot = MarketSnapshot(nifty_pct=0.01)

    summary, used_gemini = summarize_market(snapshot)

    assert used_gemini is False
    assert summary
