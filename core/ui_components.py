"""The single shared "search for an NSE stock" widget, used everywhere a stock can be
picked or added (Market Overview watchlist, Stock Analysis, Portfolio, AI Sentiment,
ML Signals) so the experience is identical across the app and the user never has to
type a `.NS`/`.BO` suffix anywhere.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from core.ai_explain import generate_ai_panel
from core.components.stock_autocomplete import reset_autocomplete, stock_autocomplete
from core.config import get_logger
from core.explain import Explanation, PREDICTION_DISCLAIMER
from core.formatting import format_inr
from core.ml.registry import list_registry_entries
from core.portfolio import get_all_held_symbols
from core.universe import UniverseEntry, display_symbol
from core.watchlist import get_all_watchlist_symbols

logger = get_logger(__name__)

# yfinance/BENCHMARK symbol (core.config.BENCHMARK_*) -> Kotak Neo's index display
# name (core.kotak_market_data.INDEX_SYMBOLS key). Both sides of this map already
# refer to the same three real-world indices; this is purely a naming bridge
# between the two data sources, not a new symbol registry of its own.
KOTAK_INDEX_MAP: dict[str, str] = {
    "^NSEI": "NIFTY 50",
    "^BSESN": "SENSEX",
    "^NSEBANK": "NIFTY BANK",
}

SEARCH_PLACEHOLDER = "e.g. Reliance, TCS, INFY, HDFC Bank..."

MOOD_ICON = {"good": "\U0001F7E2", "worried": "\U0001F534", "neutral": "⚪"}

RECENT_SEARCHES_KEY = "recent_searches"
RECENT_SEARCHES_MAX = 8


def _record_recent_search(symbol: str) -> None:
    """Track resolved symbols across the whole session (any page's search box) for the
    home dashboard's "Recent Searches" panel. Most-recent-first, deduped, capped."""
    recent = st.session_state.setdefault(RECENT_SEARCHES_KEY, [])
    if symbol in recent:
        recent.remove(symbol)
    recent.insert(0, symbol)
    del recent[RECENT_SEARCHES_MAX:]


def _search_context() -> dict:
    """Real personalization signals for `search_stocks`' `context` argument -- session
    recent-searches plus actual watchlist/portfolio membership from the DB. No new
    tracking infrastructure: every signal here already existed for another purpose."""
    return {
        "recent_searches": list(st.session_state.get(RECENT_SEARCHES_KEY, [])),
        "watchlist_symbols": get_all_watchlist_symbols(),
        "portfolio_symbols": get_all_held_symbols(),
    }


def stock_search_and_pick(
    key: str,
    label: str = "Search for a stock",
    placeholder: str = SEARCH_PLACEHOLDER,
) -> UniverseEntry | None:
    """Live search-as-you-type over the full NSE universe, via the shared
    `StockAutocomplete` component (highlighting, Watchlist/Portfolio badges, keyboard
    nav, and ARIA combobox semantics all live in the component itself -- this
    function only wires it to real app data and this call site's return contract).

    Returns the entry the user just selected, or None until they pick one. This is
    the one-shot "find a stock to add" building block (watchlist, portfolio
    holdings). For a persistent single-stock picker that a whole page revolves
    around, use `stock_picker` instead.
    """
    context = _search_context()
    picked = stock_autocomplete(key, label=label, placeholder=placeholder, context=context)
    if picked is None:
        return None
    _record_recent_search(picked.entry.symbol)
    return picked.entry


def reset_stock_search(key: str) -> None:
    """Clear a `stock_search_and_pick` widget's search box/selection. Call this right
    after successfully acting on its returned entry (e.g. after `add_holding(...)`
    succeeds) so the box returns to empty instead of continuing to offer the same
    already-used pick on every subsequent rerun -- see
    `core.components.stock_autocomplete`'s module docstring for why the selection is
    persistent rather than one-shot, which is what makes this explicit reset needed."""
    reset_autocomplete(key)


def stock_picker(
    key: str,
    default_symbol: str,
    label: str = "Search for a stock",
    placeholder: str = SEARCH_PLACEHOLDER,
) -> str:
    """A persistent, page-level "which stock am I looking at" search box, backed by
    the same shared `StockAutocomplete` component as `stock_search_and_pick`.

    Remembers the resolved symbol in `st.session_state[key]` across reruns (e.g. after
    clicking an action button on the same page), defaulting to `default_symbol` until
    the user searches for something else. Returns the canonical `.NS`/`.BO` symbol --
    callers never see or need to construct a suffix.
    """
    if key not in st.session_state:
        st.session_state[key] = default_symbol

    context = _search_context()
    picked = stock_autocomplete(f"{key}_autocomplete", label=label, placeholder=placeholder, context=context)
    if picked is not None:
        st.session_state[key] = picked.entry.symbol
        _record_recent_search(picked.entry.symbol)

    return st.session_state[key]


MODE_SIMPLE = "Simple"
MODE_PROFESSIONAL = "Professional"


def render_mode_toggle() -> str:
    """Sidebar Simple/Professional mode toggle, shared and persisted across every page
    for the whole browser session. Defaults to Simple Mode -- the spec's explicit bar
    is that a 10-year-old with no finance background can use this app confidently, so
    plain-language explanations are what a first-time visitor sees unless they opt into
    Professional Mode themselves.
    """
    if "mode" not in st.session_state:
        st.session_state["mode"] = MODE_SIMPLE
    with st.sidebar:
        st.radio(
            "Mode",
            options=[MODE_SIMPLE, MODE_PROFESSIONAL],
            key="mode",
            help="Simple Mode explains everything in plain language. Professional Mode shows full technical detail.",
        )
    return st.session_state["mode"]


def render_explanation(explanation: Explanation, mode: str) -> None:
    """Render one metric's explanation as a mood-colored caption, text chosen by mode."""
    text = explanation.simple if mode == MODE_SIMPLE else explanation.professional
    st.caption(f"{MOOD_ICON[explanation.mood]} {text}")


def render_prediction_disclaimer() -> None:
    """Persistent, unmissable disclaimer for any panel showing an AI/ML prediction."""
    st.warning(f"⚠️ {PREDICTION_DISCLAIMER}")


_CONFIDENCE_LEVEL_ICON = {"Very High": "\U0001F7E2", "High": "\U0001F7E2", "Medium": "\U0001F7E1", "Low": "\U0001F534", "Very Low": "\U0001F534"}
_STATUS_ICON = {"active": "\U0001F7E2", "testing": "\U0001F7E1", "archived": "⚪"}


def _render_model_registry_expander(model_name: str, current_version: str) -> None:
    """Explainable-AI platform phase, Phase 6: full registry lineage for the model that
    produced this prediction -- training/registration date, dataset/feature version,
    hyperparameters, eval metrics, and every other version's status (active/testing/
    archived), so a user can see this model's full history, not just its current state.
    Professional mode only, since Simple mode's "Model: X (status)" caption already
    answers "which model" at the level a non-technical user needs.
    """
    entries = list_registry_entries(model_name)
    if not entries:
        return
    current = next((e for e in entries if e["version"] == current_version), entries[0])

    with st.expander(f"\U0001F4CB Model Registry: {current['version']}", expanded=False):
        lineage_cols = st.columns(3)
        lineage_cols[0].metric("Status", f"{_STATUS_ICON.get(current['status'], '')} {current['status'].title()}")
        lineage_cols[1].metric("Dataset Version", current["dataset_version"])
        lineage_cols[2].metric("Feature Version", current["feature_version"])
        st.caption(
            f"Registered: {current['created_at']:%Y-%m-%d %H:%M UTC} · Family: {current['model_family']} · "
            f"Git commit: {current['git_commit_hash'][:8] if current['git_commit_hash'] else 'unknown'}"
        )
        metric_items = ", ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}" for k, v in current["metrics"].items())
        st.caption(f"Eval metrics: {metric_items or 'none recorded'}")
        hyperparam_items = ", ".join(f"{k}={v}" for k, v in current["hyperparameters"].items())
        st.caption(f"Hyperparameters: {hyperparam_items or 'none recorded'}")

        if len(entries) > 1:
            st.caption(f"**Version history** ({len(entries)} total):")
            for entry in entries:
                marker = "→ " if entry["version"] == current_version else "  "
                active_bit = " (serving)" if entry["is_active"] else ""
                st.caption(f"{marker}{_STATUS_ICON.get(entry['status'], '')} {entry['version']} — {entry['status']}{active_bit}")


