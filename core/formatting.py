"""Indian Rupee currency formatting shared across all pages."""

from __future__ import annotations


def format_inr(value: float | None, decimals: int = 2) -> str:
    """Format a number using Indian digit grouping with a Rupee sign, e.g. 1234567.89 -> '₹12,34,567.89'."""
    if value is None:
        return "—"

    negative = value < 0
    value = abs(value)
    int_part = int(value)
    int_str = str(int_part)

    if len(int_str) > 3:
        last_three = int_str[-3:]
        remainder = int_str[:-3]
        groups: list[str] = []
        while len(remainder) > 2:
            groups.insert(0, remainder[-2:])
            remainder = remainder[:-2]
        if remainder:
            groups.insert(0, remainder)
        grouped = ",".join(groups) + "," + last_three
    else:
        grouped = int_str

    if decimals > 0:
        frac_str = f"{value:.{decimals}f}".split(".")[1]
        grouped += "." + frac_str

    return ("-" if negative else "") + "₹" + grouped
