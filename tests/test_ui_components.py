"""Tests for core.ui_components's shared rendering helpers."""

from datetime import datetime, timezone

from core.ml.confidence import assess_confidence
from core.ml.prediction_service import PredictionResult
from core.ui_components import render_empty_state, render_prediction_result


class _FakeColumn:
    def __init__(self, metric_calls: list) -> None:
        self._metric_calls = metric_calls

    def metric(self, *args, **kwargs) -> None:
        self._metric_calls.append(args)


def test_render_prediction_result_removes_the_chances_card_structurally(monkeypatch):
    """Regression test for the Chances/Probability feature removal (ML Signals):
    the top metric row must request exactly 2 columns (Direction, Confidence) --
    never 3 -- and no .metric() call anywhere may be labeled "Chances" or
    "Probability (Up / Down)". Asserting on the st.columns() call arguments (not just
    absence of a label) confirms this is a structural component-tree change, not a
    CSS-hidden element still present in the render tree."""
    result = PredictionResult(symbol="TEST.NS", generated_at=datetime.now(timezone.utc))
    result.confidence = assess_confidence(0.7, was_calibrated=True)
    result.model_source = "in_app_fallback"  # keeps every optional branch (risk/
    # explanation/recommendation/freshness/drift) at its default None, so only the
    # top metric row actually renders -- exactly the surface this test targets.

    columns_calls: list = []
    metric_calls: list = []

    def fake_columns(spec):
        columns_calls.append(spec)
        return [_FakeColumn(metric_calls) for _ in spec]

    monkeypatch.setattr("core.ui_components.st.columns", fake_columns)

    for mode in ("Simple", "Professional"):
        columns_calls.clear()
        metric_calls.clear()
        render_prediction_result(result, mode)

        assert columns_calls[0] == [1, 1], f"expected exactly 2 columns for mode={mode}, got {columns_calls[0]}"
        labels = [call[0] for call in metric_calls if call]
        assert "Chances" not in labels, f"Chances label still rendered in mode={mode}"
        assert "Probability (Up / Down)" not in labels, f"Probability label still rendered in mode={mode}"
        assert len(metric_calls) == 2, f"expected exactly 2 metric() calls (Direction, Confidence) for mode={mode}, got {len(metric_calls)}"


def test_render_empty_state_escapes_html_special_characters(monkeypatch):
    """Regression test for a defensive XSS-hardening fix (Production Stabilization
    Phase 5b): render_empty_state interpolates title/body into raw HTML via
    st.markdown(unsafe_allow_html=True). No current call site passes user-controlled
    text, but the function itself must escape its inputs so a *future* call site
    can never introduce an XSS vector by passing unescaped user text through."""
    captured = {}

    def fake_markdown(body, **kwargs):
        captured["body"] = body

    monkeypatch.setattr("core.ui_components.st.markdown", fake_markdown)

    render_empty_state(
        title="<script>alert('title')</script>",
        body="<img src=x onerror=alert('body')>",
    )

    assert "<script>" not in captured["body"]
    assert "<img" not in captured["body"]
    assert "&lt;script&gt;alert(&#x27;title&#x27;)&lt;/script&gt;" in captured["body"]
    assert "&lt;img src=x onerror=alert(&#x27;body&#x27;)&gt;" in captured["body"]


def test_render_empty_state_preserves_plain_text_and_emoji(monkeypatch):
    captured = {}

    def fake_markdown(body, **kwargs):
        captured["body"] = body

    monkeypatch.setattr("core.ui_components.st.markdown", fake_markdown)

    render_empty_state(title="No holdings yet", body="Add a stock to get started.", icon="\U0001F4BC")

    assert "No holdings yet" in captured["body"]
    assert "Add a stock to get started." in captured["body"]
    assert "\U0001F4BC" in captured["body"]