_FRESHNESS_ICON = {"Fresh": "\U0001F7E2", "Delayed": "\U0001F7E1", "Stale": "\U0001F534", "Unknown": "⚪"}


def _render_dataset_intelligence_expander(result) -> None:
    """Explainable-AI platform phase, Phase 7: dataset lineage -- version, size, source,
    and (when computable) the real train/validation date ranges behind the serving
    model, via `core.ml.dataset_intelligence`. Professional mode only, same reasoning
    as the Model Registry expander (Phase 6): the freshness caption already shown above
    answers "is this current" at the level a non-technical user needs."""
    from core.ml.dataset_intelligence import dataset_version_info, training_validation_periods

    ds_info = dataset_version_info(result.dataset_version)
    periods = training_validation_periods(result.feature_version)
    if ds_info is None and periods is None:
        return

    with st.expander(f"\U0001F5C3️ Dataset: {result.dataset_version or 'unavailable'}", expanded=False):
        if ds_info is not None:
            ds_cols = st.columns(3)
            ds_cols[0].metric("Dataset Rows", f"{ds_info['row_count']:,}")
            ds_cols[1].metric("Symbols", ds_info["symbol_count"])
            ds_cols[2].metric("Dataset Period", f"{ds_info['start_date']:%Y-%m-%d} → {ds_info['end_date']:%Y-%m-%d}")
            st.caption(f"Source: {ds_info['source']} · Registered: {ds_info['created_at']:%Y-%m-%d}")
        else:
            st.caption("No registered dataset-version record found for this prediction's dataset_version.")

        if periods is not None:
            (train_start, train_end), (val_start, val_end) = periods
            period_cols = st.columns(2)
            period_cols[0].metric("Training Period", f"{train_start:%Y-%m-%d} → {train_end:%Y-%m-%d}")
            period_cols[1].metric("Validation Period", f"{val_start:%Y-%m-%d} → {val_end:%Y-%m-%d}")
        else:
            st.caption("Training/validation period could not be computed for this feature version.")


