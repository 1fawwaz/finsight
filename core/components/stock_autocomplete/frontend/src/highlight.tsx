import React from "react";

/** Bold the first case-insensitive occurrence of `substring` within `text`, preserving
 * text's original casing -- mirrors core.ui_components._highlight_matched_text's logic,
 * but renders real <mark> markup now that the dropdown itself isn't limited to plain
 * text (unlike the native st.selectbox this component replaces). */
export function highlight(text: string, substring: string | null): React.ReactNode {
  if (!substring) return text;
  const idx = text.toUpperCase().indexOf(substring.toUpperCase());
  if (idx === -1) return text;
  const end = idx + substring.length;
  return (
    <>
      {text.slice(0, idx)}
      <mark>{text.slice(idx, end)}</mark>
      {text.slice(end)}
    </>
  );
}
