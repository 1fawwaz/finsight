# FinSight UI Audit (Phase 1)

Repository-wide inventory before any design-system implementation. Evidence-based —
every finding below has a file:line reference, not an adjective.

## UI inventory (every page)

| Page | Purpose | Current state |
|---|---|---|
| `app.py` (Home) | Command-center dashboard | 3 index metrics, Portfolio Value card, Watchlist table, AI market summary, Recent Searches list, Quick Search — plain `st.metric`/`st.dataframe`, no custom card styling |
| `pages/1_Market_Overview.py` | Watchlist management | Add/remove stock, sector filter, symbol filter, watchlist table (`st.dataframe`), sector pie, top movers, volume leaders |
| `pages/2_Stock_Analysis.py` | Technical charting | Candlestick + volume + RSI + MACD subplot (Plotly, `theme.py`-colored), overlay checkboxes, 8 `st.metric` stat cards, AI panel |
| `pages/3_Portfolio.py` | Holdings CRUD + analytics | Add/Edit/Delete holdings, allocation pie, sector pie, risk metrics, diversification, risk gauge, cumulative return vs benchmark, correlation heatmap, Monte Carlo — the densest page in the app |
| `pages/4_AI_Sentiment.py` | News sentiment | Sentiment timeline bar chart, per-article cards (`st.container(border=True)` — the only real "card" pattern in the whole app), AI panel |
| `pages/5_ML_Signals.py` | ML direction prediction | Prediction metric + explanation, backtest metrics, confusion matrix heatmap, equity curve |
| `pages/6_About.py` | Static docs | Single `st.markdown` block, architecture table, disclaimer |
| `pages/7_Ask_FinSight_AI.py` | Chat | `st.chat_message`/`st.chat_input`, example-query buttons |

## Component map

| Component type | Where used | Distinct style count |
|---|---|---|
| Buttons | `st.button` everywhere (Add/Delete/Analyze/Run Backtest/New conversation/example queries) | **1** — Streamlit's own default button styling throughout; no custom button variants exist at all |
| Cards | `st.container(border=True)` — **only** in AI Sentiment's article list | **1 real card pattern, used in exactly one place** — every other "card-shaped" UI (KPIs, holdings summary) is actually a bare `st.metric` or `st.columns` group with no visual container |
| Tables | `st.dataframe` (Home watchlist, Market Overview watchlist, Portfolio holdings) | **1** — same Streamlit dataframe widget, but **3 different `NumberColumn` currency formats declared ad hoc per call site** (see Finding 1) |
| Forms | Portfolio's Add/Edit Holding number inputs, Create Portfolio text input, CSV uploader | Native Streamlit widgets only, no shared form-field wrapper |
| Nav | Streamlit's own auto-generated sidebar page list (`pages/N_Name.py` convention) | **1** — no custom/duplicate nav pattern exists; this is a genuine asset, not a gap |
| Charts | Every chart routes through `core/theme.py`'s `apply_dark_layout()` + shared color constants | **1 visual language already** — charts are the most consistent part of the app today |
| Custom autocomplete | `core/components/stock_autocomplete/frontend/` (React/TS, its own CSS file) | **Its own, separate visual language** — hand-styled to approximate the Streamlit theme by eye, not by shared tokens (Finding 3) |

## Findings (with root cause, not adjectives)

### Finding 1 — Three incompatible currency-formatting patterns (Critical for financial UX)

The same kind of number (a stock price, a portfolio value) is formatted three
different ways depending on which widget happens to render it:

1. `core.formatting.format_inr()` — **Indian digit grouping** (₹12,34,567.89) — used
   in `st.metric()` calls: `app.py:93`, `pages/2_Stock_Analysis.py:217-224`,
   `pages/3_Portfolio.py:469-471`.
2. `st.column_config.NumberColumn(format="₹%.2f")` — **Western digit grouping**
   (₹1,234,567.89, Streamlit's own printf-style formatter, no Indian grouping
   support) — used in every `st.dataframe` table: `app.py:123`,
   `pages/1_Market_Overview.py:160/165/166`, `pages/3_Portfolio.py:223-225`.
3. Raw f-string `f"₹{value:,.2f}"` — **also Western digit grouping** — used in
   `core/chat.py:528,532,654` (AI chat responses) and `core/explain.py:209`
   (explanation captions).

**Root cause:** `format_inr()` was built for `st.metric()` call sites only;
`st.column_config.NumberColumn` doesn't support a custom formatter function (only a
printf-style string), so table columns were given a plain `₹%.2f` instead of being
routed through `format_inr`; the chat/explain modules were written independently
and never wired to the shared formatter either. No single call site is "wrong" in
isolation — the inconsistency is architectural: three code paths solve "show a
rupee amount" without a shared contract.

### Finding 2 — Dead/unused design tokens in `core/theme.py`

