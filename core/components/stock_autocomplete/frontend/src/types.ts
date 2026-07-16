export interface SearchResultDTO {
  symbol: string;
  display_symbol: string;
  name: string;
  series: string;
  tier_label: string;
  matched_substring: string | null;
  in_watchlist: boolean;
  in_portfolio: boolean;
}

export type Status = "idle" | "success" | "empty" | "error" | "index_unavailable";

export interface AutocompleteArgs {
  label: string;
  placeholder: string;
  query: string;
  results: SearchResultDTO[];
  status: Status;
  error_message: string | null;
  disabled: boolean;
  debounce_ms: number;
}

export type ComponentEvent =
  | { type: "query"; text: string }
  | { type: "select"; symbol: string }
  | { type: "clear" };
