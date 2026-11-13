"""
Publication axes: limits on round numbers and evenly spaced ticks.

You give at most the limits (and, if you like, the tick step); the rest is worked out:

    nice_axis(data_min, data_max)                 -> (lo, hi, ticks)  everything automatic
    nice_axis(lim=(1.5, 2.5))                     -> ticks 1.50, 1.75, ... 2.50 (step chosen)
    nice_axis(lim=(1.5, 2.5), step=0.2)           -> np.arange(1.5, 2.5 + tiny, 0.2)
    nice_axis(lim=(1.5, 2.5), ticks=[1.5, 2, 2.5])-> exactly these

Steps are always 1, 2 or 5 x 10^k (never 2.5: 0.25 needs two decimals), and every label on
an axis gets the same number of decimals (2.0, 2.5, 3.0 — not 2, 2.5, 3).
Automatic limits are snapped OUT to a multiple of the step, so the box starts and ends on
a tick and every figure that shares the same data range gets the same axis.

set_axis(ax, "x", ...) applies the result to a matplotlib axis. shared_range(list of arrays)
gives one (min, max) over several figures, so a whole batch can use one scale.

YAML convention (any technique that uses this):
    xlim: [1.5, 2.5]        # optional — omitted = automatic
    xstep: 0.25             # optional — omitted = chosen from the limits
    xticks: [1.5, 2, 2.5]   # optional — exact ticks (wins over xstep)
    (same for y)
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np

_MANTISSAS = (1.0, 2.0, 5.0, 3.0, 4.0)     # 1, 2, 5 preferred on a tie
MAX_DECIMALS = 1          # tick labels: 2.0, 2.5 (one decimal) unless the range is too small


def nice_step(span: float, n_ticks: int = 5) -> float:
    """
    The step (1, 2, 3, 4 or 5 x 10^k) giving the number of tick LABELS closest to n_ticks
    over span, with at most MAX_DECIMALS decimals when the range allows it
    (span 1.72 -> 0.4: 1.2 1.6 2.0 2.4 2.8; span 1.0 -> 0.2: 1.5 1.7 1.9 2.1 2.3 2.5).
    """
    if not np.isfinite(span) or span <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(span / max(n_ticks, 1)))
    cands = [m * mag * f for f in (0.1, 1, 10) for m in _MANTISSAS]
    short = [st for st in cands if decimals_for(st) <= MAX_DECIMALS and span / st >= 2 - 1e-9]
    pref = {m: i for i, m in enumerate(_MANTISSAS)}
    def key(st):
        m = round(st / 10 ** math.floor(math.log10(st) + 1e-9), 6)
        return (abs(span / st + 1 - n_ticks), pref.get(m, 9))
    return min(short or cands, key=key)


def step_for_limits(lo: float, hi: float, n_ticks: int = 5) -> float:
    """A round step that divides [lo, hi] evenly (ticks on both limits), preferring steps
    the lower limit is a multiple of and about n_ticks intervals."""
    span = hi - lo
    if span <= 0:
        return 1.0
    best, score = None, None
    mag0 = 10 ** math.floor(math.log10(span))
    for mag in (mag0 / 100, mag0 / 10, mag0, mag0 * 10):
        for m in _MANTISSAS:
            st = m * mag
            n = span / st
            if abs(n - round(n)) > 1e-6 or not 2 <= round(n) <= 10:
                continue
            on_grid = abs(lo / st - round(lo / st)) < 1e-6
            sc = abs(round(n) + 1 - n_ticks) + (0 if on_grid else 0.5) + 0.1 * (m in (3.0, 4.0))
            if score is None or sc < score:
                best, score = st, sc
    return best or nice_step(span, n_ticks)


def _round(v, step):
    # kill float noise (0.30000000004) using the step's decimal places
    dec = max(0, -math.floor(math.log10(step)) + 2) if step > 0 else 6
    return round(v, dec)


def ticks_between(lo: float, hi: float, step: float) -> np.ndarray:
    """Every multiple of step inside [lo, hi] (inclusive, robust to float error)."""
    first = math.ceil(lo / step - 1e-9) * step
    out = np.arange(first, hi + step * 1e-6, step)
    return np.array([_round(v, step) for v in out])


def nice_axis(data_min: Optional[float] = None, data_max: Optional[float] = None, *,
              lim: Optional[Sequence[float]] = None, step: Optional[float] = None,
              ticks: Optional[Sequence[float]] = None, n_ticks: int = 5,
              pad: float = 0.0) -> Tuple[float, float, np.ndarray]:
    """(lo, hi, ticks). Given limits are kept exactly; otherwise they come from the data,
    padded by `pad` (fraction of the span) and snapped out to the tick step."""
    if ticks is not None:
        t = np.asarray(ticks, float)
        lo, hi = (lim if lim is not None else (t.min(), t.max()))
        return float(lo), float(hi), t
    if lim is not None:
        lo, hi = float(lim[0]), float(lim[1])
        a, b = min(lo, hi), max(lo, hi)
        st = float(step) if step else step_for_limits(a, b, n_ticks)
        # like np.arange(lo, hi + step, step): ticks start exactly at the lower limit
        k = int(math.floor((b - a) / st + 1e-6))
        return lo, hi, np.array([_round(a + i * st, st) for i in range(k + 1)])
    if data_min is None or data_max is None or not np.isfinite([data_min, data_max]).all():
        return 0.0, 1.0, np.linspace(0, 1, 6)
    span = (data_max - data_min) or abs(data_max) or 1.0
    a, b = data_min - pad * span, data_max + pad * span
    st = float(step) if step else nice_step(b - a, n_ticks)
    lo = math.floor(a / st + 1e-9) * st
    hi = math.ceil(b / st - 1e-9) * st
    if hi - lo < st:                      # single value
        lo, hi = lo - st, hi + st
    return _round(lo, st), _round(hi, st), ticks_between(lo, hi, st)


def shared_range(arrays: Iterable) -> Tuple[float, float]:
    """(min, max) over several data arrays — one scale for many figures."""
    vals = [np.asarray(a, float) for a in arrays]
    vals = [v[np.isfinite(v)] for v in vals if np.size(v)]
    vals = [v for v in vals if v.size]
    if not vals:
        return (np.nan, np.nan)
    return float(min(v.min() for v in vals)), float(max(v.max() for v in vals))


def set_axis(ax, which: str, data_min=None, data_max=None, *, lim=None, step=None, ticks=None,
             n_ticks: int = 5, pad: float = 0.0, invert: bool = False, min_decimals: int = 0):
    """Apply nice_axis to ax ('x' or 'y'); returns (lo, hi, ticks)."""
    lo, hi, t = nice_axis(data_min, data_max, lim=lim, step=step, ticks=ticks, n_ticks=n_ticks, pad=pad)
    if invert:
        lo, hi = max(lo, hi), min(lo, hi)
    (ax.set_xlim if which == "x" else ax.set_ylim)(lo, hi)
    format_ticks(ax, which, t, min_decimals)
    return lo, hi, t


def from_yaml(cfg: dict, which: str) -> dict:
    """{'lim':..., 'step':..., 'ticks':...} from a YAML block's xlim/xstep/xticks (or y...)."""
    out = {}
    if cfg.get(f"{which}lim") is not None:
        out["lim"] = cfg[f"{which}lim"]
    st = cfg.get(f"{which}step", cfg.get(f"{which}tick_step"))
    if st is not None:
        out["step"] = st
    tk = cfg.get(f"{which}ticks")
    if isinstance(tk, dict):                      # {start, stop, step}
        out["lim"] = out.get("lim", [tk["start"], tk["stop"]])
        out["step"] = tk["step"]
    elif tk is not None:
        out["ticks"] = tk
    return out