_DRIFT_STATUS_ICON = {"Stable": "\U0001F7E2", "Drifting": "\U0001F7E1", "Significant Drift": "\U0001F534", "Insufficient Data": "⚪"}


def _render_drift_expander(result, price_df, sentiment_by_date) -> None:
    """Explainable-AI platform phase, Phase 8: full drift report -- feature/data
    distribution drift (PSI vs. training), prediction drift, and concept drift.
    Professional mode only, and only when real price data is available to compute the
    (heavier) feature-drift check against -- the cheap drift_status caption above
    already answers "is this model drifting" for Simple mode / when price data isn't
    passed in."""
    from core.ml.drift import assess_drift
    from core.ml.registry import list_registry_entries

    registered_accuracy = None
    match = next((e for e in list_registry_entries(result.model_name) if e["version"] == result.model_version), None)
    if match is not None:
        registered_accuracy = match["metrics"].get("accuracy")

    report = assess_drift(
        result.symbol, result.model_version, result.feature_version, price_df,
        registered_accuracy=registered_accuracy, sentiment_by_date=sentiment_by_date,
    )

    with st.expander(f"{_DRIFT_STATUS_ICON[report.overall_status]} Drift Report: {report.overall_status}", expanded=False):
        drift_cols = st.columns(3)
        drift_cols[0].metric("Feature/Data Drift", report.data_drift_status)
        drift_cols[1].metric("Prediction Drift", report.prediction_drift_status)
        drift_cols[2].metric("Concept Drift", report.concept_drift_status)

        if report.prediction_drift_detail:
            st.caption(f"Prediction drift: {report.prediction_drift_detail}")
        if report.concept_drift_detail:
            st.caption(f"Concept drift: {report.concept_drift_detail}")
        if report.feature_drift:
            st.caption("Most-drifted features (PSI vs. training distribution):")
            for fd in report.feature_drift:
                st.caption(f"{_DRIFT_STATUS_ICON[fd.status]} {fd.feature_name}: PSI={fd.psi:.3f} ({fd.status})")
        if report.recommend_retraining:
            st.warning("Significant drift detected -- consider retraining this model.")
        for warning in report.warnings:
            st.caption(f"⚠️ {warning}")


