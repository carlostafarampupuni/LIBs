"""
Automatic XPS peak fitting — raw instrument export -> fitted components,
no XPSPeak / CasaXPS / KherveFitting step in between.

Pipeline for every (sample, region):

    1. read      read_avantage_workbook(): one Thermo Avantage .xls export,
                 one sheet per region ("C1s Scan", "P2p Scan", ...), several
                 samples side by side.
    2. shift     charge correction per sample, from the C 1s C–C/C–H peak
                 (default 284.8 eV), applied to every region of that sample.
    3. detect    signal-to-noise check — regions with no real peak (pure
                 noise) are reported as "no signal" instead of being fitted.
    4. bg        shirley | smart (Shirley clipped to data, like Avantage)
                 | tougaard (universal 2-parameter cross-section) | linear.
    5. fit       line shapes:
                   gl  Gaussian–Lorentzian sum (pseudo-Voigt), area-normalized
                   la  asymmetric Lorentzian convolved with a Gaussian, the
                       CasaXPS LA(α, β, m) family — for metals, graphitic C
                 constraints: BE windows, FWHM bounds, doublets (fixed
                 splitting + area ratio), optional shared FWHM.
    6. errors    monte_carlo(): Casa-style — synthesize noisy copies of the
                 best fit, re-run background + fit, report the spread.
    7. quant     atomic_percent(): areas / (RSF × KE^n) -> atomic % per
                 element, and per chemical species. RSFs can be read
                 straight from the Avantage "Peak Table" sheet.
    8. report    tables, Excel, figures, XPSPeak-layout export.

Chemistry (which peaks, where) lives in the YAML model file; this module
only knows physics and bookkeeping. See tuner.py for the interactive
peak-adjusting widget.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from lmfit import Parameters, minimize
from scipy.signal import savgol_filter

LN2 = np.log(2.0)
AL_KALPHA = 1486.6
_trapz = getattr(np, "trapezoid", None) or np.trapz


# ──────────────────────────────────────────────────────────────────────
# 1. Reading
# ──────────────────────────────────────────────────────────────────────
def read_avantage_sheet(xl: pd.ExcelFile, sheet: str) -> pd.DataFrame:
    """
    One Avantage region sheet -> DataFrame [BE, <sample1>, <sample2>, ...],
    BE descending as exported. Sample names are taken from the
    "Position (Pos)" row (or the row above it, for exports that put them
    there).
    """
    raw = xl.parse(sheet, header=None)
    pos_row = next(
        (i for i in range(len(raw))
         if raw.shape[1] > 1 and isinstance(raw.iat[i, 1], str)
         and "position (pos)" in raw.iat[i, 1].lower()),
        None,
    )
    if pos_row is None:
        raise ValueError(f"[{sheet}] no 'Position (Pos)' header row found")

    names = [str(v).strip() for v in raw.iloc[pos_row, 2:] if pd.notna(v)]
    if not names and pos_row > 0:
        names = [str(v).strip() for v in raw.iloc[pos_row - 1, 2:] if pd.notna(v)]
    if not names:
        raise ValueError(f"[{sheet}] no sample names found")

    ev_row = next(
        (i for i in range(pos_row, len(raw))
         if isinstance(raw.iat[i, 0], str) and raw.iat[i, 0].strip().lower() == "ev"),
        None,
    )
    if ev_row is None:
        raise ValueError(f"[{sheet}] no 'eV' units row found")

    data = raw.iloc[ev_row + 1:, [0] + list(range(2, 2 + len(names)))]
    data = data.apply(pd.to_numeric, errors="coerce").dropna()
    data.columns = ["BE"] + names
    return data.reset_index(drop=True)


def read_avantage_workbook(path, regions: Optional[Sequence[str]] = None) -> Dict[str, pd.DataFrame]:
    """
    Every "<Region> Scan" sheet of an Avantage export ->
    {"C1s": DataFrame[BE, samples...], "P2p": ..., ...}.
    """
    xl = pd.ExcelFile(path)
    out = {}
    for sheet in xl.sheet_names:
        if not sheet.lower().endswith(" scan"):
            continue
        region = sheet[:-5].strip()
        if regions is not None and region not in regions:
            continue
        out[region] = read_avantage_sheet(xl, sheet)
    return out


def read_avantage_peak_table(path) -> pd.DataFrame:
    """
    The Avantage "Peak Table" sheet (if present) as a DataFrame — includes
    the instrument's sensitivity factor column ("SF ...") and its own
    Area / Atomic % for cross-checking.
    """
    xl = pd.ExcelFile(path)
    sheet = next((s for s in xl.sheet_names if s.lower().startswith("peak table")), None)
    if sheet is None:
        return pd.DataFrame()
    raw = xl.parse(sheet, header=None)
    hdr = next(i for i in range(len(raw)) if any(str(v).strip() == "Name" for v in raw.iloc[i]))
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(v).strip() for v in raw.iloc[hdr]]
    df = df.loc[:, [c for c in df.columns if c and c != "nan"]].dropna(subset=["Name"])
    for c in df.columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().all():
            df[c] = conv
    return df.reset_index(drop=True)


def avantage_sensitivity_factors(path) -> Dict[str, float]:
    """{"C1s": 1.0, "F1s": 4.118, ...} from the Peak Table's "SF ..." column."""
    pt = read_avantage_peak_table(path)
    if pt.empty:
        return {}
    sf_col = next((c for c in pt.columns if c.upper().startswith("SF")), None)
    return {} if sf_col is None else {str(n).strip(): float(v) for n, v in zip(pt["Name"], pt[sf_col])}


