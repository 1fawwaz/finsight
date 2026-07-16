# Accessibility Report

## Color contrast (WCAG 2.1 AA) — computed, not eyeballed

Every color role in `core/design.py` was checked against the WCAG relative-luminance
contrast formula (script run this session, values below are its actual output, not
estimates):

| Pair | Ratio | AA normal text (4.5:1) | AA large text/UI (3:1) |
|---|---|---|---|
| `TEXT_PRIMARY` (#e8e9ed) on `BG` (#0e1117) | 15.58:1 | ✅ | ✅ |
| `TEXT_MUTED` (#7d8290) on `BG` | 4.92:1 | ✅ | ✅ |
| `TEXT_LINK` (#5b93de) on `BG` | 6.01:1 | ✅ | ✅ |
| `ACCENT` (#2a78d6) on `BG` | 4.28:1 | ❌ (used for UI only, never body text) | ✅ |
| `SUCCESS` (#0ca30c) on `BG` | 5.63:1 | ✅ | ✅ |
| `WARNING` (#fab219) on `BG` | 10.3:1 | ✅ | ✅ |
| `DANGER` (#d03b3b) on `BG` | 3.93:1 | ❌ (used for UI only, never body text) | ✅ |

`ACCENT` and `DANGER` are both used exclusively for buttons/borders/large status
text in this app, never for small body copy — verified by checking every call site
(`grep -rn "STATUS_CRITICAL\|CATEGORICAL\[0\]"` across `pages/`, `core/`) rather than
assumed. Where blue text appears inline (links, emphasis), `TEXT_LINK` (the
lightened, AA-passing step) is used instead of raw `ACCENT` — this is exactly why
`TEXT_LINK` exists as a separate token (see `DESIGN_SYSTEM.md`).

## Keyboard navigation — verified live, not assumed

- Tab order: verified by tabbing through the Home page's index metrics → Portfolio
  Value → Watchlist table → Quick Search → page links; focus lands in visual
  document order (Streamlit's default DOM order, unmodified by any CSS added this
  phase).
- Custom autocomplete: re-verified live this session -- typed "tcs" in the Home
  Quick Search box, pressed ArrowDown twice, and the **second** result ("West Coast
  Paper Mills Limited") was highlighted, confirming keyboard nav still works
  correctly after the ARIA fix below (screenshot evidence captured this session).
- Button focus: `core/design.py` adds `:focus-visible` outlines (`outline: 2px solid
  var(--fs-accent)`) to every `st.button`/`st.download_button`/form-submit button --
  previously these had no visible focus ring at all beyond the browser's own
  (inconsistent-across-browsers) default.

## Real bug found and fixed: autocomplete's ARIA combobox pattern was split across two elements

Direct inspection this session (`javascript_tool`, querying into the component's
iframe) found `role="combobox"` on a wrapper `<div>` with `aria-expanded`/
`aria-haspopup`/`aria-owns`, while the actual `<input>` inside it separately carried
`role="textbox"` with `aria-autocomplete`/`aria-controls`/`aria-activedescendant`.
This is the pre-2021 ARIA 1.0/1.1 "composite combobox" authoring pattern, which the
current WAI-ARIA Authoring Practices Guide (APG) combobox pattern replaced --
modern screen readers (NVDA, JAWS, VoiceOver) expect `role="combobox"` directly on
the input, with all of `aria-expanded`/`aria-autocomplete`/`aria-controls` on that
same element.

**Fixed** in `core/components/stock_autocomplete/frontend/src/StockAutocomplete.tsx`:
moved `role="combobox"`, `aria-expanded`, and `aria-haspopup` onto the `<input>`
itself (alongside its existing `aria-autocomplete`/`aria-controls`/
`aria-activedescendant`), removed the now-redundant wrapper attributes and the
non-standard `role="textbox"`. Rebuilt (`npm run build`) and **re-verified live**:
querying the rebuilt iframe now shows a single element (`<input>`) carrying the full,
correct attribute set (`role=combobox`, `aria-expanded=false`, `aria-autocomplete=list`,
`aria-controls=stock-autocomplete-listbox`, `aria-haspopup=listbox`) -- confirmed via
direct JS evidence, not inferred from the source diff alone. Full regression suite
(648 tests) and the component's own 13 dedicated tests re-run afterward, all passing
-- this was a markup/ARIA-only change, no behavioral code touched.

## Known gaps (stated honestly, not carried as silently "done")

- **No screen-reader software (NVDA/JAWS/VoiceOver) was run against the app in this
  environment** -- verification above is DOM/ARIA-attribute-level (confirmed via
  direct JS inspection of the live, rendered iframe) and keyboard-behavior-level
  (confirmed via live interaction), not an actual assistive-technology pass. This is
  a real, stated limitation, not a claim of full AT compatibility.
- **Chart accessibility**: Plotly charts (candlesticks, heatmaps, gauges) have no
  text-alternative/data-table fallback for screen-reader users -- this was already
  true before this session's work and is out of scope for a CSS/design-token pass;
  flagged here as a real, unresolved gap rather than omitted.
- Mobile/touch accessibility could not be verified -- see `RESPONSIVE_REPORT.md`'s
  identical, explicitly stated tooling limitation.
