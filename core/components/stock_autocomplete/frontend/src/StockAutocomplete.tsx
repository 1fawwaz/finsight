import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Streamlit, ComponentProps } from "streamlit-component-lib";
import { AutocompleteArgs, ComponentEvent, SearchResultDTO } from "./types";
import { highlight } from "./highlight";

/** Real, only-client-side UI state: open/closed, which row is highlighted, and
 * whether a debounced query is currently in flight. Arrow keys, hover, Escape, and
 * Tab-preview never cross the Python boundary -- only a debounced query string (as
 * the user types) and the final selection do, since only those two actually require
 * `search_stocks()` or app-state changes on the Python side. This is what makes
 * keyboard nav instant instead of waiting on a rerun. */
export default function StockAutocomplete({ args, disabled }: ComponentProps) {
  const {
    label,
    placeholder,
    query: initialQuery,
    results,
    status,
    error_message: errorMessage,
    debounce_ms: debounceMs,
  } = args as AutocompleteArgs;

  const [text, setText] = useState(initialQuery ?? "");
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const [pending, setPending] = useState(false);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputId = "stock-autocomplete-input";
  const listboxId = "stock-autocomplete-listbox";

  const sendEvent = useCallback((event: ComponentEvent) => {
    Streamlit.setComponentValue(event);
  }, []);

  const reportHeight = useCallback(() => {
    // Components render in a same-origin iframe with no intrinsic height -- Streamlit
    // clips to whatever height was last reported, so every visible-content change
    // (dropdown open/close, result count, error text) must re-report it.
    window.requestAnimationFrame(() => {
      if (containerRef.current) {
        Streamlit.setFrameHeight(containerRef.current.scrollHeight + 4);
      }
    });
  }, []);

  useEffect(() => {
    Streamlit.setFrameHeight();
    reportHeight();
  }, [reportHeight]);

  useEffect(() => {
    reportHeight();
  }, [open, results, status, errorMessage, pending, reportHeight]);

  // `results`/`status` only change once Python has finished a search and re-rendered
  // the component with fresh props -- that's the real, observable end of the
  // in-flight request, not a fixed/guessed timeout.
  useEffect(() => {
    setPending(false);
  }, [results, status]);

  useEffect(() => {
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setText(value);
    setOpen(true);
    setHighlightedIndex(-1);
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    if (!value.trim()) {
      setPending(false);
      sendEvent({ type: "clear" });
      return;
    }
    setPending(true);
    debounceTimer.current = setTimeout(() => {
      sendEvent({ type: "query", text: value });
    }, debounceMs || 175);
  };

  const handleSelect = useCallback(
    (result: SearchResultDTO) => {
      setText(`${result.display_symbol} — ${result.name}`);
      setOpen(false);
      setHighlightedIndex(-1);
      sendEvent({ type: "select", symbol: result.symbol });
    },
    [sendEvent]
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) {
      if (e.key === "ArrowDown" && text.trim()) setOpen(true);
      return;
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setHighlightedIndex((i) => (i + 1) % results.length);
        break;
      case "ArrowUp":
        e.preventDefault();
        setHighlightedIndex((i) => (i <= 0 ? results.length - 1 : i - 1));
        break;
      case "Enter":
        e.preventDefault();
        if (highlightedIndex >= 0 && highlightedIndex < results.length) {
          handleSelect(results[highlightedIndex]);
        }
        break;
      case "Tab":
        // Autocomplete the text field to the highlighted result without selecting it
        // (selection is Enter/click only) and without stealing focus -- if nothing is
        // highlighted, Tab behaves normally (moves focus away), so keyboard users are
        // never trapped when there's no useful completion to offer.
        if (highlightedIndex >= 0 && highlightedIndex < results.length) {
          e.preventDefault();
          setText(results[highlightedIndex].display_symbol);
        }
        break;
      case "Escape":
        setOpen(false);
        setHighlightedIndex(-1);
        break;
      default:
        break;
    }
  };

  const showDropdown = open && text.trim().length > 0;

  const statusMessage = useMemo(() => {
    if (pending) return "Searching…";
    if (status === "index_unavailable") return "Search is temporarily rebuilding — please try again in a moment.";
    if (status === "error") return errorMessage || "Search failed. Please try again.";
    if (status === "empty") return `No NSE-listed company found matching "${text}".`;
    return null;
  }, [pending, status, errorMessage, text]);

  return (
    <div ref={containerRef} className="sa-root">
      <label className="sa-label" htmlFor={inputId}>
        {label}
      </label>
      <div className="sa-combobox">
        <input
          id={inputId}
          className="sa-input"
          type="text"
          role="combobox"
          aria-expanded={showDropdown}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-activedescendant={
            showDropdown && highlightedIndex >= 0 ? `sa-option-${highlightedIndex}` : undefined
          }
          placeholder={placeholder}
          value={text}
          disabled={disabled}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onFocus={() => text.trim() && setOpen(true)}
          autoComplete="off"
          spellCheck={false}
        />
        {showDropdown && (
          <ul className="sa-listbox" role="listbox" id={listboxId}>
            {statusMessage && (
              <li className="sa-status" role="status" aria-live="polite">
                {statusMessage}
              </li>
            )}
            {!pending &&
              results.map((r, i) => (
                <li
                  key={r.symbol}
                  id={`sa-option-${i}`}
                  role="option"
                  aria-selected={i === highlightedIndex}
                  className={`sa-option${i === highlightedIndex ? " sa-option-active" : ""}`}
                  onMouseEnter={() => setHighlightedIndex(i)}
                  onMouseDown={(e) => e.preventDefault()} // keep input focus through the click
                  onClick={() => handleSelect(r)}
                >
                  <span className="sa-option-symbol">{highlight(r.display_symbol, r.matched_substring)}</span>
                  <span className="sa-option-name">{highlight(r.name, r.matched_substring)}</span>
                  <span className="sa-option-meta">
                    {r.series}
                    {r.in_watchlist && <span className="sa-badge sa-badge-watchlist">Watchlist</span>}
                    {r.in_portfolio && <span className="sa-badge sa-badge-portfolio">Portfolio</span>}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </div>
    </div>
  );
}