# ──────────────────────────────────────────────────────────────────────
# 2. Backgrounds and signal detection
# ──────────────────────────────────────────────────────────────────────
def _end_levels(y: np.ndarray, n_avg: int):
    n = max(1, min(n_avg, len(y) // 4))
    return float(np.mean(y[:n])), float(np.mean(y[-n:]))


def shirley_background(x: np.ndarray, y: np.ndarray, n_avg: int = 5,
                       max_iter: int = 100, tol: float = 1e-6) -> np.ndarray:
    """
    Iterative Shirley background. x must be ASCENDING binding energy.
    The background at BE x is proportional to the peak area at lower BE
    (inelastically scattered electrons appear at higher BE).
    """
    lo, hi = _end_levels(y, n_avg)
    B = np.full_like(y, lo, dtype=float)
    for _ in range(max_iter):
        s = y - B
        cum = np.concatenate([[0.0], np.cumsum(0.5 * (s[1:] + s[:-1]) * np.diff(x))])
        total = cum[-1]
        if total <= 0:
            break
        B_new = lo + (hi - lo) * cum / total
        if np.max(np.abs(B_new - B)) < tol * max(abs(hi), 1.0):
            B = B_new
            break
        B = B_new
    return B


def tougaard_background(x: np.ndarray, y: np.ndarray, n_avg: int = 5,
                        C: float = 1643.0) -> np.ndarray:
    """
    Tougaard background with the universal cross-section
        K(T) = B·T / (C + T²)²,   T = energy loss (eV), C = 1643 eV².
    B(x_i) = Σ_{x_j < x_i} K(x_i − x_j) · (y_j − y_low) · Δx, with the
    scale B chosen so the background meets the data at the high-BE end.
    x must be ASCENDING BE. Works best when the window extends well
    (≳ 20–30 eV) above the peak; on narrow windows prefer Shirley.
    """
    lo, hi = _end_levels(y, n_avg)
    s = np.clip(y - lo, 0, None)
    dx = np.gradient(x)
    T = x[:, None] - x[None, :]                       # loss for i (row) from j (col)
    K = np.where(T > 0, T / (C + T ** 2) ** 2, 0.0)
    raw = K @ (s * dx)
    n = max(1, min(n_avg, len(y) // 4))
    end = float(np.mean(raw[-n:]))
    scale = (hi - lo) / end if end > 0 else 0.0
    return lo + scale * raw


def linear_background(x: np.ndarray, y: np.ndarray, n_avg: int = 5) -> np.ndarray:
    lo, hi = _end_levels(y, n_avg)
    return lo + (hi - lo) * (x - x[0]) / (x[-1] - x[0])


def make_background(kind: str, x: np.ndarray, y: np.ndarray, n_avg: int = 5,
                    tougaard_C: float = 1643.0) -> np.ndarray:
    kind = kind.lower()
    if kind == "linear":
        return linear_background(x, y, n_avg)
    if kind == "tougaard":
        return tougaard_background(x, y, n_avg, C=tougaard_C)
    B = shirley_background(x, y, n_avg)
    if kind == "smart":
        # Avantage "Smart": Shirley, but never above the data. Smooth hard
        # (~1/8 of the window) so the clip follows peaks, not noise.
        win = max(5, (len(y) // 8) | 1)
        ys = savgol_filter(y, min(win, len(y) - (1 - len(y) % 2)), 2)
        B = np.minimum(B, ys)
    elif kind != "shirley":
        raise ValueError(f"Unknown background '{kind}' (use shirley / smart / tougaard / linear)")
    return B


def noise_sigma(y: np.ndarray) -> float:
    """Point-to-point noise estimate, robust to peaks (MAD of first differences)."""
    d = np.diff(y)
    return float(1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2))


def signal_to_noise(y: np.ndarray, bg: np.ndarray) -> float:
    win = min(len(y) - (1 - len(y) % 2), 21)
    ys = savgol_filter(y, win, 2) if win >= 5 else y
    sig = noise_sigma(y)
    return float(np.max(ys - bg) / sig) if sig > 0 else np.inf


# ──────────────────────────────────────────────────────────────────────
# 3. Line shapes
# ──────────────────────────────────────────────────────────────────────
def gl_peak(x, center, fwhm, area, lg):
    """
    Area-normalized Gaussian–Lorentzian sum (pseudo-Voigt), same FWHM for
    both parts. lg = Lorentzian fraction (0 = pure Gaussian; 0.3 ≈ GL(30)).
    """
    g = np.sqrt(4 * LN2 / np.pi) / fwhm * np.exp(-4 * LN2 * ((x - center) / fwhm) ** 2)
    l = (2 / (np.pi * fwhm)) / (1 + 4 * ((x - center) / fwhm) ** 2)
    return area * ((1 - lg) * g + lg * l)


def _la_grid(center, width, alpha, beta, gauss, x_lo, x_hi, dx):
    lo = min(x_lo, center) - 10 * width - 4 * gauss
    hi = max(x_hi, center) + 60 * width + 4 * gauss       # long high-BE tail
    g = np.arange(lo, hi, dx)
    t = 2 * (g - center) / width
    L = 1.0 / (1.0 + t * t)
    y = np.where(g > center, L ** alpha, L ** beta)        # α: high-BE (tail) side
    if gauss > 0:
        sig = gauss / (2 * np.sqrt(2 * LN2))
        k = np.arange(-4 * sig, 4 * sig + dx, dx)
        ker = np.exp(-0.5 * (k / sig) ** 2)
        y = np.convolve(y, ker / ker.sum(), mode="same")
    y /= _trapz(y, g)
    return g, y


def la_peak(x, center, width, area, alpha=1.2, beta=5.0, gauss=0.6):
    """
    Asymmetric line shape in the spirit of CasaXPS LA(α, β, m):
    a Lorentzian of FWHM `width`, raised to power α on the high-BE side
    (α ≈ 1 → long metallic / graphitic tail) and β on the low-BE side,
    convolved with a Gaussian of FWHM `gauss` (eV). Area-normalized
    numerically, so `area` is the integrated area including the tail.
    """
    x = np.asarray(x, float)
    step = float(np.min(np.abs(np.diff(x)))) if len(x) > 1 else 0.05
    g, y = _la_grid(center, width, max(alpha, 0.55), beta, gauss, x.min(), x.max(), min(0.02, step / 2))
    return area * np.interp(x, g, y)


def measured_fwhm(shape: str, width: float, **kw) -> float:
    """Actual FWHM of a line (differs from `width` for LA shapes)."""
    if shape != "la":
        return width
    g, y = _la_grid(0.0, width, max(kw.get("alpha", 1.2), 0.55), kw.get("beta", 5.0),
                    kw.get("gauss", 0.6), -5 * width, 5 * width, 0.002)
    above = g[y >= y.max() / 2]
    return float(above.max() - above.min())


# ──────────────────────────────────────────────────────────────────────
# 4. Model specification (from YAML)
# ──────────────────────────────────────────────────────────────────────
@dataclass
class ComponentSpec:
    name: str
    center: float
    window: tuple                 # (min, max) BE for the main (or 2p3/2, 3d5/2...) line
    fwhm: float = 1.3
    fwhm_bounds: tuple = (0.6, 2.5)
    shape: str = "gl"             # gl | la
    lg: float = 0.3               # gl only
    vary_lg: bool = False
    alpha: float = 1.2            # la only: high-BE tail exponent
    beta: float = 5.0             # la only: low-BE side exponent
    gauss: float = 0.6            # la only: Gaussian FWHM (eV)
    vary_asym: bool = False       # la only: let alpha/beta float
    # spin–orbit doublet: second line at center + splitting, area*ratio, fwhm*fwhm_ratio
    splitting: Optional[float] = None
    ratio: float = 0.5
    fwhm_ratio: float = 1.0
    color: Optional[str] = None   # figure colour (keep one colour per species across regions)

    @property
    def area_factor(self) -> float:
        return 1 + (self.ratio if self.splitting else 0)


@dataclass
class RegionModel:
    region: str
    fit_range: Optional[tuple] = None        # (low, high) BE after charge correction
    background: str = "shirley"
    n_avg: int = 5
    tougaard_C: float = 1643.0
    share_fwhm: bool = False
    min_snr: float = 5.0
    components: List[ComponentSpec] = field(default_factory=list)


_INHERITED = ("splitting", "ratio", "fwhm_ratio", "lg", "fwhm", "fwhm_bounds", "shape",
              "alpha", "beta", "gauss", "vary_asym", "vary_lg")


def _component_from_dict(d: dict, defaults: dict) -> ComponentSpec:
    m = {**defaults, **d}
    window = tuple(m.get("window", (m["center"] - 0.6, m["center"] + 0.6)))
    return ComponentSpec(
        name=m["name"], center=float(m["center"]), window=(float(window[0]), float(window[1])),
        fwhm=float(m.get("fwhm", 1.3)), fwhm_bounds=tuple(m.get("fwhm_bounds", (0.6, 2.5))),
        shape=str(m.get("shape", "gl")).lower(),
        lg=float(m.get("lg", 0.3)), vary_lg=bool(m.get("vary_lg", False)),
        alpha=float(m.get("alpha", 1.2)), beta=float(m.get("beta", 5.0)),
        gauss=float(m.get("gauss", 0.6)), vary_asym=bool(m.get("vary_asym", False)),
        splitting=m.get("splitting"), ratio=float(m.get("ratio", 0.5)),
        fwhm_ratio=float(m.get("fwhm_ratio", 1.0)),
        color=d.get("color"),
    )


def load_fit_config(path) -> dict:
    """Read the YAML fit model file -> {'settings', 'regions': {name: RegionModel}, 'path'}."""
    import yaml

    path = Path(path)
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    settings = cfg.get("settings", {}) or {}
    comp_defaults = settings.get("component_defaults", {}) or {}
    regions = {}
    for name, r in (cfg.get("regions") or {}).items():
        region_defaults = {**comp_defaults, **{k: r[k] for k in _INHERITED if k in r}}
        regions[name] = RegionModel(
            region=name,
            fit_range=tuple(r["fit_range"]) if r.get("fit_range") else None,
            background=r.get("background", settings.get("background", "shirley")),
            n_avg=int(r.get("n_avg", settings.get("n_avg", 5))),
            tougaard_C=float(r.get("tougaard_C", settings.get("tougaard_C", 1643.0))),
            share_fwhm=bool(r.get("share_fwhm", settings.get("share_fwhm", False))),
            min_snr=float(r.get("min_snr", settings.get("min_snr", 5.0))),
            components=[_component_from_dict(c, region_defaults) for c in r.get("components", [])],
        )
    return {"settings": settings, "regions": regions, "raw": cfg, "path": path}


# ──────────────────────────────────────────────────────────────────────
# 5. Fitting one spectrum
# ──────────────────────────────────────────────────────────────────────
@dataclass
class FitOutcome:
    sample: str
    region: str
    status: str                         # "fitted" | "no signal" | "no model" | "failed: ..."
    shift: float                        # charge correction applied (eV)
    x: np.ndarray                       # corrected BE, ascending, within fit range
    y: np.ndarray
    background: np.ndarray
    envelope: Optional[np.ndarray] = None
    components: Dict[str, np.ndarray] = field(default_factory=dict)   # name -> curve (no bg)
    table: pd.DataFrame = field(default_factory=pd.DataFrame)
    snr: float = np.nan
    r2: float = np.nan
    redchi: float = np.nan
    starts: str = ""                    # "k/n": how many random starts reached the best χ²
    model: Optional[RegionModel] = field(default=None, repr=False)
    params: Optional[Parameters] = field(default=None, repr=False)

    @property
    def fitted(self) -> bool:
        return self.status == "fitted"

    @property
    def total_area(self) -> float:
        return float(self.table["area"].sum()) if self.fitted else 0.0

    def to_xpspeak_array(self) -> np.ndarray:
        """
        Same column layout as an XPSPeak .dat export, BE descending:
            BE, raw, peak sum (+bg), background, peak_1 (+bg), ..., peak_n (+bg)
        so auto-fits can go through import_xps()/quantify_peaks() unchanged.
        """
        if not self.fitted:
            raise ValueError(f"{self.sample} {self.region}: not fitted ({self.status})")
        cols = [self.x, self.y, self.envelope + self.background, self.background]
        cols += [c + self.background for c in self.components.values()]
        return np.column_stack(cols)[::-1]

    def export_dat(self, path) -> Path:
        path = Path(path)
        header = "\t".join(["B.E.", "Raw Intensity", "Peak Sum", "Background", *self.components.keys()])
        np.savetxt(path, self.to_xpspeak_array(), delimiter="\t", header=header, comments="", fmt="%.5g")
        return path


def _build_params(model: RegionModel, x, signal) -> Parameters:
    p = Parameters()
    xmin, xmax = x.min(), x.max()
    for i, c in enumerate(model.components):
        k = f"c{i}"
        lo, hi = max(c.window[0], xmin), min(c.window[1], xmax)
        if lo >= hi:            # window outside the measured range
            lo, hi = c.window
        p.add(f"{k}_center", value=float(np.clip(c.center, lo, hi)), min=lo, max=hi)
        if model.share_fwhm and i > 0:
            p.add(f"{k}_fwhm", expr="c0_fwhm")
        else:
            p.add(f"{k}_fwhm", value=float(np.clip(c.fwhm, *c.fwhm_bounds)),
                  min=c.fwhm_bounds[0], max=c.fwhm_bounds[1])
        h = max(float(np.interp(c.center, x, signal)), 0.0)
        p.add(f"{k}_area", value=max(h * c.fwhm * 0.5, 1e-6), min=0)
        p.add(f"{k}_lg", value=c.lg, min=0, max=1, vary=c.vary_lg and c.shape == "gl")
        p.add(f"{k}_alpha", value=c.alpha, min=0.6, max=3.0, vary=c.vary_asym and c.shape == "la")
        p.add(f"{k}_beta", value=c.beta, min=1.0, max=20.0, vary=c.vary_asym and c.shape == "la")
    return p


def _line(c: ComponentSpec, x, center, width, area, pv):
    if c.shape == "la":
        return la_peak(x, center, width, area, pv["alpha"], pv["beta"], c.gauss)
    return gl_peak(x, center, width, area, pv["lg"])


def _component_curves(model: RegionModel, params, x) -> Dict[str, np.ndarray]:
    curves = {}
    for i, c in enumerate(model.components):
        k = f"c{i}"
        pv = {n: params[f"{k}_{n}"].value for n in ("center", "fwhm", "area", "lg", "alpha", "beta")}
        y = _line(c, x, pv["center"], pv["fwhm"], pv["area"], pv)
        if c.splitting:
            y = y + _line(c, x, pv["center"] + c.splitting, pv["fwhm"] * c.fwhm_ratio,
                          pv["area"] * c.ratio, pv)
        curves[c.name] = y
    return curves


def _component_stats(model: RegionModel, params) -> List[dict]:
    """center, fwhm, area (doublet-summed) and area % for each component."""
    stats = []
    for i, c in enumerate(model.components):
        k = f"c{i}"
        stats.append({
            "center": params[f"{k}_center"].value,
            "fwhm": measured_fwhm(c.shape, params[f"{k}_fwhm"].value,
                                  alpha=params[f"{k}_alpha"].value, beta=params[f"{k}_beta"].value,
                                  gauss=c.gauss),
            "area": params[f"{k}_area"].value * c.area_factor,
        })
    total = sum(s["area"] for s in stats)
    for s in stats:
        s["pct"] = 100 * s["area"] / total if total > 0 else 0.0
    return stats


def _run_fit(model: RegionModel, x, signal, sigma, params):
    def residual(p):
        return (sum(_component_curves(model, p, x).values()) - signal) / sigma
    return minimize(residual, params, method="least_squares")


def fit_spectrum(be: np.ndarray, counts: np.ndarray, model: Optional[RegionModel],
                 sample: str, region: str, shift: float = 0.0,
                 start_params: Optional[Parameters] = None, n_starts: int = 10,
                 seed: Optional[int] = 0) -> FitOutcome:
    """
    Fit one spectrum. `be` in instrument BE (any order); `shift` is added
    first. n_starts > 1 refits from random starting points and keeps the
    best (lowest χ²) — see the multi-start note below.
    """
    x = np.asarray(be, float) + shift
    y = np.asarray(counts, float)
    order = np.argsort(x)
    x, y = x[order], y[order]
    if model is not None and model.fit_range:
        lo, hi = sorted(model.fit_range)
        m = (x >= lo) & (x <= hi)
        x, y = x[m], y[m]

    kind = model.background if model else "shirley"
    n_avg = model.n_avg if model else 5
    bg = make_background(kind, x, y, n_avg, model.tougaard_C if model else 1643.0)
    snr = signal_to_noise(y, bg)
    base = dict(sample=sample, region=region, shift=shift, x=x, y=y, background=bg, snr=snr, model=model)

    if model is None or not model.components:
        return FitOutcome(status="no model", **base)
    if snr < model.min_snr:
        return FitOutcome(status="no signal", **base)

    signal = y - bg
    params = start_params if start_params is not None else _build_params(model, x, signal)
    sigma = max(noise_sigma(y), 1e-9)

    # Multi-start: overlapping peaks have several local minima, and a single
    # least-squares run stops in whichever is nearest its starting guess.
    # Refit from random starts inside the windows and keep the lowest χ².
    rng = np.random.default_rng(seed)
    tries = []
    for k in range(max(1, n_starts)):
        p = copy.deepcopy(params)
        if k > 0:
            for i in range(len(model.components)):
                c = p[f"c{i}_center"]
                c.value = rng.uniform(c.min, c.max)
                p[f"c{i}_area"].value *= rng.uniform(0.2, 3.0)
                w = p[f"c{i}_fwhm"]
                if w.expr is None:
                    w.value = rng.uniform(w.min, w.max)
        try:
            tries.append(_run_fit(model, x, signal, sigma, p))
        except Exception as exc:  # noqa: BLE001 — report, don't crash a batch
            if k == 0 and n_starts <= 1:
                return FitOutcome(status=f"failed: {exc}", **base)
    if not tries:
        return FitOutcome(status="failed: no start converged", **base)
    res = min(tries, key=lambda r: r.chisqr)
    n_agree = sum(r.chisqr <= res.chisqr * 1.01 for r in tries)

    comps = _component_curves(model, res.params, x)
    env = sum(comps.values())
    ss_res = float(np.sum((signal - env) ** 2))
    ss_tot = float(np.sum((signal - signal.mean()) ** 2))

    stats = _component_stats(model, res.params)
    total = sum(s["area"] for s in stats)
    rows = []
    for i, (c, s) in enumerate(zip(model.components, stats)):
        cen = res.params[f"c{i}_center"]
        at_bound = (abs(cen.value - cen.min) < 0.01) or (abs(cen.value - cen.max) < 0.01)
        rows.append({
            "sample": sample, "region": region, "component": c.name,
            "BE (eV)": round(s["center"], 2),
            "FWHM (eV)": round(s["fwhm"], 2),
            "shape": (f"GL({round(100 * res.params[f'c{i}_lg'].value)})" if c.shape == "gl" else
                      f"LA({res.params[f'c{i}_alpha'].value:.2f},{res.params[f'c{i}_beta'].value:.1f},"
                      f"{c.gauss:g})"),
            "area": s["area"],
            "area %": s["pct"],
            "doublet": f"+{c.splitting} eV, {c.ratio:g}" if c.splitting else "",
            "at BE limit": bool(at_bound and s["area"] > 0.01 * total),
        })

    return FitOutcome(
        status="fitted", envelope=env, components=comps, table=pd.DataFrame(rows),
        r2=1 - ss_res / ss_tot if ss_tot > 0 else np.nan, redchi=float(res.redchi),
        starts=f"{n_agree}/{len(tries)}", params=res.params, **base,
    )


# ──────────────────────────────────────────────────────────────────────
# 6. Monte Carlo uncertainties
# ──────────────────────────────────────────────────────────────────────
def monte_carlo(outcome: FitOutcome, n: int = 100, seed: Optional[int] = 0) -> FitOutcome:
    """
    CasaXPS-style error estimate. n times: take the best fit (background +
    envelope), add noise with the same σ as the actual residual, recompute
    the background, refit from the best-fit parameters. The standard
    deviation of each result across the n refits is its uncertainty —
    it captures noise, background placement and peak overlap together.
    Adds "± BE", "± FWHM", "± area %" columns to outcome.table (in place).
    """
    if not outcome.fitted or n < 2 or outcome.params is None:   # imported fits: nothing to perturb
        return outcome
    m = outcome.model
    rng = np.random.default_rng(seed)
    best = outcome.background + outcome.envelope
    sig_res = float(np.std(outcome.y - best))
    sigma = max(noise_sigma(outcome.y), 1e-9)
    draws = []
    for _ in range(n):
        ys = best + rng.normal(0.0, sig_res, size=best.shape)
        bg = make_background(m.background, outcome.x, ys, m.n_avg, m.tougaard_C)
        try:
            r = _run_fit(m, outcome.x, ys - bg, sigma, copy.deepcopy(outcome.params))
        except Exception:  # noqa: BLE001
            continue
        draws.append(_component_stats(m, r.params))
    if len(draws) < 2:
        return outcome
    for key, col, nd in (("center", "± BE", 3), ("fwhm", "± FWHM", 3), ("pct", "± area %", 2)):
        arr = np.array([[s[key] for s in d] for d in draws])
        outcome.table[col] = np.round(arr.std(axis=0, ddof=1), nd)
    outcome.table["MC runs"] = len(draws)
    return outcome


def monte_carlo_all(outcomes: Sequence[FitOutcome], n: int = 100, seed: Optional[int] = 0,
                    verbose: bool = True) -> List[FitOutcome]:
    fitted = [o for o in outcomes if o.fitted]
    for i, o in enumerate(fitted, 1):
        monte_carlo(o, n=n, seed=seed)
        if verbose:
            print(f"\r  Monte Carlo {i}/{len(fitted)}  ({o.sample} {o.region})      ", end="")
    if verbose:
        print()
    return list(outcomes)


# ──────────────────────────────────────────────────────────────────────
# 7. Charge correction + batch
# ──────────────────────────────────────────────────────────────────────
def charge_shift(be: np.ndarray, counts: np.ndarray, reference: float = 284.8,
                 search: tuple = (282.0, 286.5)) -> float:
    """
    Shift (eV) that puts the C 1s C–C/C–H maximum at `reference`.
    Peak position = vertex of a parabola through the top of the smoothed,
    Shirley-subtracted spectrum inside the `search` window (instrument BE).
    """
    x = np.asarray(be, float)
    y = np.asarray(counts, float)
    o = np.argsort(x)
    x, y = x[o], y[o]
    s = y - shirley_background(x, y)
    win = min(len(s) - (1 - len(s) % 2), 9)
    s = savgol_filter(s, win, 2) if win >= 5 else s
    m = (x >= search[0]) & (x <= search[1])
    xi, si = x[m], s[m]
    j = int(np.argmax(si))
    lo, hi = max(j - 3, 0), min(j + 4, len(si))
    a, b, _ = np.polyfit(xi[lo:hi], si[lo:hi], 2)
    peak = -b / (2 * a) if a < 0 else xi[j]
    return float(reference - peak)


def compute_shifts(data: Dict[str, pd.DataFrame], settings: dict, samples: Sequence[str]) -> Dict[str, float]:
    cc = settings.get("charge_correction", {}) or {}
    if cc.get("fixed") is not None:
        return {s: float(cc["fixed"]) for s in samples}
    region = cc.get("region", "C1s")
    if not cc.get("enabled", True) or region not in data:
        return {s: 0.0 for s in samples}
    ref = data[region]
    return {s: charge_shift(ref["BE"].values, ref[s].values,
                            reference=float(cc.get("reference", 284.8)),
                            search=tuple(cc.get("search", (282.0, 286.5))))
            for s in samples if s in ref.columns}


def fit_workbook(path, fit_cfg: dict, samples: Optional[Sequence[str]] = None,
                 regions: Optional[Sequence[str]] = None, verbose: bool = True,
                 monte_carlo_runs: int = 0) -> List[FitOutcome]:
    """
    Fit every region × sample in an Avantage workbook. Returns a flat list
    of FitOutcome; use results_table()/quality_table()/atomic_percent().
    monte_carlo_runs > 0 also adds MC uncertainties to every fit.
    """
    models: Dict[str, RegionModel] = fit_cfg["regions"]
    data = read_avantage_workbook(path, regions=regions)

    all_samples = list(dict.fromkeys(s for df in data.values() for s in df.columns[1:]))
    samples = [s for s in all_samples if samples is None or s in samples]
    shifts = compute_shifts(data, fit_cfg["settings"], samples)

    out = []
    for region, df in data.items():
        model = models.get(region)
        for s in samples:
            if s not in df.columns:
                continue
            r = fit_spectrum(df["BE"].values, df[s].values, model, s, region, shift=shifts.get(s, 0.0),
                             n_starts=int(fit_cfg["settings"].get("n_starts", 10)))
            out.append(r)
            if verbose:
                extra = f"R²={r.r2:.4f}" if r.fitted else ""
                print(f"  {s:>8} {region:<5} shift {r.shift:+.2f} eV  SNR {r.snr:6.1f}  {r.status:<10} {extra}")
    if monte_carlo_runs:
        monte_carlo_all(out, n=monte_carlo_runs, verbose=verbose)
    return out


# ──────────────────────────────────────────────────────────────────────
# 8. Quantification (atomic %)
# ──────────────────────────────────────────────────────────────────────
def element_of(region: str) -> str:
    m = re.match(r"([A-Z][a-z]?)", region)
    return m.group(1) if m else region


def _quant_settings(fit_cfg: dict, source=None):
    q = (fit_cfg.get("settings", {}) or {}).get("quantification", {}) or {}
    rsf = {}
    if q.get("use_workbook_sf", True) and source is not None:
        rsf.update(avantage_sensitivity_factors(source))
    rsf.update({k: float(v) for k, v in (q.get("rsf") or {}).items() if v is not None})
    return rsf, float(q.get("ke_exponent", 0.75)), float(q.get("source_energy", AL_KALPHA)), \
        float(q.get("transmission_exponent", 0.0))


def normalized_area(area: float, be: float, rsf: float, ke_exponent: float = 0.75,
                    source_energy: float = AL_KALPHA, transmission_exponent: float = 0.0) -> float:
    """
    area / (RSF × λ(KE) × T(KE)) with λ ∝ KE^ke_exponent (escape-depth
    correction; 0.75 reproduces Avantage's TPP-2M correction closely) and
    T ∝ KE^-transmission_exponent (0 when the data are already
    transmission-corrected, as Avantage exports are).
    """
    ke = source_energy - be
    return area / (rsf * ke ** ke_exponent * ke ** (-transmission_exponent))


def atomic_percent(outcomes: Sequence[FitOutcome], fit_cfg: dict, source=None,
                   by: str = "element") -> pd.DataFrame:
    """
    Atomic % per sample.
      by="element": one row per element (from each region's total fitted area)
      by="species": one row per fitted component (element at% × component area %)
    Regions reported "no signal" count as 0 (below detection) and are listed
    as such. RSFs come from the YAML `quantification.rsf` and/or the
    workbook's Avantage Peak Table.
    """
    rsf, n_ke, hv, n_tx = _quant_settings(fit_cfg, source)
    rows = []
    for o in outcomes:
        if o.status not in ("fitted", "no signal"):
            continue
        if o.fitted:
            t = o.table
            be = float(np.average(t["BE (eV)"], weights=np.maximum(t["area"], 1e-12)))
            if o.region not in rsf:
                raise KeyError(f"No sensitivity factor for {o.region} — add it under "
                               f"settings.quantification.rsf in the YAML")
            norm = normalized_area(o.total_area, be, rsf[o.region], n_ke, hv, n_tx)
        else:
            norm = 0.0
        rows.append({"sample": o.sample, "region": o.region, "element": element_of(o.region),
                     "norm": norm, "outcome": o})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["at %"] = 100 * df["norm"] / df.groupby("sample")["norm"].transform("sum")

    if by == "element":
        tab = df.pivot_table(index="element", columns="sample", values="at %", sort=False)
        order = list(dict.fromkeys(df["sample"]))
        return tab[order].round(2)

    srows = []
    for _, r in df.iterrows():
        o = r["outcome"]
        if not o.fitted:
            srows.append({"sample": r["sample"], "element": r["element"], "species": "(below detection)",
                          "at %": 0.0})
            continue
        for _, c in o.table.iterrows():
            srows.append({"sample": r["sample"], "element": r["element"],
                          "species": c["component"], "at %": r["at %"] * c["area %"] / 100})
    s = pd.DataFrame(srows)
    tab = s.pivot_table(index=["element", "species"], columns="sample", values="at %", sort=False)
    order = list(dict.fromkeys(s["sample"]))
    return tab[order].round(2)


# ──────────────────────────────────────────────────────────────────────
# 9. Tables, export, plotting
# ──────────────────────────────────────────────────────────────────────
def results_table(outcomes: Sequence[FitOutcome]) -> pd.DataFrame:
    tabs = [o.table for o in outcomes if o.fitted and not o.table.empty]
    return pd.concat(tabs, ignore_index=True) if tabs else pd.DataFrame()


def quality_table(outcomes: Sequence[FitOutcome]) -> pd.DataFrame:
    return pd.DataFrame([{
        "sample": o.sample, "region": o.region, "status": o.status,
        "background": o.model.background if o.model else "",
        "charge shift (eV)": round(o.shift, 2), "SNR": round(o.snr, 1),
        "R²": round(o.r2, 4) if o.fitted else np.nan,
        "reduced χ²": round(o.redchi, 2) if o.fitted else np.nan,
        "starts at best": o.starts,
        "components at BE limit": (", ".join(o.table.loc[o.table["at BE limit"], "component"])
                                   if o.fitted else ""),
    } for o in outcomes])


def export_excel(outcomes: Sequence[FitOutcome], path, fit_cfg: Optional[dict] = None, source=None) -> Path:
    """Area-% summary, atomic %, species at %, peak table and fit quality (tables only)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    peaks = results_table(outcomes)
    with pd.ExcelWriter(path) as xw:
        if not peaks.empty:
            pivot = peaks.pivot_table(index=["region", "component"], columns="sample",
                                      values="area %", sort=False).round(1)
            pivot.to_excel(xw, sheet_name="Area % summary")
        if fit_cfg is not None:
            try:
                atomic_percent(outcomes, fit_cfg, source, by="element").to_excel(xw, sheet_name="Atomic %")
                atomic_percent(outcomes, fit_cfg, source, by="species").to_excel(xw, sheet_name="Species at %")
            except KeyError as exc:
                print(f"⚠️  atomic % skipped: {exc}")
        if not peaks.empty:
            peaks.to_excel(xw, sheet_name="Peak table", index=False)
        quality_table(outcomes).to_excel(xw, sheet_name="Fit quality", index=False)
        # fitted curves are not repeated here: they are in each figure's plot data (Exports/PlotData/XPS/)
    return path


COMPONENT_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3",
                    "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD"]


def draw_fit(ax, o: FitOutcome, normalize: bool = False, res_ax=None, title: Optional[str] = None):
    """Draw one FitOutcome on an axis (used by plot_region and the tuner)."""
    k = (o.y.max() - o.background.min()) if normalize else 1.0
    ax.plot(o.x, o.y / k, "o", ms=2.2, mfc="none", mec="0.35", mew=0.6, label="Raw")
    ax.plot(o.x, o.background / k, "--", lw=0.9, c="0.5", label="Background")
    if o.envelope is not None:
        for (name, c), col in zip(o.components.items(), COMPONENT_COLORS * 3):
            if c.max() <= 0:
                continue
            ax.fill_between(o.x, o.background / k, (o.background + c) / k, color=col, alpha=0.55,
                            lw=0.6, ec=col, label=name)
        ax.plot(o.x, (o.envelope + o.background) / k, c="crimson", lw=1.2, label="Envelope")
        if res_ax is not None:
            res_ax.plot(o.x, (o.y - o.background - o.envelope) / k, lw=0.7, c="0.3")
            res_ax.axhline(0, c="crimson", lw=0.6)
    if title is None:
        title = f"{o.sample}  (R² {o.r2:.3f})" if o.fitted else f"{o.sample}  — {o.status}"
    ax.set_title(title, fontsize=9)


def plot_region(outcomes: Sequence[FitOutcome], region: str, save_to=None,
                normalize: bool = False, show_residual: bool = True):
    """One row of panels (one per sample) for `region`, with residuals underneath."""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    rs = [o for o in outcomes if o.region == region]
    if not rs:
        raise ValueError(f"No outcomes for region {region}")
    n = len(rs)
    fig, axes = plt.subplots(2 if show_residual else 1, n, figsize=(3.6 * n, 4.4 if show_residual else 3.4),
                             sharex=True, squeeze=False,
                             gridspec_kw={"height_ratios": [4, 1]} if show_residual else None)
    for j, o in enumerate(rs):
        ax = axes[0, j]
        draw_fit(ax, o, normalize, axes[1, j] if (show_residual and o.fitted) else None)
        if show_residual and not o.fitted:
            axes[1, j].axis("off")
        ax.tick_params(labelsize=8)
        ax.xaxis.set_major_locator(MaxNLocator(5))
        if show_residual:
            axes[1, j].tick_params(labelsize=7)
            axes[1, j].yaxis.set_major_locator(MaxNLocator(3))
        if j == 0:
            ax.set_ylabel("Intensity (a.u.)" if normalize else "Counts / s", fontsize=9)
    axes[0, 0].invert_xaxis()   # shared x: XPS convention, high BE on the left
    axes[-1, n // 2].set_xlabel("Binding energy (eV)", fontsize=9)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    for a in axes[0, 1:]:
        for hh, ll in zip(*a.get_legend_handles_labels()):
            if ll not in labels:
                handles.append(hh)
                labels.append(ll)
    fig.legend(handles, labels, loc="upper center", ncol=min(len(labels), 8), fontsize=8, frameon=False)
    fig.suptitle(region, x=0.01, ha="left", fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    if save_to:
        Path(save_to).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_to, bbox_inches="tight")
    return fig