def span_axis(center: float, half_width: float, *, step: Optional[float] = None,
              n_ticks: int = 5) -> Tuple[float, float, np.ndarray]:
    """
    Same SCALE in every figure, each one symmetric about its own centre (e.g. the OCV):
    limits = center -/+ W/2, with the same width W (a whole number of tick steps) in every
    figure that gets the same half_width. Ticks are the round values inside the box.
    Use the largest half_width of the group so every figure's data fits.
    """
    def box(st):
        W = math.ceil(2 * half_width / st - 1e-9) * st
        lo, hi = center - W / 2, center + W / 2
        return lo, hi, ticks_between(lo, hi, st)

    if step:
        return box(float(step))
    span = 2 * half_width
    mag = 10 ** math.floor(math.log10(span / max(n_ticks, 1))) if span > 0 else 1.0
    cands = [m * mag * f for f in (0.1, 1, 10) for m in _MANTISSAS]
    cands = [st for st in cands if decimals_for(st) <= MAX_DECIMALS] or cands
    pref = {m: i for i, m in enumerate(_MANTISSAS)}
    # count the ticks that really fall inside the (OCV-centred) box, not span/step
    def key(st):
        m = round(st / 10 ** math.floor(math.log10(st) + 1e-9), 6)
        return (abs(len(box(st)[2]) - n_ticks), pref.get(m, 9))
    return box(min(cands, key=key))


def decimals_for(step: float) -> int:
    """Decimals a tick step needs: 0.5 -> 1, 0.2 -> 1, 5 -> 0, 0.05 -> 2."""
    if not step or not np.isfinite(step):
        return 1
    for d in range(0, 6):
        if abs(round(step, d) - step) < 1e-9 * max(1, abs(step)):
            return d
    return 6


def format_ticks(ax, which: str, ticks, min_decimals: int = 0) -> None:
    """Same number of decimals on every label of an axis (2.0, 2.5, 3.0); min_decimals=1
    writes 1.0, 2.0 for a step of 1."""
    from matplotlib.ticker import FixedLocator, FuncFormatter
    t = np.asarray(ticks, float)
    if t.size < 2:
        return
    d = max(min_decimals, decimals_for(float(np.min(np.diff(t)))), decimals_for(float(t[0])) if t[0] % 1 else 0)
    axis = ax.xaxis if which == "x" else ax.yaxis
    axis.set_major_locator(FixedLocator(t))
    axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.{d}f}".replace("-", "\u2212")))
