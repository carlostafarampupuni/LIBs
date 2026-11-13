"""
One entry point for XPS data, whatever form it arrives in:

    results = sources.collect(cfg)            # -> list of FitOutcome

`settings.source` in the YAML may be a file, a folder, or a list of both.
Every file is classified and handled automatically:

  Avantage workbook (.xls/.xlsx with "<Region> Scan" sheets)
        -> raw, several samples per sheet          -> FIT  (fitting.fit_workbook)
  single spectrum, ≥ 4 columns (BE, raw, sum, background, peak 1, ...)
        -> already fitted elsewhere (XPSPeak,      -> PLOT AS-IS (no refit)
           CasaXPS/Kherve exported in that layout,
           or this code's own export_dat)
  single spectrum, 2 columns (BE, counts)
        -> raw                                      -> FIT

`settings.mode` overrides the automatic choice:
  auto  (default)  as above
  plot             only show already-fitted files; raw files are skipped
  fit              fit everything — already-fitted files are REFITTED from
                   their raw-intensity column with this project's model

Single-spectrum filenames give sample + region, e.g. "BareF1s.dat",
"MXCuP2p.xlsx", "PA800_Cycle_O1s.txt", "BareP2P.dat.txt" (case-insensitive
region, optional condition word). Already-fitted peaks are named and
coloured by matching their position to the YAML region model, so imported
fits and your own fits produce identical-looking figures.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import fitting as xf

REGIONS = ["Mn2p", "Co2p", "Ni2p", "Cu2p", "Ti2p", "Si2p", "Al2p", "Cl2p", "P2p", "S2p",
           "C1s", "O1s", "N1s", "F1s", "Li1s", "Na1s", "B1s"]
CONDITIONS = {"pristine": "Pristine", "prisitine": "Pristine", "cycled": "Cycle", "cycle": "Cycle",
              "fresh": "Pristine", "aftercycling": "Cycle"}
SPECTRUM_EXT = (".dat", ".txt", ".csv", ".xlsx", ".xls")


# ──────────────────────────────────────────────────────────────────────
# Filenames
# ──────────────────────────────────────────────────────────────────────
def parse_name(path) -> Optional[tuple]:
    """'BareP2P.dat.txt' -> ('Bare', 'P2p', None); 'PA800_Cycle_O1s' -> ('PA800', 'O1s', 'Cycle')."""
    stem = Path(path).name
    while Path(stem).suffix.lower() in SPECTRUM_EXT:
        stem = stem[: -len(Path(stem).suffix)]
    low = stem.lower()
    region = next((r for r in REGIONS if low.endswith(r.lower())), None)
    if region is None and low.endswith("cl1s"):       # common naming slip: the Cl line measured is Cl 2p
        region, cut = "Cl2p", 4
    elif region is None:
        return None
    else:
        cut = len(region)
    rest = stem[: -cut]
    condition = None
    for alias in sorted(CONDITIONS, key=len, reverse=True):
        i = rest.lower().find(alias)
        if i != -1:
            condition = CONDITIONS[alias]
            rest = rest[:i] + rest[i + len(alias):]
            break
    sample = rest.strip(" _-.")
    return (sample, region, condition) if sample else None


# ──────────────────────────────────────────────────────────────────────
# Reading single-spectrum files
# ──────────────────────────────────────────────────────────────────────
def _read_text_table(path: Path) -> pd.DataFrame:
    """
    Plain-text spectrum, any common layout:
      * tab / comma / semicolon separated (Excel, Origin, this code's exports)
      * XPSPeak .dat: columns aligned with runs of spaces, header names that
        contain single spaces ("Raw Intensity", "Peak 1")
    Returns a DataFrame with a header row (generated names when there is none).
    """
    lines = [l.rstrip("\r\n") for l in path.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    if not lines:
        raise ValueError(f"{path.name}: empty file")

    def is_number(tok):
        try:
            float(tok); return True
        except ValueError:
            return False

    first = lines[0].strip()
    for sep in ("\t", ",", ";"):
        if sep in first:
            head = [h.strip() for h in first.split(sep)]
            rows = [l.split(sep) for l in lines[1:]] if not all(is_number(h) for h in head if h) else \
                   [l.split(sep) for l in lines]
            if all(is_number(h) for h in head if h):
                head = [f"col{i}" for i in range(len(head))]
            break
    else:                                      # whitespace-aligned (XPSPeak .dat and similar)
        toks = first.split()
        if all(is_number(t) for t in toks):
            head = [f"col{i}" for i in range(len(toks))]
            rows = [l.split() for l in lines]
        else:
            head = [h.strip() for h in re.split(r"\s{2,}", first) if h.strip()]
            rows = [l.split() for l in lines[1:]]
    n = max(len(r) for r in rows)
    if len(head) < n:
        head += [f"col{i}" for i in range(len(head), n)]
    body = pd.DataFrame([r + [None] * (n - len(r)) for r in rows], columns=head[:n])
    return body


def read_table(path) -> pd.DataFrame:
    """Numeric table with column names (generated if the file has no header)."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path, header=None)
        first_numeric = pd.to_numeric(raw.iloc[0], errors="coerce").notna().all()
        if first_numeric:
            names, body = [f"col{i}" for i in range(raw.shape[1])], raw
        else:
            names, body = [str(v).strip() for v in raw.iloc[0]], raw.iloc[1:]
        body = body.copy()
        body.columns = names
    else:
        body = _read_text_table(path)
    body = body.apply(pd.to_numeric, errors="coerce").dropna(how="all", axis=1).dropna()
    return body.reset_index(drop=True)


def _is_residual(name: str, col: np.ndarray, bg: np.ndarray) -> bool:
    if re.search(r"diff|resid", name, re.I):
        return True
    # a residual (raw − fit) swings around zero, with real negative values;
    # peak columns are ≥ 0 (with or without the background added)
    swings_negative = np.min(col) < -0.1 * np.max(np.abs(col))
    near_zero = np.median(np.abs(col)) < 0.25 * max(np.median(np.abs(bg)), 1e-12)
    return bool(swings_negative and near_zero)


def _match_names(centers: List[float], model: Optional[xf.RegionModel], region: str,
                 header_names: List[str]) -> List[tuple]:
    """
    (name, color) for each imported peak. Peaks are matched to the YAML
    components all at once (optimal assignment on distance to each window),
    so one peak can't steal another's name; unmatched peaks fall back to the
    file's own header name, then to the analyzer's BE-window assignment.
    """
    from scipy.optimize import linear_sum_assignment
    from .analyzer import get_peak_assignment

    comps = model.components if model else []
    assigned = {}
    if comps:
        cost = np.full((len(centers), len(comps)), 1e6)
        for i, be in enumerate(centers):
            for j, c in enumerate(comps):
                lo, hi = c.window
                d = 0.0 if lo - 0.15 <= be <= hi + 0.15 else min(abs(be - lo), abs(be - hi))
                if d <= 0.3:
                    cost[i, j] = d * 10 + abs(be - c.center)
        rows, cols = linear_sum_assignment(cost)
        for i, j in zip(rows, cols):
            if cost[i, j] < 1e6:
                assigned[i] = (comps[j].name, comps[j].color)

    out = []
    for k, be in enumerate(centers):
        if k in assigned:
            out.append(assigned[k])
            continue
        h = header_names[k] if k < len(header_names) else ""
        if h and not re.fullmatch(r"(peak|col)\s*\d+", h, re.I):
            out.append((h, None))
        else:
            label = re.sub(r"^([A-Z][a-z]?)(\d.*)$", r"\1 \2", region)
            a = get_peak_assignment(label, be)
            # no YAML component or known assignment fits: name it by its position, neutrally
            out.append((f"{be:.1f} eV" if a in ("unknown", "not recognized") else a, None))
    return out


def outcome_from_fitted(table: pd.DataFrame, sample: str, region: str,
                        model: Optional[xf.RegionModel]) -> xf.FitOutcome:
    """An already-fitted export (BE, raw, sum, bg, peaks...) -> FitOutcome, without refitting."""
    cols = list(table.columns)
    arr = table.to_numpy(float)
    o = np.argsort(arr[:, 0])
    arr = arr[o]
    x, y, total, bg = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    peak_cols = [(cols[j], arr[:, j]) for j in range(4, arr.shape[1])
                 if not _is_residual(cols[j], arr[:, j], bg)]
    # XPSPeak-style columns are peak + background; some exports give the bare peak
    curves = [np.clip(c - bg if np.median(c) > 0.5 * np.median(bg) else c, 0, None) for _, c in peak_cols]
    # drop empty components (≈ 0 area: only rounding noise left, position meaningless)
    areas0 = [abs(float(xf._trapz(c, x))) for c in curves]
    keep_k = [k for k, a in enumerate(areas0) if a > 1e-3 * max(sum(areas0), 1e-12)]
    peak_cols = [peak_cols[k] for k in keep_k]
    curves = [curves[k] for k in keep_k]
    centers = [float(x[np.argmax(c)]) for c in curves]
    named = _match_names(centers, model, region, [n for n, _ in peak_cols])

    areas = [abs(float(xf._trapz(c, x))) for c in curves]
    tot = sum(areas) or 1.0
    rows, comps, specs = [], {}, []
    for (name, color), c, be, a in zip(named, curves, centers, areas):
        half = c >= c.max() / 2
        fwhm = float(x[half].max() - x[half].min()) if half.any() else np.nan
        rows.append({"sample": sample, "region": region, "component": name, "BE (eV)": round(be, 2),
                     "FWHM (eV)": round(fwhm, 2), "shape": "imported", "area": a, "area %": 100 * a / tot,
                     "doublet": "", "at BE limit": False})
        comps[name] = c
        specs.append(xf.ComponentSpec(name=name, center=be, window=(be, be), color=color))
    env = total - bg
    ss_res = float(np.sum((y - total) ** 2))
    ss_tot = float(np.sum((y - bg - (y - bg).mean()) ** 2))
    m = xf.RegionModel(region=region, components=specs,
                       background=model.background if model else "imported")
    return xf.FitOutcome(sample=sample, region=region, status="fitted", shift=0.0, x=x, y=y,
                         background=bg, envelope=env, components=comps, table=pd.DataFrame(rows),
                         snr=xf.signal_to_noise(y, bg), r2=1 - ss_res / ss_tot if ss_tot else np.nan,
                         starts="imported", model=m)


# ──────────────────────────────────────────────────────────────────────
# Collect everything
# ──────────────────────────────────────────────────────────────────────
def _expand(source, base: Path) -> List[Path]:
    if source is None:
        return []
    items = source if isinstance(source, (list, tuple)) else [source]
    out = []
    for s in items:
        p = Path(s)
        p = p if p.is_absolute() else (base / p)
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SPECTRUM_EXT
                          and not f.name.startswith(("~$", ".")) and "tmp" not in f.stem.lower())
        elif p.exists():
            out.append(p)
        else:
            print(f"⚠️  source not found (no data for this batch yet?): {p}")
    return out


