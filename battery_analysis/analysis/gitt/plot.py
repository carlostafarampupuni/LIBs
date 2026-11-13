"""
GITT figures: D vs state of charge (classic GITT and sqrt(t), every sample) and
the relaxed OCV vs state. Classic-GITT points outside its validity
(tau*D/L^2 >= max_validity, default 0.1) are drawn hollow.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

import numpy as np
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import apply_style, fig_sizes, legend_style, batch_filename, SAVE_DPI

PALETTE = ["#156082", "#E97132", "#55A868", "#A02B93", "#8C8C8C", "#C44E52"]


def _size(fs, default="medium"):
    return tuple(fs) if isinstance(fs, (list, tuple)) else fig_sizes.get(fs or default, fig_sizes[default])


def _save(fig, path, formats):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for fmt in formats:
        p = path.with_suffix(f".{fmt}")
        fig.savefig(p, dpi=SAVE_DPI, transparent=True, bbox_inches="tight", pad_inches=0.02)
        out.append(p)
    return out


def plot_D(samples: Sequence, figs: dict):
    apply_style()
    fig, ax = plt.subplots(figsize=_size(figs.get("size"), "medium"))
    lim = float(figs.get("max_validity", 0.1))
    for k, s in enumerate(samples):
        c = PALETTE[k % len(PALETTE)]
        t = s.table
        x = np.asarray(t["state"], float)
        if "D_gitt_cm2_s" in t:
            ok = t["gitt_validity_tauD_L2"] < lim
            ax.plot(x, t["D_gitt_cm2_s"], color=c, lw=0.8, zorder=2)
            ax.scatter(x[ok], t["D_gitt_cm2_s"][ok], s=20, color=c, zorder=3, label=f"{s.sample}: GITT")
            ax.scatter(x[~ok], t["D_gitt_cm2_s"][~ok], s=20, facecolors="none", edgecolors=c, zorder=3)
        if "D_sqrt_t_cm2_s" in t:
            ax.plot(x, t["D_sqrt_t_cm2_s"], color=c, lw=0.8, ls="--", marker="^", ms=3.5, zorder=2,
                    label=f"{s.sample}: √t pulse")
    values = [v for s in samples for c in ("D_gitt_cm2_s", "D_sqrt_t_cm2_s") if c in s.table
              for v in s.table[c] if np.isfinite(v) and v > 0]
    if values:
        ax.set_yscale("log")
        ax.legend(loc="best", **{**legend_style, "fontsize": 6.5})
    else:
        ax.text(0.5, 0.5, "No diffusion coefficients:\nset thickness_um (GITT) and/or\ncomposition (√t) in gitt.yaml",
                transform=ax.transAxes, ha="center", va="center", fontsize=legend_style["fontsize"], color="0.4")
    ax.set_xlabel(samples[0].settings.get("state_name", "DOD") + " (%)" if samples else "")
    ax.set_ylabel("D (cm$^{2}$ s$^{-1}$)")
    return fig


def plot_ocv(samples: Sequence, figs: dict):
    apply_style()
    fig, ax = plt.subplots(figsize=_size(figs.get("ocv_size"), "small"))
    for k, s in enumerate(samples):
        t = s.table
        ax.plot(np.asarray(t["state"], float), t["OCV_V"], marker="o", ms=3.5, lw=0.8,
                color=PALETTE[k % len(PALETTE)], label=s.sample)
    ax.set_xlabel(samples[0].settings.get("state_name", "DOD") + " (%)" if samples else "")
    ax.set_ylabel("OCV (V)")
    ax.legend(loc="best", **legend_style)
    return fig


def export_figures(samples: Sequence, figs: dict, out_dir: Path, batch: str, show: bool = False) -> List[Path]:
    formats = figs.get("formats", ["svg"])
    root = Path(out_dir) / batch
    written = []
    for name, fn in (("GITT_D", plot_D), ("GITT_OCV", plot_ocv)):
        fig = fn(samples, figs)
        written += _save(fig, root / batch_filename(name, batch), formats)
        plt.show() if show else plt.close(fig)
    return written
