"""Tests for core.formatting.format_inr (Indian digit grouping) and core.config.is_supported_symbol."""

import pytest

from core.config import is_supported_symbol
from core.formatting import format_inr


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, "₹0.00"),
        (999, "₹999.00"),
        (1000, "₹1,000.00"),
        (100000, "₹1,00,000.00"),
        (1234567.891, "₹12,34,567.89"),
        (-1234567.89, "-₹12,34,567.89"),
    ],
)
def test_format_inr_indian_grouping(value, expected):
    assert format_inr(value) == expected


def test_format_inr_none_is_em_dash():
    assert format_inr(None) == "—"


def test_format_inr_zero_decimals():
    assert format_inr(1234567, decimals=0) == "₹12,34,567"


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("RELIANCE.NS", True),
        ("reliance.ns", True),
        ("TCS.BO", True),
        ("^NSEI", True),
        ("^BSESN", True),
        ("AAPL", False),
        ("SPY", False),
        ("RELIANCE", False),
    ],
)
def test_is_supported_symbol(symbol, expected):
    assert is_supported_symbol(symbol) == expected
