"""
DOE figures, in the house style, saved to Figures/DOE/<design_name>/.

    pareto       |t| of every effect, largest first, with the significance line
    main         mean response at each level of each factor (one panel each)
    interaction  response vs one factor, one line per level of another
    residuals    residuals vs fitted + normal probability plot (model check)
"""
from __future__ import annotations

from itertools import combinations
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from battery_analysis.plotting.style import (
    apply_style, fig_sizes, format_axes, ensure_figure_transparency, finish_figure, chem_label,
)

LOW, HIGH = "#6B7280", "#E8746A"          # grey / red, as in config.yaml
BAR_SIG, BAR_NS = "#E8746A", "#C9CDD3"


def _ylabel(result, cfg) -> str:
    r = next((r for r in cfg["responses"] if r["name"] == result.response), {})
    return f"{result.label} ({r['unit']})" if r.get("unit") else result.label


def _save(fig, folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.svg"
    ensure_figure_transparency(fig)
    fig.savefig(path, bbox_inches="tight", transparent=True)
    finish_figure(fig)
    return path


def pareto(result, folder, alpha=0.05):
    from scipy import stats
    e = result.effects.iloc[::-1]
    fig, ax = plt.subplots(figsize=fig_sizes["medium"])
    t = e["t"].abs().values
    ax.barh(range(len(e)), t, color=[BAR_SIG if s else BAR_NS for s in e["significant"]], height=0.65)
    ax.set_yticks(range(len(e)), [chem_label(n) for n in e["name"]])
    if result.method == "OLS":
        line = stats.t.ppf(1 - alpha / 2, result.model.df_resid)
    else:
        line = stats.t.ppf(1 - alpha / 2, len(e) / 3)
    ax.axvline(line, color="black", lw=0.7, ls="--")
    ax.set_xlabel("|standardized effect|")
    format_axes(ax)
    ax.set_title(result.label, fontsize=8)
    return _save(fig, folder, f"pareto_{result.response}")


def main_effects(result, cfg, folder):
    d = result.data
    factors = cfg["factors"]
    fig, axes = plt.subplots(1, len(factors), figsize=(1.6 * len(factors), 2.1), sharey=True)
    for ax, f in zip(np.atleast_1d(axes), factors):
        lv = sorted(d[f["code"]].unique())
        means = [d.loc[d[f["code"]] == v, "y"].mean() for v in lv]
        ax.plot(lv, means, "-o", color="black", ms=4, lw=0.9)
        ax.scatter(d[f["code"]] + np.random.default_rng(0).uniform(-0.06, 0.06, len(d)), d["y"],
                   s=6, color=LOW, alpha=0.6, zorder=0)
        ticks = [-1, 1] if len(lv) == 2 else lv
        labels = [str(f["levels"][0]), str(f["levels"][1])] if len(lv) == 2 else [f"{v:g}" for v in lv]
        ax.set_xticks(ticks, [chem_label(x) for x in labels])
        ax.set_xlim(-1.5, 1.5)
        ax.set_xlabel(chem_label(f["name"]))
        format_axes(ax)
    np.atleast_1d(axes)[0].set_ylabel(_ylabel(result, cfg))
    fig.tight_layout()
    return _save(fig, folder, f"main_{result.response}")


def interactions(result, cfg, folder):
    d = result.data
    factors = cfg["factors"]
    pairs = list(combinations(factors, 2))
    fig, axes = plt.subplots(1, len(pairs), figsize=(1.8 * len(pairs), 2.1), sharey=True)
    for ax, (fa, fb) in zip(np.atleast_1d(axes), pairs):
        for lvl, color, txt in ((-1, LOW, fb["levels"][0]), (1, HIGH, fb["levels"][1])):
            sub = d[d[fb["code"]] == lvl]
            m = sub.groupby(fa["code"])["y"].mean()
            m = m[m.index.isin([-1, 1])]
            ax.plot(m.index, m.values, "-o", color=color, ms=3.5, lw=0.9, label=chem_label(txt))
        ax.set_xticks([-1, 1], [chem_label(str(x)) for x in fa["levels"]])
        ax.set_xlim(-1.5, 1.5)
        ax.set_xlabel(chem_label(fa["name"]))
        ax.legend(title=chem_label(fb["name"]), fontsize=6, title_fontsize=6, frameon=False,
                  handlelength=1.0, loc="best")
        format_axes(ax)
    np.atleast_1d(axes)[0].set_ylabel(_ylabel(result, cfg))
    fig.tight_layout()
    return _save(fig, folder, f"interactions_{result.response}")


def residuals(result, folder):
    from scipy import stats
    if result.method != "OLS":
        return None
    res = result.model
    fig, (a1, a2) = plt.subplots(1, 2, figsize=fig_sizes["large"])
    a1.scatter(res.fittedvalues, res.resid, s=8, color="black")
    a1.axhline(0, color=LOW, lw=0.7, ls="--")
    a1.set_xlabel("fitted")
    a1.set_ylabel("residual")
    (osm, osr), (slope, icpt, _) = stats.probplot(res.resid, dist="norm")
    a2.scatter(osm, osr, s=8, color="black")
    a2.plot(osm, slope * np.asarray(osm) + icpt, color=HIGH, lw=0.8)
    a2.set_xlabel("normal quantile")
    a2.set_ylabel("residual")
    for a in (a1, a2):
        format_axes(a)
    fig.tight_layout()
    return _save(fig, folder, f"residuals_{result.response}")


def plot_all(result, cfg, folder, alpha=0.05) -> list:
    apply_style()
    folder = Path(folder)
    out = [pareto(result, folder, alpha), main_effects(result, cfg, folder)]
    if cfg["kind"] != "plackett_burman":
        out.append(interactions(result, cfg, folder))
    out.append(residuals(result, folder))
    return [p for p in out if p]
