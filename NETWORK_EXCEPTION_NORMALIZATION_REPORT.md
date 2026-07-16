# Production Reliability Fix — Network Exception Normalization

## Root Cause

**Symptom**: A genuine network interruption during price-history ingestion (a
dropped connection, a timeout, a DNS failure, an HTTP 429/500 from the
provider) did not produce the app's intended friendly "Couldn't fetch X"
message. Instead it surfaced as an unhandled exception for that page's rerun.

**Root Cause**: `core.data_ingestion.fetch_price_history()` caught every
exception from `yf.Ticker(symbol).history()` only long enough to record
provider health, then re-raised the *original* exception via a bare `raise`
(no normalization). Every application caller (`app.py`, all 5 relevant pages,
`core/historical_backfill.py`, `core/ml/data_layer.py`) only ever catches
`except IngestionError`, per the ingestion layer's assumed contract. A raw
`requests.exceptions.ConnectionError` (or `Timeout`, `HTTPError`, a yfinance
`YFRateLimitError`, etc.) is not an `IngestionError`, so it passed straight
through every caller's error handling.

**Trigger**: Any real network interruption, provider rate-limit (HTTP 429),
provider-side error (HTTP 500), or DNS failure occurring during
`yf.Ticker(...).history()`, for any symbol, on any of the ingestion call paths.

**Files affected**: `core/data_ingestion.py` (the fix), `tests/test_ingestion.py`
(regression tests). No other file required a change — see "Verify Every
Caller" below.

## Fix

**Code modified**: `core/data_ingestion.py`:

1. Added `_EXPECTED_PROVIDER_EXCEPTIONS = (requests.exceptions.RequestException,
   yfinance.exceptions.YFException)` — the two exception family roots that cover
   every *provider/network* failure mode (`RequestException` is the base class
   for `ConnectionError`/`Timeout`/`HTTPError`/`SSLError`/wrapped DNS failures;
   `YFException` is the base class for yfinance's own `YFRateLimitError`,
   `YFTickerMissingError`, etc. — confirmed via direct inspection of
   `yfinance.exceptions`).
2. `fetch_price_history()`'s exception handling is now split into three cases
   instead of one blanket `except Exception`:
   - `except IngestionError` (from `_validate_history`'s empty/malformed-response
     check) — record the failure, re-raise as-is (already the right type).
   - `except _EXPECTED_PROVIDER_EXCEPTIONS as exc` — record the failure, then
     `raise IngestionError(...) from exc` (chains the original exception).
   - **Anything else propagates unmodified** — a real bug in our own code
     (`TypeError`, `AttributeError`, etc.) is not masked as "provider trouble".
3. Added `_record_fetch_failure(symbol, exc, start)`: emits a structured
   `logger.error("Market data ingestion failed", extra={"provider": "yfinance",
   "symbol": symbol, "exception": type(exc).__name__, "error_message": str(exc),
   "execution_time_ms": latency_ms})` for every failure, then performs the
   existing provider-health DB write (unchanged behavior, just extracted into
   the shared helper to avoid duplicating it across the two failure branches).

**Why this restores the contract**: every call site's existing `except
IngestionError` now actually catches every provider/network failure mode, not
just the empty-response case it happened to catch before. The public contract
(`Return pandas.DataFrame` / `Raise IngestionError`) is unchanged — no caller
needed modification.

**Why unrelated behavior is unchanged**: the success path
(`fetch_price_history`'s final `return history`) was not touched. The
provider-health DB-write logic (what gets recorded, its own failure-swallowing
try/except) is byte-identical, just relocated into a shared helper instead of
duplicated. `_classify_failure()` (the DB failure-type classifier) is untouched.

## Verification

### Regression tests

```
$ pytest -q tests/test_ingestion.py
29 passed in 1.01s
```

10 new tests added, covering every requested scenario:

| Scenario | Test |
|---|---|
| ConnectionError | `test_fetch_price_history_normalizes_every_expected_provider_failure[ConnectionError-...]` |
| Timeout | same, `[Timeout-...]` |
| DNS failure | same, `[DNSFailure-...]` (a `ConnectionError` wrapping a DNS-resolution message, matching how `requests` actually surfaces DNS failures) |
| HTTP 500 | same, `[HTTP500-...]` |
| HTTP 429 | same, `[HTTP429_YFRateLimit-...]` (yfinance's own `YFRateLimitError`, confirmed live earlier this session to be yfinance's real mapping for HTTP 429) |
| SSL error | same, `[SSLError-...]` (an explicit extra beyond the minimum list) |
| Provider unavailable | same, `[ProviderUnavailable-...]` (yfinance's `YFTickerMissingError`) |
| Invalid ticker / empty DataFrame | `test_fetch_price_history_empty_dataframe_still_raises_ingestion_error` |
| Successful request | `test_fetch_price_history_success_path_unaffected` |
| Programming bug (not a provider failure) | `test_fetch_price_history_lets_real_programming_bugs_propagate_unmodified` (asserts a `TypeError` is **not** swallowed) |

Every parametrized scenario asserts all four required properties in one test:
raw exception NOT raised, `IngestionError` IS raised, original exception IS
chained (`exc_info.value.__cause__ is original_exc`), and a structured log
entry IS emitted (via `caplog`).

### Stack trace before fix (reconstructed from the exact pre-fix code path)

```
Traceback (most recent call last):
  File "<string>", line 14, in <module>
  File "<string>", line 8, in old_fetch_price_history_simulation