def render_prediction_result(result, mode: str, price_df=None, sentiment_by_date=None) -> None:
    """Explainable-AI platform phase: the single renderer for a
    `core.ml.prediction_service.PredictionResult`. Answers the Engineering
    Constitution's questions with whatever the result actually has evidence for, and
    surfaces `result.warnings` for anything it doesn't -- never silently omits a gap.
    """
    if not result.has_prediction:
        for warning in result.warnings:
            st.caption(f"⚠️ {warning}")
        return

    confidence = result.confidence
    cols = st.columns([1, 1])
    cols[0].metric(
        "Direction" if mode == MODE_PROFESSIONAL else "Guess",
        f"{'⬆ Up' if confidence.prediction_class == 'UP' else '⬇ Down'}",
    )
    cols[1].metric(
        "Confidence" if mode == MODE_PROFESSIONAL else "How sure?",
        f"{_CONFIDENCE_LEVEL_ICON[confidence.confidence_level]} {confidence.confidence_level}",
        f"{confidence.confidence_score:.0f}/100" if mode == MODE_PROFESSIONAL else None,
    )

    if result.data_freshness is not None:
        freshness_icon = _FRESHNESS_ICON[result.data_freshness]
        as_of = f" (as of {result.latest_market_timestamp:%d %b %Y})" if result.latest_market_timestamp is not None else ""
        st.caption(f"{freshness_icon} Data: {result.data_freshness}{as_of}" if mode == MODE_SIMPLE else f"{freshness_icon} Market data freshness: {result.data_freshness}{as_of}")

    if result.drift_status is not None and result.drift_status != "Insufficient Data":
        drift_icon = _DRIFT_STATUS_ICON[result.drift_status]
        st.caption(f"{drift_icon} Model drift: {result.drift_status}" if mode == MODE_SIMPLE else f"{drift_icon} Live drift status: {result.drift_status}")

    if mode == MODE_PROFESSIONAL:
        model_bit = (
            f"Model: **{result.model_version}** ({result.model_status})" if result.model_source == "registry" else "Model: **in-app fallback** (no registry version)"
        )
        calib_bit = "calibrated probability" if confidence.was_calibrated else "raw, uncalibrated probability"
        st.caption(f"{model_bit} · {calib_bit}")

        if result.model_source == "registry":
            _render_model_registry_expander(result.model_name, result.model_version)
            _render_dataset_intelligence_expander(result)
            if price_df is not None:
                _render_drift_expander(result, price_df, sentiment_by_date)

    if result.risk is not None:
        risk = result.risk
        risk_icon = {"Low": "\U0001F7E2", "Medium": "\U0001F7E1", "High": "\U0001F7E0", "Very High": "\U0001F534"}[risk.risk_level]
        with st.expander(f"{risk_icon} Risk: {risk.risk_level}" if mode == MODE_SIMPLE else f"{risk_icon} Risk Assessment: {risk.risk_level} ({risk.risk_score:.0f}/100)", expanded=False):
            if mode == MODE_SIMPLE:
                st.write(
                    f"This stock's price has been {'very bumpy' if risk.risk_level in ('High', 'Very High') else 'fairly steady'} "
                    f"lately ({risk.volatility_annualized:.0%} a year). In the last couple of months it swung as much as "
                    f"{abs(risk.expected_drawdown):.0%} down and {risk.expected_upside:.0%} up from local highs/lows."
                )
            else:
                risk_cols = st.columns(4)
                risk_cols[0].metric("Volatility (annualized)", f"{risk.volatility_annualized:.1%}")
                risk_cols[1].metric("Market Regime", risk.market_regime)
                risk_cols[2].metric("Prediction Stability", f"{risk.prediction_stability:.0f}/100")
                risk_cols[3].metric("Confidence Penalty", f"-{risk.confidence_penalty:.0f} pts")
                dd_cols = st.columns(2)
                dd_cols[0].metric("Expected Drawdown (60d)", f"{risk.expected_drawdown:.1%}")
                dd_cols[1].metric("Expected Upside (60d)", f"{risk.expected_upside:.1%}")
                st.caption(risk.method_notes)

    if result.explanation is not None:
        with st.expander("Why?" if mode == MODE_SIMPLE else "Explanation (SHAP)", expanded=False):
            st.write(result.explanation.natural_language_explanation)
            if mode == MODE_PROFESSIONAL:
                exp_cols = st.columns(2)
                with exp_cols[0]:
                    st.caption("Top factors pushing **UP**")
                    for name, value in result.explanation.top_positive_features:
                        st.caption(f"🟢 {name}: +{value:.4f}")
                with exp_cols[1]:
                    st.caption("Top factors pushing **DOWN**")
                    for name, value in result.explanation.top_negative_features:
                        st.caption(f"🔴 {name}: {value:.4f}")
                st.caption(f"Method: {result.explanation.method} · base value {result.explanation.base_value:.3f}")

    if result.recommendation is not None:
        rec = result.recommendation
        stance_icon = "⬆️" if rec.stance == "Leans Up" else "⬇️"
        label = f"{stance_icon} {rec.stance} ({rec.stance_strength})" if mode == MODE_SIMPLE else f"{stance_icon} Recommendation Summary: {rec.stance} ({rec.stance_strength} confidence)"
        with st.expander(label, expanded=False):
            st.write(rec.rationale)
            st.caption(rec.horizon)
            if mode == MODE_SIMPLE:
                st.write(rec.reference_stop_note)
                for r in rec.key_risks:
                    st.caption(f"⚠️ {r}")
            else:
                st.caption(f"Reference level: {rec.reference_stop_level:+.1%}" if rec.reference_stop_level is not None else "Reference level: unavailable")
                st.caption(rec.reference_stop_note)
                st.caption("**Key risks:**")
                for r in rec.key_risks:
                    st.caption(f"• {r}")
            for c in rec.caveats:
                st.caption(f"ℹ️ {c}")

    for warning in result.warnings:
        st.caption(f"⚠️ {warning}")