def is_avantage_workbook(path) -> bool:
    if Path(path).suffix.lower() not in (".xls", ".xlsx"):
        return False
    try:
        return any(s.lower().endswith(" scan") for s in pd.ExcelFile(path).sheet_names)
    except Exception:  # noqa: BLE001
        return False


def source_paths(fit_cfg: dict) -> List[Path]:
    base = Path(fit_cfg.get("path", ".")).parent
    return _expand(fit_cfg["settings"].get("source"), base)


def survey_workbook(fit_cfg: dict, region: Optional[str] = None) -> Optional[Path]:
    """First source workbook that has a survey sheet (or settings.survey_source).
    With region="F1s": the first workbook that has that region's scan instead."""
    base = Path(fit_cfg.get("path", ".")).parent
    explicit = None if region else fit_cfg["settings"].get("survey_source")
    cands = _expand(explicit, base) if explicit else source_paths(fit_cfg)
    for p in cands:
        if p.suffix.lower() in (".xls", ".xlsx"):
            try:
                names = [s.lower() for s in pd.ExcelFile(p).sheet_names]
                want = f"{region.lower()} scan" if region else "survey"
                if any(want in s for s in names):
                    return p
            except Exception:  # noqa: BLE001
                pass
    return None


def collect(fit_cfg: dict, samples: Optional[Sequence[str]] = None,
            regions: Optional[Sequence[str]] = None, verbose: bool = True) -> List[xf.FitOutcome]:
    """Load/fit every source in the YAML. See module docstring for how files are treated."""
    st = fit_cfg["settings"]
    mode = str(st.get("mode", "auto")).lower()
    models: Dict[str, xf.RegionModel] = fit_cfg["regions"]
    n_starts = int(st.get("n_starts", 10))
    samples = samples if samples is not None else fit_cfg.get("samples")      # batch `samples:` list
    renames = {str(k): str(v) for k, v in (st.get("sample_names") or {}).items()}   # e.g. workbook position "5" -> "BareLMP"
    rename = lambda o: setattr(o, "sample", renames.get(o.sample, o.sample)) or o  # noqa: E731
    wanted = {s.lower() for s in samples} if samples else None
    keep = lambda s, r: (wanted is None or s.lower() in wanted) and (regions is None or r in regions)  # noqa: E731

    out: List[xf.FitOutcome] = []
    raw_singles = []          # (sample, region, x, y, path) to fit after charge shifts are known
    # single-spectrum files (your fits) first, workbooks after: when both hold the same
    # sample/region, the file you fitted yourself wins over the automatic fit
    paths = source_paths(fit_cfg)
    paths = [p for p in paths if not is_avantage_workbook(p)] + [p for p in paths if is_avantage_workbook(p)]
    for path in paths:
        if is_avantage_workbook(path):
            if mode == "plot":
                if verbose:
                    print(f"  skip (mode=plot, raw workbook): {path.name}")
                continue
            if verbose:
                print(f"  FIT   {path.name} (Avantage workbook)")
            fitted = [rename(o) for o in xf.fit_workbook(path, fit_cfg, regions=regions, verbose=False)]
            for o in fitted:
                if not o.table.empty:
                    o.table["sample"] = o.sample
            out += [o for o in fitted if keep(o.sample, o.region)]
            continue
        parsed = parse_name(path)
        if parsed is None:
            if verbose:
                print(f"  skip (no sample/region in name): {path.name}")
            continue
        sample, region, condition = parsed
        sample = renames.get(sample, sample)
        if not keep(sample, region):
            continue
        try:
            tab = read_table(path)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  could not read {path.name}: {exc}")
            continue
        fitted_file = tab.shape[1] >= 5
        if fitted_file and mode != "fit":
            out.append(outcome_from_fitted(tab, sample, region, models.get(region)))
            if verbose:
                print(f"  PLOT  {path.name}  -> {sample} {region} (already fitted, {tab.shape[1] - 4} peak cols)")
        elif mode == "plot":
            if verbose:
                print(f"  skip (mode=plot, raw spectrum): {path.name}")
        else:
            raw_singles.append((sample, region, tab.iloc[:, 0].to_numpy(float),
                                tab.iloc[:, 1].to_numpy(float), path))

    # raw single spectra: charge shift per sample from its own C1s file, then fit
    if raw_singles:
        data = {}
        for s, r, x, y, _ in raw_singles:
            data.setdefault(r, {})[s] = (x, y)
        shifts = {}
        cc = st.get("charge_correction", {}) or {}
        ref = cc.get("region", "C1s")
        for s in {s for s, *_ in raw_singles}:
            if cc.get("fixed") is not None:
                shifts[s] = float(cc["fixed"])
            elif cc.get("enabled", True) and s in data.get(ref, {}):
                shifts[s] = xf.charge_shift(*data[ref][s], reference=float(cc.get("reference", 284.8)),
                                            search=tuple(cc.get("search", (282.0, 286.5))))
            else:
                shifts[s] = 0.0
        for s, r, x, y, path in raw_singles:
            o = xf.fit_spectrum(x, y, models.get(r), s, r, shift=shifts[s], n_starts=n_starts)
            out.append(o)
            if verbose:
                print(f"  FIT   {path.name}  -> {s} {r} (raw, shift {shifts[s]:+.2f} eV): {o.status}")

    # same sample spelled differently in different files (MXCu vs MxCu): use the first spelling seen
    canon = {x.lower(): x for x in (samples or [])}
    for o in out:
        canon.setdefault(o.sample.lower(), o.sample)
    for o in out:
        if canon[o.sample.lower()] != o.sample:
            o.sample = canon[o.sample.lower()]
            o.table["sample"] = o.sample

    # one result per (sample, region): first source wins
    seen, unique = set(), []
    for o in out:
        if (o.sample, o.region) in seen:
            if verbose:
                print(f"  duplicate {o.sample} {o.region} ignored (an earlier source already provided it)")
            continue
        seen.add((o.sample, o.region))
        unique.append(o)
    return unique
