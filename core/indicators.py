"""Technical indicator calculations: SMA, EMA, RSI, MACD, Bollinger Bands, volatility, returns.

All functions take and return pandas Series/DataFrames indexed the same way as the input,
so callers can assign results straight back onto a price DataFrame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, window: int = 20) -> pd.Series:
    """Simple moving average over `window` periods."""
    return close.rolling(window=window, min_periods=window).mean()


def ema(close: pd.Series, span: int = 20) -> pd.Series:
    """Exponential moving average with the given span."""
    return close.ewm(span=span, adjust=False, min_periods=span).mean()


def returns(close: pd.Series) -> pd.Series:
    """Simple period-over-period percentage returns."""
    return close.pct_change()


def log_returns(close: pd.Series) -> pd.Series:
    """Log returns, additive across periods."""
    return np.log(close / close.shift(1))


def volatility(close: pd.Series, window: int = 20, annualize: bool = True) -> pd.Series:
    """Rolling standard deviation of simple returns, optionally annualized (252 trading days)."""
    rolling_std = returns(close).rolling(window=window, min_periods=window).std()
    if annualize:
        rolling_std = rolling_std * (252 ** 0.5)
    return rolling_std


def _wilder_smooth(deltas: pd.Series, window: int) -> pd.Series:
    """Wilder's smoothed average: simple-mean seed over the first `window` deltas,
    then recursive smoothing avg[i] = (avg[i-1] * (window - 1) + x[i]) / window.

    `deltas` is expected to have a leading NaN (e.g. from Series.diff()), so the
    first `window` real observations are deltas.iloc[1:window+1].
    """
    if len(deltas) <= window:
        # Too little history to seed a window-length average (e.g. a newly-listed
        # stock with under `window` days of trading) -- NaN, not a crash. iloc
        # assignment below would otherwise raise IndexError trying to enlarge the
        # series at position `window`, which doesn't exist yet.
        return pd.Series(np.nan, index=deltas.index)
    seed = deltas.iloc[1 : window + 1].mean()
    seeded = deltas.copy()
    seeded.iloc[:window] = np.nan
    seeded.iloc[window] = seed
    # alpha = 1/window reproduces Wilder's recursive formula exactly once seeded.
    return seeded.ewm(alpha=1 / window, adjust=False, min_periods=1).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index over `window` periods (0-100 scale)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = _wilder_smooth(gain, window)
    avg_loss = _wilder_smooth(loss, window)

    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))
    result = result.where(avg_loss != 0, 100.0)
    result = result.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return result


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD line, signal line, and histogram. Returns a DataFrame with columns macd/signal/histogram."""
    ema_fast = ema(close, span=fast)
    ema_slow = ema(close, span=slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: middle (SMA), upper, and lower bands."""
    middle = sma(close, window=window)
    std = close.rolling(window=window, min_periods=window).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return pd.DataFrame({"middle": middle, "upper": upper, "lower": lower})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range: the largest of today's high-low, |high - prev close|, |low - prev close|."""
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range: Wilder-smoothed true range, a measure of how much a stock
    typically moves per day (in price units, not a percentage)."""
    tr = true_range(high, low, close)
    return _atr_smooth(tr, window)


def _atr_smooth(tr: pd.Series, window: int) -> pd.Series:
    if len(tr) < window:
        # Too little history to seed a window-length average -- NaN, not a crash.
        return pd.Series(np.nan, index=tr.index)
    # True range has no leading NaN (unlike diff()-based series), so seed directly
    # from its first `window` values rather than reusing _wilder_smooth's diff-shaped
    # assumption of a leading NaN.
    seed = tr.iloc[:window].mean()
    seeded = tr.copy()
    seeded.iloc[: window - 1] = np.nan
    seeded.iloc[window - 1] = seed
    return seeded.ewm(alpha=1 / window, adjust=False, min_periods=1).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average Directional Index: strength of a trend (any direction) on a 0-100 scale,
    regardless of whether it's trending up or down."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr = true_range(high, low, close)
    smoothed_tr = _atr_smooth(tr, window)
    smoothed_plus_dm = _atr_smooth(plus_dm, window)
    smoothed_minus_dm = _atr_smooth(minus_dm, window)

    plus_di = 100 * (smoothed_plus_dm / smoothed_tr)
    minus_di = 100 * (smoothed_minus_dm / smoothed_tr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return _atr_smooth(dx, window)


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
    """Rolling Volume-Weighted Average Price over `window` days.

    True VWAP is normally computed intraday from tick/minute data reset each session;
    since FinSight only stores daily OHLCV bars, this is a `window`-day rolling
    approximation using the daily typical price ((high+low+close)/3), a common
    adaptation when only daily bars are available.
    """
    typical_price = (high + low + close) / 3
    pv = typical_price * volume
    return pv.rolling(window=window, min_periods=window).sum() / volume.rolling(window=window, min_periods=window).sum()


def support_resistance(high: pd.Series, low: pd.Series, window: int = 20) -> pd.DataFrame:
    """Rolling support/resistance: the lowest low and highest high over the trailing
    `window` days -- simple, widely-used proxies for "price floor" and "price ceiling"
    levels, not a claim of exact future turning points."""
    resistance = high.rolling(window=window, min_periods=window).max()
    support = low.rolling(window=window, min_periods=window).min()
    return pd.DataFrame({"support": support, "resistance": resistance})


# --- Phase 2 Step 5: Volatility Features (extends this module's existing `volatility`/
# `atr`, does not duplicate them) ------------------------------------------------------


def rolling_variance(close: pd.Series, window: int = 20, annualize: bool = True) -> pd.Series:
    """Rolling variance of simple returns -- `volatility()`'s own building block, exposed
    directly (variance, not its square root) since some downstream uses (e.g. combining
    variance estimators, like Yang-Zhang below) need it un-rooted."""
    rolling_var = returns(close).rolling(window=window, min_periods=window).var()
    if annualize:
        rolling_var = rolling_var * 252
    return rolling_var


def parkinson_volatility(high: pd.Series, low: pd.Series, window: int = 20, annualize: bool = True) -> pd.Series:
    """Parkinson (1980) range-based volatility estimator: uses the high-low range each
    day rather than only the close, making it more statistically efficient than
    close-to-close `volatility()` for the same window (at the cost of assuming no
    overnight gaps, which Yang-Zhang below corrects for)."""
    log_hl = np.log(high / low)
    daily_term = log_hl ** 2 / (4 * np.log(2))
    rolling_mean = daily_term.rolling(window=window, min_periods=window).mean()
    vol = np.sqrt(rolling_mean)
    if annualize:
        vol = vol * (252 ** 0.5)
    return vol


def yang_zhang_volatility(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20, annualize: bool = True
) -> pd.Series:
    """Yang & Zhang (2000) volatility estimator: combines overnight (close-to-open),
    open-to-close, and a Rogers-Satchell drift-independent range term into a single
    minimum-variance unbiased estimator -- handles both overnight gaps (which Parkinson
    ignores) and drift (which naive close-to-close volatility doesn't correct for).
    """
    prev_close = close.shift(1)
    overnight_return = np.log(open_ / prev_close)
    open_to_close_return = np.log(close / open_)
    rogers_satchell = np.log(high / close) * np.log(high / open_) + np.log(low / close) * np.log(low / open_)

    overnight_var = overnight_return.rolling(window=window, min_periods=window).var()
    open_close_var = open_to_close_return.rolling(window=window, min_periods=window).var()
    rs_mean = rogers_satchell.rolling(window=window, min_periods=window).mean()

    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    variance = overnight_var + k * open_close_var + (1 - k) * rs_mean
    # The Rogers-Satchell component can be (rarely) slightly negative in a small/noisy
    # window even though it's a true variance in expectation -- clip at 0 before sqrt
    # rather than propagate NaN from an invalid sqrt of a negative number.
    vol = np.sqrt(variance.clip(lower=0))
    if annualize:
        vol = vol * (252 ** 0.5)
    return vol


def volatility_percentile(vol_series: pd.Series, lookback: int = 252) -> pd.Series:
    """Where today's volatility value ranks (0-1) within its own trailing `lookback`-day
    history -- e.g. 0.9 means today's volatility is higher than 90% of the last year's
    values for this same series. Takes an already-computed volatility series (any of the
    estimators above) rather than raw prices, so it composes with whichever estimator a
    caller already chose instead of hardcoding one."""
    # min_periods scales with lookback but must never exceed it -- a hardcoded floor
    # (e.g. always requiring >=20) would raise ValueError for any caller-supplied
    # lookback smaller than that floor instead of degrading gracefully.
    min_periods = min(lookback, max(5, lookback // 5))
    return vol_series.rolling(window=lookback, min_periods=min_periods).apply(
        lambda w: pd.Series(w).rank(pct=True).iloc[-1], raw=False
    )


def volatility_regime(vol_percentile: pd.Series, low_threshold: float = 0.33, high_threshold: float = 0.67) -> pd.Series:
    """Classify a volatility-percentile series into "low"/"medium"/"high" regimes.
    Thresholds are terciles by default (roughly equal-sized buckets over time), not an
    arbitrary fixed volatility level -- consistent with using a percentile (relative to
    the symbol's own history) rather than a hardcoded absolute volatility cutoff that
    would mean different things for a calm vs. a volatile stock."""
    regime = pd.Series(pd.NA, index=vol_percentile.index, dtype="object")
    valid = vol_percentile.notna()
    regime[valid & (vol_percentile <= low_threshold)] = "low"
    regime[valid & (vol_percentile > low_threshold) & (vol_percentile <= high_threshold)] = "medium"
    regime[valid & (vol_percentile > high_threshold)] = "high"
    return regime