def render_page_header(title: str, subtitle: str | None = None, icon: str | None = None) -> None:
    """The shared page-title pattern: one `st.title` + optional muted subtitle
    caption, used identically at the top of every page instead of each page
    hand-rolling its own title/caption pair."""
    st.title(f"{icon} {title}" if icon else title)
    if subtitle:
        st.caption(subtitle)


def render_empty_state(title: str, body: str, icon: str = "\U0001F4ED") -> None:
    """A consistent placeholder for "nothing here yet" states (empty watchlist,
    empty portfolio, no search results) instead of each page writing its own
    st.info one-liner with different wording/tone.

    Every current call site passes a hardcoded string literal, so there's no live
    XSS vector today -- but `title`/`body` are still HTML-escaped defensively
    before interpolation, so a future call site can never introduce one by
    accidentally passing user-controlled text (e.g. "No results for '{query}'")
    through unescaped.
    """
    safe_icon, safe_title, safe_body = html.escape(icon), html.escape(title), html.escape(body)
    st.markdown(
        f"""
        <div style="
            text-align: center;
            padding: 2.5rem 1.5rem;
            border: 1px dashed var(--fs-border, #262b38);
            border-radius: 10px;
            color: var(--fs-text-muted, #7d8290);
        ">
            <div style="font-size: 2rem; margin-bottom: 0.5rem;">{safe_icon}</div>
            <div style="font-size: 1.0625rem; font-weight: 600; color: var(--fs-text, #e8e9ed); margin-bottom: 0.25rem;">{safe_title}</div>
            <div style="font-size: 0.9375rem;">{safe_body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_ai_panel(context_label: str, data_key: tuple, fallback_text: str, mode: str) -> tuple[str, bool]:
    return generate_ai_panel(context_label, dict(data_key), fallback_text, mode)


def render_ai_panel(context_label: str, data: dict, fallback_text: str, mode: str) -> None:
    """The shared "AI Analysis" panel for every analytical page: a Gemini-narrated
    synthesis of that page's own already-computed numbers (never invented), with a
    rule-based fallback if Gemini is unavailable or fails.

    Cached per (context_label, data, mode) so switching tabs, toggling a checkbox, or
    any other same-page rerun doesn't re-call Gemini for numbers it already explained.
    """
    st.subheader("What the AI Thinks" if mode == MODE_SIMPLE else "AI Analysis")
    data_key = tuple(sorted(data.items()))
    text, used_gemini = _cached_ai_panel(context_label, data_key, fallback_text, mode)
    st.info(text)
    if not used_gemini and mode == MODE_PROFESSIONAL:
        st.caption("Rule-based summary -- Gemini unavailable or not configured.")


def render_live_market_data_panel(symbols: list[str]) -> None:
    """Opt-in live intraday quotes panel. Additive to, never a replacement for, the
    historical yfinance-based data shown elsewhere on every page -- this only adds a
    separate, clearly-labeled live-quote layer. Never auto-starts: most FinSight users
    won't have live-broker credentials configured, so this stays fully inert (no
    background thread, no network call) until the user explicitly opts in, and even
    then only if credentials are present.

    Routes through `core.broker_adapter.get_active_broker_adapter()` -- whichever
    broker `USE_UPSTOX_PRIMARY` selects backs this panel, with zero rendering-code
    changes either way (the broker-adapter migration's own "zero UI changes" design
    goal)."""
    from core.broker_adapter import get_active_broker_adapter

    adapter = get_active_broker_adapter()
    with st.expander(f"\U0001F534 Live Market Data ({adapter.broker_name})", expanded=False):
        if not adapter.credentials_configured():
            st.caption(
                f"Live intraday quotes require {adapter.broker_name} API credentials in `.env`. "
                "Not configured yet -- this panel stays off until they are."
            )
            return

        enabled = st.checkbox("Enable live data for this session", key="live_market_data_enabled")
        if not enabled:
            st.caption("Live data is off. Turn it on to authenticate and subscribe to your watchlist's live ticks.")
            return

        adapter.ensure_started()
        status = adapter.status()

        status_cols = st.columns(3)
        status_cols[0].metric("Connection", status["status"].replace("_", " ").title())
        status_cols[1].metric("Subscriptions", len(status["subscriptions"]))
        status_cols[2].metric("Reconnect attempts", status["reconnect_attempt"])
        if status["last_error"]:
            st.warning(f"{adapter.broker_name}: {status['last_error']}")

        if status["authenticated"] and symbols:
            to_subscribe = [s for s in symbols if s not in status["subscriptions"]]
            if to_subscribe:
                try:
                    adapter.subscribe_multiple(to_subscribe)
                except Exception as exc:
                    st.error(f"Couldn't subscribe to live data for one or more symbols: {exc}")

        _render_live_ticks_fragment(adapter, tuple(symbols))


@st.fragment(run_every=2)
def _render_live_ticks_fragment(adapter, symbols: tuple[str, ...]) -> None:
    """Re-renders just this small table every 2s by reading the adapter's
    already-updated in-memory tick cache -- no network call happens here, the
    real WebSocket I/O runs on the adapter's own background thread; this fragment
    only polls the local cache so the page doesn't need a full rerun to show
    fresh ticks."""
    ticks = adapter.all_ticks()
    rows = []
    for symbol in symbols:
        tick = ticks.get(symbol)
        if tick is None or tick.ltp is None:
            continue
        rows.append(
            {
                "Symbol": display_symbol(symbol),
                "LTP": format_inr(tick.ltp),
                "Open": format_inr(tick.open) if tick.open is not None else "—",
                "High": format_inr(tick.high) if tick.high is not None else "—",
                "Low": format_inr(tick.low) if tick.low is not None else "—",
                "Volume": f"{tick.volume:,}" if tick.volume is not None else "—",
                "Updated": tick.ingest_ts.strftime("%H:%M:%S") if tick.ingest_ts is not None else "—",
            }
        )
    if not rows:
        st.caption("Waiting for the first live tick...")
    else:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_live_index_cards(
    index_specs: list[tuple[str, str]],
    last_close_by_symbol: dict[str, float],
    pct_by_symbol: dict[str, float | None],
) -> None:
    """The Home dashboard's 3 index metric cards (Nifty 50 / Sensex / Bank Nifty),
    live-updating from the active broker's WebSocket feed (routed via
    `get_active_broker_adapter()`, so `USE_UPSTOX_PRIMARY` controls this card block
    exactly like `render_live_market_data_panel`) when credentials are configured,
    falling back to the caller's already-computed historical (yfinance) value when no
    live tick is available yet -- never the reverse.

    `index_specs`: the caller's own `[(label, yf_symbol), ...]` list, unchanged.
    `last_close_by_symbol`/`pct_by_symbol`: the caller's own already-computed
    historical values, keyed by the same yf_symbol -- this function only
    changes *how* the 3 cards are rendered, not how the historical fallback
    values are computed (that logic, and everything downstream of it in the
    caller such as the AI market summary, is untouched).

    Only this card block reruns on its own 1s timer (`st.fragment`) -- the rest
    of the page does not rerun because of this.
    """
    from core.broker_adapter import BrokerError, get_active_broker_adapter

    adapter = get_active_broker_adapter()
    if adapter.credentials_configured():
        adapter.ensure_started()
        status = adapter.status()
        # KOTAK_INDEX_MAP's values are canonical index display names ("NIFTY 50" etc.)
        # that both KotakAdapter and UpstoxAdapter accept identically for
        # subscribe/get_tick -- despite the name, this map is broker-agnostic (see
        # core.upstox_market_data.INDEX_SYMBOLS, which deliberately uses the same
        # canonical names).
        live_index_names = [KOTAK_INDEX_MAP[sym] for _, sym in index_specs if sym in KOTAK_INDEX_MAP]
        missing = [name for name in live_index_names if name not in status["subscriptions"]]
        if status["authenticated"] and missing:
            try:
                adapter.subscribe_multiple(missing)
            except BrokerError as exc:
                # Fail loudly (logged inside subscribe_multiple already at ERROR),
                # but never crash the whole Home page over a live-data problem --
                # the fragment below falls back to the historical value for any
                # index whose live subscription didn't succeed.
                logger.error("live_index_subscribe_failed broker=%s error=%s", adapter.broker_name, exc)
            except Exception as exc:
                logger.warning("live_index_subscribe_error broker=%s error=%s", adapter.broker_name, exc)

    _render_index_cards_fragment(tuple(index_specs), last_close_by_symbol, pct_by_symbol)


@st.fragment(run_every=1)
def _render_index_cards_fragment(
    index_specs: tuple[tuple[str, str], ...],
    last_close_by_symbol: dict[str, float],
    pct_by_symbol: dict[str, float | None],
) -> None:
    from core.broker_adapter import get_active_broker_adapter

    adapter = get_active_broker_adapter()
    cols = st.columns(3)
    for col, (label, yf_symbol) in zip(cols, index_specs):
        live_index_name = KOTAK_INDEX_MAP.get(yf_symbol)
        tick = adapter.get_tick(live_index_name) if live_index_name else None

        if tick is not None and tick.ltp:
            # Percent change is computed against the historical pipeline's own
            # reference close (not the live feed's own "close" field, whose
            # semantics outside market hours were confirmed ambiguous during
            # live testing -- e.g. an off-hours snapshot arrived with
            # open=high=low=close all equal and ltp=0). Using one single,
            # already-correct reference (yfinance's prior close) keeps the
            # percentage meaningful regardless of what the live feed's close
            # field currently contains.
            reference_close = last_close_by_symbol.get(yf_symbol)
            pct = (tick.ltp / reference_close - 1) if reference_close else None
            col.metric(label, f"{tick.ltp:,.2f}", f"{pct:+.2%}" if pct is not None else None)

            # Layer 10: tick -> UI latency, logged for every fragment render that
            # shows a live value (not just once) -- NormalizedTick.ingest_ts is set
            # at cache-write time by whichever adapter is active, so this measures
            # the full "tick arrived on the WS thread" -> "this Streamlit fragment
            # rendered it" pipeline, not just the parse step.
            if tick.ingest_ts is not None:
                latency_ms = (datetime.now(timezone.utc) - tick.ingest_ts).total_seconds() * 1000
                logger.info(
                    "live_tick_to_ui_latency broker=%s symbol=%s label=%s latency_ms=%.1f",
                    adapter.broker_name, live_index_name, label, latency_ms,
                )
        else:
            value = last_close_by_symbol.get(yf_symbol)
            pct = pct_by_symbol.get(yf_symbol)
            if value is None:
                col.metric(label, "—")
            else:
                col.metric(label, f"{value:,.2f}", f"{pct:+.2%}" if pct is not None else None)
