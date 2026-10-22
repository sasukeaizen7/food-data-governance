"""One chart style for all projects.

Light surface, hairline grid, thin marks, text in ink colors (never the series
color), one hue per single-series chart, and a source line under every chart.
Colors are the validated categorical palette; slots are used in fixed order.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from matplotlib.transforms import offset_copy  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
# Categorical slots, fixed order (blue, orange, aqua, yellow, magenta, green, violet, red).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
# Sequential blue ramp (light -> dark) for magnitude, e.g. heatmaps.
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGING = ("#2a78d6", "#f0efec", "#e34948")  # blue <-> gray <-> red


def style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
        "font.size": 10.5,
        "text.color": INK,
        "axes.labelcolor": INK_2,
        "axes.edgecolor": BASELINE,
        "axes.linewidth": 0.8,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 14,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "grid.linestyle": "-",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": INK_2,
        "ytick.labelcolor": INK_2,
        "legend.frameon": False,
        "legend.labelcolor": INK_2,
        "lines.linewidth": 2,
        "axes.prop_cycle": matplotlib.cycler(color=PALETTE),
    })


def thousands(x, _pos=None) -> str:
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= div:
            return f"{x / div:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{x:,.0f}"


THOUSANDS = FuncFormatter(thousands)


def save(fig, path: Path, source: str, subtitle: str | None = None) -> None:
    """Move the first axes' title into a figure header (title + subtitle), add the source line, write a PNG.

    Header and footer sit outside the plot at fixed point offsets, so they never collide with
    the plot whatever the figure size; bbox_inches='tight' grows the canvas to include them.
    """
    ax = fig.axes[0]
    title = ax.get_title(loc="left")
    ax.set_title("", loc="left")
    fig.tight_layout(pad=0.4)  # plot fills the figure; header/footer are added outside it
    top = offset_copy(fig.transFigure, fig=fig, x=6, y=(36 if subtitle else 14), units="points")
    fig.text(0, 1, title, transform=top, fontsize=14, fontweight="bold", color=INK, ha="left", va="bottom")
    if subtitle:
        sub = offset_copy(fig.transFigure, fig=fig, x=6, y=12, units="points")
        fig.text(0, 1, subtitle, transform=sub, fontsize=10.5, color=INK_2, ha="left", va="bottom")
    foot = offset_copy(fig.transFigure, fig=fig, x=6, y=-8, units="points")
    fig.text(0, 0, f"Source: {source}", transform=foot, color=MUTED, fontsize=8.5, ha="left", va="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print(f"  chart -> {path.name}")


def bar_h(ax, labels, values, color=PALETTE[0], fmt=thousands, highlight: int | None = None):
    """Horizontal bars, largest on top, value labels at the bar end (selective by design: one per bar, ranked list)."""
    labels, values = list(labels)[::-1], list(values)[::-1]
    colors = [color] * len(values)
    if highlight is not None:
        colors = [GRID if i != len(values) - 1 - highlight else color for i in range(len(values))]
    bars = ax.barh(labels, values, color=colors, height=0.68)
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, p: fmt(x)))
    vmax = max(values) if values else 0
    for b, v in zip(bars, values):
        ax.text(b.get_width() + vmax * 0.01, b.get_y() + b.get_height() / 2, fmt(v), va="center", fontsize=9, color=INK_2)
    ax.set_xlim(0, vmax * 1.12 if vmax else 1)
    ax.tick_params(axis="y", length=0)
    return bars


def line(ax, x, y, label=None, color=PALETTE[0], end_label: bool = True, fmt=thousands):
    ax.plot(x, y, color=color, label=label, solid_capstyle="round")
    if end_label and len(x):
        ax.annotate(
            (f"{label}: " if label else "") + fmt(list(y)[-1]),
            xy=(list(x)[-1], list(y)[-1]), xytext=(6, 0), textcoords="offset points",
            va="center", fontsize=9, color=INK_2,
        )