`INK_PRIMARY` (#0b0b0b), `INK_SECONDARY` (#52514e), `INK_MUTED` (#898781),
`GRIDLINE` (#e1e0d9) — all light-mode-oriented values — have **zero references**
anywhere in the codebase (`grep -rn "theme\.INK_PRIMARY\|theme\.INK_SECONDARY\|theme\.INK_MUTED\|theme\.GRIDLINE\b"` → 0 matches). `STATUS_SERIOUS` (#ec835a) is
also unreferenced. **Root cause:** leftover from an earlier design pass, never
removed when the app committed fully to dark mode.

### Finding 3 — The autocomplete component has its own, separate visual language

`core/components/stock_autocomplete/frontend/src/styles.css` hand-codes its own
color values (`--sa-bg: #0e1117`, border `rgba(128,128,128,0.4)`, focus outline
`#ff4b4b`) that approximate but do not reference `core/theme.py`'s tokens or any
shared source of truth — a second, independent styling system for one widget.
**Root cause:** the component was built as a self-contained React app (necessarily
so, to get real live-search behavior Streamlit can't provide — see
`docs/SEARCH_ENGINE.md` §9) and its CSS was hand-tuned to *look* consistent by eye,
not wired to shared tokens, since no shared token file existed yet at that time.

### Finding 4 — Exactly one real "card" component, used in exactly one place

`st.container(border=True)` (a bordered card) appears **only** in
`pages/4_AI_Sentiment.py:97` (the per-article list). Every other place a "card"
would make sense — Home's Portfolio Value, the KPI index metrics, Stock Analysis's
stat rows, Portfolio's risk-metric pairs — uses a bare `st.metric()` with no visual
container at all. This isn't inconsistent styling so much as an **absent**
component: there is no reusable "card" to be inconsistent, since it was only ever
built once, ad hoc.

### Finding 5 — No custom CSS/spacing scale exists (a smaller gap than typical)

Zero `unsafe_allow_html`/inline `<style>` usage anywhere in `pages/` or `core/`
(`grep` confirmed). This means: **no orphaned per-page CSS exists to reconcile** —
the entire app's visual baseline comes from exactly one source,
`.streamlit/config.toml`'s theme block (`primaryColor #2a78d6`, `backgroundColor
#0e1117`, `secondaryBackgroundColor #161a23`, `textColor #e8e9ed`, sans-serif font),
applied automatically to every native widget on every page. This is a genuine
asset: FinSight does not have the "five different button styles fighting each
other" problem this directive is calibrated against. What it lacks is an
*intentional, extended* system on top of that baseline (typography scale, 8pt
spacing rhythm, card/table/form primitives, WCAG-verified color pairs) — a
green-field addition, not a many-style cleanup.

### Finding 6 — One isolated spacing hack

`pages/4_AI_Sentiment.py:48-49` uses `st.write("")` twice to vertically align a
button next to a search box — the only instance of this pattern in the codebase
(`grep -rn 'st\.write("")' pages/ app.py` → exactly these 2 lines). Low-impact,
but a real ad hoc spacing workaround that a shared layout primitive would remove.

## Exact values currently in use

**Colors** (all from `core/theme.py` + `.streamlit/config.toml` — confirmed zero
inline hex values exist anywhere else in `pages/`/`core/`):
`#0e1117` (bg), `#161a23` (card bg / secondary bg), `#262b38` (gridline),
`#e8e9ed` (ink primary / text), `#7d8290` (ink muted), `#2a78d6` (primary blue),
`#1baf7a`, `#eda100`, `#008300`, `#4a3aa7`, `#e34948`, `#e87ba4`, `#eb6834`
(categorical chart palette), `#0ca30c` (status good), `#fab219` (status warning),
`#ec835a` (status serious — unused, Finding 2), `#d03b3b` (status critical).

**Fonts:** exactly one family in use — `.streamlit/config.toml`'s `font = "sans
serif"` (Streamlit's default sans-serif stack) — applied uniformly by Streamlit
itself; no page overrides it. No explicit type scale (H1/H2/Body/Caption sizes) is
declared anywhere — sizes come entirely from Streamlit's own built-in element
styles (`st.title`, `st.subheader`, `st.caption`, etc.), which is consistent by
construction (same widget, same size, everywhere) but not *intentional* (no one
chose "H2 is 24px/600 weight" — Streamlit chose it, and no page deviates, which
is consistent but undocumented).

**Spacing:** no explicit px scale exists; layout is entirely `st.columns([...])`
ratios and `st.divider()` section breaks. Divider counts per page: Home 4,
Market Overview 3, Stock Analysis 2, Portfolio 8, AI Sentiment 3, ML Signals 4,
About 1, Ask FinSight AI 1 — Portfolio is by far the most section-heavy page,
consistent with it being the most feature-dense.

**Button style count:** 1 (Streamlit's default, theme-colored via `primaryColor`).
**Card style count:** 1 real pattern (`st.container(border=True)`), used once.
**Table style count:** 1 widget (`st.dataframe`), 3 divergent currency formats
within it (Finding 1).

## Summary — what this audit changes about scope

This repository has **less accumulated inconsistency debt than a typical target of
this directive** (no custom CSS forks, no duplicate button/nav implementations, one
consistent chart color language already). The real work is: (1) fix the one
concrete, high-impact bug (three-way currency formatting), (2) build the *missing*
pieces (a real card primitive, a typography/spacing scale, WCAG-verified extended
color roles) as a single shared module, (3) apply it everywhere so what's
implicit-and-consistent-by-luck becomes explicit-and-consistent-by-design, (4) align
the one component with its own visual language (the autocomplete frontend) to the
same token source.
