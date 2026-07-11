"""Shared color constants and layout helpers for Plotly charts across all FinSight pages.

Colors here are chosen to match the app's dark theme (`.streamlit/config.toml`), so every
chart looks like it belongs to the same product instead of a light-mode plot dropped onto
a dark page.
"""

# Matches .streamlit/config.toml's backgroundColor exactly, so charts blend seamlessly
# into the page instead of showing as a separate white rectangle.
DARK_BG = "#0e1117"
DARK_CARD_BG = "#161a23"
DARK_GRIDLINE = "#262b38"
DARK_INK_PRIMARY = "#e8e9ed"
DARK_INK_MUTED = "#7d8290"

CATEGORICAL = [
    "#2a78d6",  # blue
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
    "#e87ba4",  # magenta
    "#eb6834",  # orange
]

SEQUENTIAL_BLUE = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]

DIVERGING_BLUE_RED = [[0.0, "#1c5cab"], [0.5, "#f0efec"], [1.0, "#d03b3b"]]

STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"


def apply_dark_layout(fig, **extra_layout):
    """Apply the shared dark chart theme (background, gridlines, font color) to a Plotly
    figure in place, then return it for chaining. Works for both simple figures and
    make_subplots figures (update_xaxes/update_yaxes apply to every subplot axis)."""
    fig.update_layout(
        paper_bgcolor=DARK_BG,
        plot_bgcolor=DARK_BG,
        font=dict(color=DARK_INK_PRIMARY),
        **extra_layout,
    )
    fig.update_xaxes(gridcolor=DARK_GRIDLINE, linecolor=DARK_GRIDLINE, zerolinecolor=DARK_GRIDLINE)
    fig.update_yaxes(gridcolor=DARK_GRIDLINE, linecolor=DARK_GRIDLINE, zerolinecolor=DARK_GRIDLINE)
    return fig