requests.exceptions.ConnectionError: Simulated network interruption: connection refused
```

No `IngestionError` anywhere in the chain — this is exactly what would reach
`except IngestionError:` at any call site and fail to match.

### Stack trace after fix (live run against the actual, current code)

```
2026-07-14 16:06:42,211 [ERROR] core.data_ingestion: Market data ingestion failed
Traceback (most recent call last):
  File ".../core/data_ingestion.py", line 164, in fetch_price_history
    history = yf.Ticker(symbol).history(period=period, auto_adjust=False)
  File "<string>", line 16, in _raise
requests.exceptions.ConnectionError: Simulated network interruption: connection refused

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<string>", line 24, in <module>
  File ".../core/data_ingestion.py", line 175, in fetch_price_history
    raise IngestionError(f"Could not fetch price history for {symbol!r}: {exc}") from exc
core.data_ingestion.IngestionError: Could not fetch price history for 'RELIANCE.NS': Simulated network interruption: connection refused
```

This demonstrates, in one real run: (1) the structured log line fires
(`[ERROR] core.data_ingestion: Market data ingestion failed`), (2) the
original `ConnectionError` is preserved as the exception cause ("The above
exception was the direct cause of the following exception" is Python's own
standard rendering of `raise ... from exc`), (3) the exception the caller
actually receives is `IngestionError`.

### Proof callers still receive `IngestionError`, unmodified

Every existing call site was located and reviewed:

```
app.py:46, pages/1_Market_Overview.py:82, pages/2_Stock_Analysis.py:59,
pages/3_Portfolio.py:78 & 180, pages/4_AI_Sentiment.py:45,
pages/5_ML_Signals.py:81, core/historical_backfill.py:64,
core/ml/data_layer.py:117 & 137
```

Every one of these exclusively catches `except IngestionError` — none depend
on a raw provider exception type, so none required modification (per
Requirement #6: "If any caller depends on raw provider exceptions, document it
before changing anything" — none do). Full regression suite, which exercises
most of these call sites indirectly:

```
$ pytest -q
668 passed, 9 warnings in 69.47s
```

(658 before this fix's tests were added, +10 new = 668, 0 failures.)

## A real bug caught during this fix's own development

The first draft of the structured-logging call used `extra={"message": str(exc),
...}` per the directive's own example. This raises `KeyError: "Attempt to
overwrite 'message' in LogRecord"` at the first `logger.error(...)` call —
`message` is a reserved `LogRecord` attribute name in Python's `logging` module
(set by `Formatter.format()` via `record.getMessage()`), and passing it through
`extra=` is explicitly rejected by `Logger.makeRecord()`. Caught by testing the
exact `extra=` dict in isolation before wiring it into the real function;
renamed to `error_message` in the actual fix. Documented here rather than
silently corrected, since it's a legitimate example of "verify, don't assume"
catching a real defect before it shipped.

## Final Status

**STATUS: SUCCESS**

Every regression test passes (29/29 in the affected file, 668/668 across the
full suite), no raw provider exception can escape the ingestion layer (verified
for 7 distinct exception types plus the empty-response and success paths), and
a genuine programming bug is confirmed to still propagate unmasked. Exception
chaining and structured logging are both verified with live evidence, not
assumed. Every existing caller was inspected and requires no changes.
