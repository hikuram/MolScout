import os
import textwrap
from typing import Optional

import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from utils import log


# add_underglow, adapted from mplcyberpunk (https://github.com/dhaitz/mplcyberpunk)
def add_underglow(ax: Optional[plt.Axes] = None, alpha_underglow: float = 0.1) -> None:
    """Add an underglow effect below plotted lines."""
    if not ax:
        ax = plt.gca()

    xlims, ylims = ax.get_xlim(), ax.get_ylim()
    lines = ax.get_lines()

    for line in lines:
        x, y = line.get_data(orig=False)
        color = line.get_c()
        transform = line.get_transform()
        try:
            step_type = line.get_drawstyle().split("-")[1]
        except Exception:
            step_type = None

        ax.fill_between(
            x=x,
            y1=y,
            y2=[0] * len(y),
            color=color,
            step=step_type,
            alpha=alpha_underglow,
            transform=transform,
        )

    ax.set(xlim=xlims, ylim=ylims)


def _safe_float_array(series) -> np.ndarray:
    """Convert a pandas Series to float array with NaN for invalid values."""
    return np.asarray(series, dtype=float)


def _format_value_label(value: float) -> str:
    """Format numeric labels consistently."""
    if not np.isfinite(value):
        return "nan"
    return f"{value:.8g}"


def _build_optpoints_xticklabels(previous_images: np.ndarray):
    """Build compact x tick labels for optpoints profile."""
    labels = ["IS", "TS", "FS"]

    if previous_images is None or len(previous_images) < 3:
        return labels

    out = []
    for base, prev in zip(labels, previous_images[:3]):
        if np.isfinite(prev):
            out.append(f"{base}\n(img {int(prev)})")
        else:
            out.append(base)
    return out


def _plot_plateau_profile(
    ax: plt.Axes,
    values: np.ndarray,
    color,
    xticklabels,
    column_index: int = 0,
    ncols: int = 2,
    plateau_half_width: float = 0.30,
    connector_linewidth: float = 1.2,
    plateau_linewidth: float = 2.0,
) -> None:
    """
    Plot a reaction-profile style figure with flat plateaus and connectors.

    Expected use:
    - 3-point optpoints profile
    - values[0]: IS
    - values[1]: TS-like point
    - values[2]: FS
    """
    x_centers = np.array([0.0, 1.0, 2.0], dtype=float)

    finite_vals = values[np.isfinite(values)]
    if finite_vals.size == 0:
        y_min, y_max = -1.0, 1.0
        y_span = 1.0
    else:
        y_min = float(np.min(finite_vals))
        y_max = float(np.max(finite_vals))
        y_span = y_max - y_min
        if y_span < 1e-12:
            y_span = max(abs(y_max), 1.0) * 0.05

    # More headroom above labels
    top_margin = 0.16 * y_span
    bottom_margin = 0.06 * y_span
    ax.set_ylim(y_min - bottom_margin, y_max + top_margin)

    # Draw plateau segments
    for xc, y in zip(x_centers, values):
        if not np.isfinite(y):
            continue
        ax.hlines(
            y,
            xc - plateau_half_width,
            xc + plateau_half_width,
            color=color,
            linewidth=plateau_linewidth,
        )

    # Draw connectors between plateaus
    for i in range(len(values) - 1):
        y0 = values[i]
        y1 = values[i + 1]
        if not (np.isfinite(y0) and np.isfinite(y1)):
            continue

        ax.plot(
            [x_centers[i] + plateau_half_width, x_centers[i + 1] - plateau_half_width],
            [y0, y1],
            color=color,
            linewidth=connector_linewidth,
        )

    # Numeric labels
    text_offset_y = 0.035 * y_span

    # Right column: shift labels a little to the right to reduce clashes
    # column_index is zero-based in the flattened subplot array
    is_right_column = (column_index % ncols) == 1
    text_shift_x = 0.00 if is_right_column else 0.0

    for xc, y in zip(x_centers, values):
        if not np.isfinite(y):
            continue

        label = _format_value_label(y)

        ax.text(
            xc + text_shift_x,
            y + text_offset_y,
            label,
            ha="center",
            va="bottom",
            fontsize=8,
        )

    ax.set_xlim(-0.55, 2.55)
    ax.set_xticks(x_centers)
    ax.set_xticklabels(xticklabels)

    # Tick labels a bit closer to axis, but still readable
    ax.tick_params(axis="x", pad=2)


def instant_plot(dataframe, peak_idx, fig_name):
    # 1. Load style
    style_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "example.mplstyle")
    plt.style.use(style_path)

    # 2. Extract numeric columns
    colnames = dataframe.select_dtypes("number").columns.values.tolist()

    # Exclude x/index helper columns from y-plot candidates
    plot_cols = [c for c in colnames if c not in ["# image", "previous_#image"]]

    if len(plot_cols) == 0:
        log("Warn", "No numeric columns found to plot in the dataframe.")
        return

    # 3. Style and layout
    palette = sns.color_palette("viridis_r", len(plot_cols))
    sns.set_palette(palette)

    ncols = 2
    n_row = (len(plot_cols) + ncols - 1) // ncols
    plt.rcParams["figure.figsize"] = (12, 2.15 * n_row)

    fig, axes = plt.subplots(n_row, ncols, sharex=False)
    axes_flat = np.atleast_1d(axes).flatten()

    # 4. Detect optpoints-style dataframe
    is_optpoints_profile = (
        len(dataframe) == 3 and
        "previous_#image" in dataframe.columns
    )

    if is_optpoints_profile:
        prev_images = _safe_float_array(dataframe["previous_#image"])
        xticklabels = _build_optpoints_xticklabels(prev_images)
    else:
        prev_images = None
        xticklabels = None

    # 5. Main plotting loop
    for n, colname in enumerate(plot_cols):
        ax = axes_flat[n]
        ax.set_ylabel(textwrap.fill(colname, 20))

        if is_optpoints_profile:
            values = _safe_float_array(dataframe[colname])
            _plot_plateau_profile(
                ax=ax,
                values=values,
                color=palette[n],
                xticklabels=xticklabels,
                column_index=n,
                ncols=ncols,
                plateau_half_width=0.30,
            )
        else:
            sns.lineplot(
                data=dataframe,
                x="# image",
                y=colname,
                hue=None,
                legend=None,
                ax=ax,
                color=palette[n],
            )
            add_underglow(ax)

            if peak_idx is not None:
                for x in peak_idx:
                    if x in dataframe.index:
                        y = dataframe.loc[x, colname]
                        if y == y:
                            ax.text(
                                x,
                                y,
                                _format_value_label(float(y)),
                                ha="center",
                                va="bottom",
                                fontsize=8,
                            )

    # 6. Hide unused axes
    for empty_ax in axes_flat[len(plot_cols):]:
        empty_ax.set_visible(False)

    # 7. Extra space for two-line xticklabels
    fig.tight_layout(pad=0.8, h_pad=0.6, w_pad=0.8)

    plt.savefig(fig_name)
    plt.close(fig)
