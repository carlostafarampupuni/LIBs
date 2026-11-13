"""
Metrohm NOVA data (.nox and NOVA Excel exports) -> the raw-file layouts the
existing importers already read. Nothing in analysis/ changes: this writes
files, then eis/cv/rate import them exactly like any other batch.

Input layout (your Data/ folder as it is — nothing is moved or renamed):

    Data/<batch>/<sample>/<YYYY.MM.DD>/[CELL n/]<file>.xlsx | .nox
         Batch3  0%_SiO2   2026.09.17   CELL 4   2. SiO2 0% analisis (4).xlsx

    batch  = the top folder (Batch1, Batch2, ...) — the batch key in every YAML
    sample = the folder inside it, exactly as named in config.yaml's `samples:`
    cell   = the "CELL n" folder, else the last "(n)" in the file name

    A batch only holds the samples that were measured in it; a missing sample
    folder (or an empty one) simply isn't part of that batch.

    The older layout without batch folders, Data/<sample>/<YYYY.MM.DD>/...,
    still works: there the batch is "D" + the date (2026.09.17 -> D20260917).

    Files in ANY other place (a flat folder like Data/fresh FRA/, or raw
    .nox dropped into Data/EIS/Batch5/) are read from their file names:
        20261015 YY Superp(SiO2 5%) (2) TEABF4  Impedance.nox
        date 2026.10.15, sample 5%_SiO2 (matched to config.yaml), cell 2
    The batch is the folder's name, or what notebooks/ingest.yaml maps it to
    (batches: {fresh FRA: Batch5}). Unclear names are SKIPPED and reported.

Output (next to your raw folders, inside Data/):

    Data/EIS/<batch>/<sample><cell>/<sample><cell>_FRA.txt        freq, Re(Z), Im(Z)
    Data/Rate/<batch>/Rate-<sample>-<cell>_<NNN>_Capacity.csv     + _Cycle.csv  (rate capability)
    Data/GCD/<batch>/GCD-<sample>-<cell>_<NNN>_Capacity.csv       same pair, for gcd.yaml (mode: supercap)
    Data/CV/<batch>/CV_<range>.xlsx                                one sheet per cell
    new batches appended to eis.yaml, drt.yaml, cv.yaml, rate.yaml  (your existing files)
    Data/ingest_report.md                                          what was written, skipped, and why

Rules that decide what is genuine data
--------------------------------------
- .nox beats .xlsx for the same (cell, technique, setting): it carries the
  frequencies, scan rates and currents an Excel export throws away (and the
  0.5 A/g Excel export of 5%_SiO2 cell 4 is even missing its last cycle).
- Identical copies (the "... FRA (1).xlsx" side files) are read once.
- A plot copy whose data also appears under a DIFFERENT sample or cell is a
  leftover from an Excel template, not a measurement, and is dropped. This
  is real: one "CV 5-500" block sits in column A of 7 different cells'
  workbooks, with each cell's own data in column U.

What is assumed (all of it written to ingest_report.md)
-------------------------------------------------------
- Charge-discharge sheet/file numbers ("CHARGE-DISCHARGE 0.5", "0.5A-GB")
  are current densities in A/g. Checked against the .nox currents: 1.06,
  2.11, 4.22, 6.33 mA for 0.5/1/2/3 — exactly proportional (2.11 mg).
  Capacity = j[A/g] * t[s] / 3.6 -> mAh/g, so no electrode mass is needed.
- An Excel CV sheet has no scan rates: they come from a .nox CV with the
  same range (5-500 -> 5,20,50,100,200,500; 10-100 -> 10,20,40,60,80,100).
- An Excel FRA sheet has no frequencies: they come from a .nox EIS with the
  same number of points. Its order was verified point-for-point against
  the .nox of the same cell (5%_SiO2 cell 4, identical to 1e-13 ohm).

Run it:

    python -m battery_analysis.ingest            # from Results/
    # or, in a notebook:
    from battery_analysis.ingest import ingest
    ingest("..")
"""
from __future__ import annotations

import hashlib
import shutil
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

from .nova_xlsx import read_nova_xlsx
from .nox import CVScan, EISData, GCStep, NoxFormatError, read_nox

_DATE_RE = re.compile(r"(\d{4})[.\-_](\d{2})[.\-_](\d{2})")
_CELL_DIR_RE = re.compile(r"^cell\s*(\d+)$", re.I)
_CELL_FILE_RE = re.compile(r"\((\d+)\)")
_NOX_CURRENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*A(?:/g)?(?=[-_\s.]|$)", re.I)
_NAME_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")   # 20261015 in a file name
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_OUTPUT_DIRS = {"EIS", "CV", "Rate"}
_ALL_OUTPUT_DIRS = _OUTPUT_DIRS | {"GCD"}

# Only used when no .nox of that CV range exists anywhere under Data/.
# Both lists are what the 5%_SiO2 cell-4 .nox files record.
FALLBACK_CV_SCAN_RATES = {"5-500": [5, 20, 50, 100, 200, 500], "10-100": [10, 20, 40, 60, 80, 100]}

CV_CYCLE = "last"     # "last" = keep only the final (stabilised) scan of each rate; "all" = keep every scan


# ===========================================================================
# 1. One measurement, from whichever file it came out of
# ===========================================================================

@dataclass
class Measurement:
    sample: str
    batch: str
    cell: int
    technique: str                    # eis | cv | gcd
    setting: str                      # gcd: A/g as text; cv: "5-500"; eis: ""
    source: Path
    origin: str                       # "nox" | "xlsx"
    where: str                        # sheet / column, for the report
    eis: Optional[EISData] = None
    cv: List[CVScan] = field(default_factory=list)
    gcd: List[GCStep] = field(default_factory=list)
    date: str = ""                    # YYYY.MM.DD from the date folder or the file name, for the report

    @property
    def label(self) -> str:           # EIS folder rule: <sample><single replicate digit>
        return f"{self.sample}{self.cell}"

    @property
    def arrays(self) -> List[np.ndarray]:
        out = [self.eis.zre, self.eis.zim] if self.eis is not None else []
        for s in self.cv:
            out += [s.e_applied, s.current]
        for s in self.gcd:
            out += [s.time, s.potential]
        return out

    @property
    def digest(self) -> str:
        # float32: a .nox and its Excel export agree to ~1e-13, not bit for bit
        h = hashlib.sha1()
        for a in self.arrays:
            h.update(np.asarray(a, np.float32).tobytes())
        return h.hexdigest()

    def same_data(self, other: "Measurement") -> bool:
        a, b = self.arrays, other.arrays
        return len(a) == len(b) and all(x.shape == y.shape and np.allclose(x, y, rtol=1e-9, atol=1e-12)
                                        for x, y in zip(a, b))


def _cv_range(scans: List[CVScan]) -> str:
    rates = [s.scan_rate_mV_s for s in scans]
    return f"{min(rates):g}-{max(rates):g}"


def _read_file(path: Path, sample: str, batch: str, cell: int, notes: List[str]) -> List[Measurement]:
    rel = f"{batch}/{sample}/{path.parent.name}/{path.name}"
    if path.suffix.lower() == ".nox":
        try:
            nf = read_nox(path)
        except (NoxFormatError, OSError) as e:
            notes.append(f"SKIPPED `{rel}`: {e}")
            return []
        m = Measurement(sample, batch, cell, nf.technique, "", path, "nox", nf.procedure_text)
        if nf.technique == "eis":
            m.eis = nf.eis
        elif nf.technique == "cv":
            m.cv, m.setting = nf.cv, _cv_range(nf.cv)
        elif nf.technique == "gcd":
            hit = _NOX_CURRENT_RE.findall(path.stem)
            if not hit:
                notes.append(f"SKIPPED `{rel}`: charge-discharge .nox with no current density "
                             f"(e.g. '0.5A') in its file name")
                return []
            m.gcd, m.setting = nf.gcd, hit[-1]
        else:
            notes.append(f"SKIPPED `{rel}`: .nox holds '{nf.technique}' data, not one EIS/CV/GCD run")
            return []
        return [m]

    out = []
    try:
        groups = read_nova_xlsx(path)
    except Exception as e:                       # a corrupt workbook must not stop the batch
        notes.append(f"SKIPPED `{rel}`: could not read ({type(e).__name__}: {e})")
        return []
    for g in groups:
        if g.technique == "unknown":
            notes.append(f"`{rel}` sheet `{g.sheet}`: not recognised as EIS/CV/charge-discharge - ignored")
            continue
        col = f" (column {chr(65 + g.column)})" if g.column else ""
        out.append(Measurement(sample, batch, cell, g.technique, g.setting, path, "xlsx",
                               f"sheet `{g.sheet}`{col}", eis=g.eis, cv=g.cv, gcd=g.gcd))
    return out


# ===========================================================================
# 2. Walking Data/<sample>/<date>/[CELL n/]
# ===========================================================================

def _cell_of(path: Path, date_dir: Path) -> Optional[int]:
    for part in path.relative_to(date_dir).parts[:-1]:
        m = _CELL_DIR_RE.match(part.strip())
        if m:
            return int(m.group(1))
    hits = _CELL_FILE_RE.findall(path.stem)
    return int(hits[-1]) if hits else None


def _date_dirs(sample_dir: Path):
    return sorted(p for p in sample_dir.iterdir() if p.is_dir() and _DATE_RE.fullmatch(p.name))


def _subdirs(path: Path):
    return sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))


def collect(data_root: Path, notes: List[str], samples=(), batch_map=None) -> List[Measurement]:
    """
    Walk Data/<batch>/<sample>/<date>/... (or the older Data/<sample>/<date>/...),
    then read every file that layout did not reach from its file name — see
    _collect_loose(). `samples` (config.yaml's names) and `batch_map`
    (notebooks/ingest.yaml) are only used for those loose files.
    """
    found = []
    claimed = set()
    for top in (p for p in _subdirs(data_root) if p.name not in _OUTPUT_DIRS):
        if _date_dirs(top):                                   # old layout: top is a sample
            jobs = [(top, d, "D" + "".join(_DATE_RE.fullmatch(d.name).groups())) for d in _date_dirs(top)]
        else:                                                 # batch layout: top is a batch
            jobs = [(sample_dir, d, top.name) for sample_dir in _subdirs(top) for d in _date_dirs(sample_dir)]
        for sample_dir, date_dir, batch in jobs:
            files = sorted(f for f in date_dir.rglob("*")
                           if f.is_file() and f.suffix.lower() in (".xlsx", ".nox")
                           and not f.name.startswith(("~$", ".")))
            for f in files:
                claimed.add(f)
                cell = _cell_of(f, date_dir)
                if cell is None or not 1 <= cell <= 9:
                    notes.append(f"SKIPPED `{f.relative_to(data_root)}`: no cell number 1-9 "
                                 f"(expected a 'CELL n' folder or '(n)' in the file name)")
                    continue
                for m in _read_file(f, sample_dir.name, batch, cell, notes):
                    m.date = date_dir.name
                    found.append(m)
    found.extend(_collect_loose(data_root, claimed, notes, samples, batch_map or {}))
    return found


# ---------------------------------------------------------------------------
# Loose files: anything the folder layout above did not reach, e.g.
#     Data/fresh FRA/20261015 YY Superp(SiO2 5%) (2) TEABF4  Impedance.nox
#     Data/EIS/Batch5/<a raw .nox dropped straight into the output folder>
# The file name already says everything:  date 20261015, sample "SiO2 5%"
# (matched to config.yaml's 5%_SiO2), cell "(2)".
# Batch: the folder's name, or what notebooks/ingest.yaml maps it to:
#     batches:
#       fresh FRA: Batch5
# A file whose sample or cell can't be told from its name is SKIPPED and
# reported, never guessed.
# ---------------------------------------------------------------------------

def _sample_from_name(stem: str, samples) -> List[str]:
    """Every config.yaml sample the file name fits ('SiO2 5%' fits 5%_SiO2, not 2.5%_SiO2)."""
    flat = re.sub(r"[^a-z0-9]", "", stem.lower())
    pcts = {float(x) for x in _PCT_RE.findall(stem)}
    hits = []
    for s in samples:
        parts = [p for p in re.split(r"[_\s]+", s) if p]
        ok = True
        for p in parts:
            pm = _PCT_RE.fullmatch(p)
            if pm:
                ok &= float(pm.group(1)) in pcts            # numbers compared exactly: 5 != 2.5
            else:
                ok &= re.sub(r"[^a-z0-9]", "", p.lower()) in flat
        if ok and parts:
            hits.append(s)
    return hits


def _collect_loose(data_root: Path, claimed, notes, samples, batch_map) -> List[Measurement]:
    out = []
    for f in sorted(data_root.rglob("*")):
        if (not f.is_file() or f in claimed or f.suffix.lower() not in (".xlsx", ".nox")
                or f.name.startswith(("~$", ".")) or any(p.startswith(".") for p in f.parts)):
            continue
        rel = f.relative_to(data_root)
        if rel.parts[0] == "CV" and f.name.startswith("CV_"):
            continue                                            # ingest's own CV output
        if len(rel.parts) < 2:
            notes.append(f"SKIPPED `{rel}`: sits directly in Data/ — put it in a folder named after its batch")
            continue
        if rel.parts[0] in _ALL_OUTPUT_DIRS:
            if len(rel.parts) != 3:
                continue                                        # inside a converted cell folder
            folder = rel.parts[1]                               # Data/EIS/Batch5/<file>
        else:
            folder = rel.parts[0]                               # Data/fresh FRA/<file>
        batch = str(batch_map.get(folder, re.sub(r"\s+", "_", folder.strip())))
        hits = _sample_from_name(f.stem, samples)
        if len(hits) != 1:
            why = (f"fits several samples ({', '.join(hits)})" if hits
                   else f"no sample from config.yaml ({', '.join(samples) or 'none listed'}) in its name")
            notes.append(f"SKIPPED `{rel}`: {why}")
            continue
        cells = _CELL_FILE_RE.findall(f.stem)
        cell = int(cells[-1]) if cells else None
        if cell is None or not 1 <= cell <= 9:
            notes.append(f"SKIPPED `{rel}`: no cell number 1-9 ('(n)' in the file name)")
            continue
        d = _NAME_DATE_RE.search(f.stem)
        date = ".".join(d.groups()) if d else ""
        for m in _read_file(f, hits[0], batch, cell, notes):
            m.date = date
            out.append(m)
            notes.append(f"FROM NAME `{rel}` -> {batch} / {hits[0]} cell {cell}"
                         + (f" / {date}" if date else "") + f" ({m.technique})")
    return out


# ===========================================================================
# 3. Deciding which copy is the real one
# ===========================================================================

def select(found: List[Measurement], notes: List[str]) -> Dict[tuple, Measurement]:
    owners = defaultdict(set)
    for m in found:
        owners[m.digest].add((m.sample, m.cell))
    kept = []
    for m in found:
        if len(owners[m.digest]) > 1:
            others = ", ".join(f"{s} cell {c}" for s, c in sorted(owners[m.digest]) if (s, c) != (m.sample, m.cell))
            notes.append(f"DROPPED {m.sample} cell {m.cell} {m.technique} {m.setting} from `{m.source.name}` "
                         f"{m.where}: identical data also appears under {others} (leftover template copy)")
        else:
            kept.append(m)

    chosen: Dict[tuple, Measurement] = {}
    for m in sorted(kept, key=lambda m: (m.origin != "nox", m.source.name, m.where)):
        key = (m.batch, m.sample, m.cell, m.technique, m.setting)
        first = chosen.get(key)
        if first is None:
            chosen[key] = m
        elif not first.same_data(m):
            why = (f"{len(m.gcd)} vs {len(first.gcd)} steps" if m.technique == "gcd" and len(m.gcd) != len(first.gcd)
                   else "different values")
            notes.append(f"{m.batch} {m.label} {m.technique} {m.setting}: used `{first.source.name}` "
                         f"{first.where}; `{m.source.name}` {m.where} differs ({why}) - not used")

    # The same measurement used in two batches (e.g. a "fresh" folder that
    # re-sends an older file under a new date): still written, but flagged.
    by_digest = defaultdict(list)
    for m in chosen.values():
        by_digest[m.digest].append(m)
    for ms in by_digest.values():
        if len(ms) > 1:
            notes.append("SAME DATA in " + " and ".join(
                f"{m.batch} {m.label} {m.technique} {m.setting} (`{m.source.name}`"
                + (f", {m.date}" if m.date else "") + ")" for m in ms)
                + ": one measurement appears in more than one batch - check which is right")
    return chosen


def _learn_protocols(found: List[Measurement]):
    """EIS frequency grids and CV scan-rate lists, from whatever .nox files exist."""
    grids, rates = {}, dict(FALLBACK_CV_SCAN_RATES)
    for m in found:
        if m.origin != "nox":
            continue
        if m.technique == "eis":
            grids.setdefault(len(m.eis.freq), (m.eis.freq, m.source.name))
        elif m.technique == "cv":
            rates[m.setting] = [s.scan_rate_mV_s for s in m.cv]
    return grids, rates


# ===========================================================================
# 4. Writers - one per importer
# ===========================================================================

def write_eis(m: Measurement, out: Path, grids, notes) -> Optional[Path]:
    e = m.eis
    freq = e.freq
    if np.isnan(freq).all():
        grid = grids.get(len(e.zre))
        if grid is None:
            notes.append(f"{m.batch} {m.label} EIS: no frequencies in the Excel export and no .nox EIS with "
                         f"{len(e.zre)} points to take them from - not written")
            return None
        freq = grid[0]
    folder = out / "EIS" / m.batch / m.label
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{m.label}_FRA.txt"
    pd.DataFrame({"freq/Hz": freq, "Re(Z)/Ohm": e.zre, "Im(Z)/Ohm": e.zim}).to_csv(
        path, sep="\t", index=False, float_format="%.10g")
    return path


def _ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def write_rate(cell_runs: List[Measurement], out: Path, notes) -> Optional[dict]:
    """All current densities of one cell -> one Wonatech-style Capacity/Cycle pair."""
    runs = sorted(cell_runs, key=lambda m: float(m.setting))
    first = runs[0]
    rows, prof, steps = [], {}, []
    for m in runs:
        j = float(m.setting)                                 # A/g
        by_cycle = defaultdict(dict)
        for s in m.gcd:
            if s.kind in ("charge", "discharge"):
                by_cycle[s.cycle][s.kind] = s
        complete = [c for c in sorted(by_cycle) if len(by_cycle[c]) == 2]
        if len(complete) < len(by_cycle):
            notes.append(f"{m.batch} {m.label} {j:g} A/g: last cycle has no discharge - left out")
        start = len(rows)
        for c in complete:
            n = len(rows) + 1
            q = {k: j * (s.time - s.time[0]) / 3.6 for k, s in by_cycle[c].items()}   # mAh/g
            rows.append((n, q["discharge"][-1], q["charge"][-1], j))
            for mode, k in (("Ch", "charge"), ("Dis", "discharge")):
                prof[(f"{_ordinal(n)} {mode}", "V [V]")] = by_cycle[c][k].potential
                prof[(f"{_ordinal(n)} {mode}", "Q/M [mAh/g]")] = q[k]
        if len(rows) > start:
            steps.append((j, start, len(rows) - start))
        if m.origin == "nox" and m.gcd[1:]:
            i_ma = np.nanmedian([abs(s.current_A) for s in m.gcd if s.kind != "rest"]) * 1e3
            notes.append(f"{m.batch} {m.label} {j:g} A/g: measured |I| = {i_ma:.3f} mA "
                         f"-> implied active mass {i_ma / j:.3f} mg")
    if not rows:
        return None

    folder = out / "Rate" / first.batch
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"Rate-{first.sample}-{first.cell}_{first.cell:03d}"
    cap = folder / f"{stem}_Capacity.csv"
    # "Current [A/g]" lets plot_rate_gcd() group cycles by current density; the Rate/Cycle readers ignore it
    pd.DataFrame(rows, columns=["Cycle", "Q_Dis/M [mAh/g]", "Q_Ch/M [mAh/g]", "Current [A/g]"]).to_csv(cap, index=False)
    width = max(len(v) for v in prof.values())
    padded = {k: np.pad(np.asarray(v, float), (0, width - len(v)), constant_values=np.nan) for k, v in prof.items()}
    pd.DataFrame(padded, columns=pd.MultiIndex.from_tuples(list(prof))).to_csv(
        folder / f"{stem}_Cycle.csv", index=False)
    # the same pair for the GCD technique (gcd.yaml, mode: supercap), kept in its own folder
    gcd_dir = out / "GCD" / first.batch
    gcd_dir.mkdir(parents=True, exist_ok=True)
    for src in (cap, folder / f"{stem}_Cycle.csv"):
        shutil.copyfile(src, gcd_dir / src.name.replace("Rate-", "GCD-", 1))
    return {"file": cap, "steps": steps}


def _cv_rates_ok(m: Measurement, rates_for, notes) -> Optional[List[float]]:
    if m.origin == "nox":
        return [s.scan_rate_mV_s for s in m.cv]
    rates = rates_for.get(m.setting)
    if not rates:
        notes.append(f"{m.batch} {m.label} CV {m.setting}: scan rates unknown for this range - not written")
        return None
    if len(rates) != len(m.cv):
        notes.append(f"{m.batch} {m.label} CV {m.setting}: {len(m.cv)} traces but {len(rates)} scan rates "
                     f"({rates}) - not written")
        return None
    return rates


def write_cv(items: List[Measurement], out: Path, batch: str, rng: str, rates_for, notes) -> Optional[dict]:
    folder = out / "CV" / batch
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"CV_{rng}.xlsx"
    cells = []
    with pd.ExcelWriter(path) as xw:
        for m in sorted(items, key=lambda m: (m.sample, m.cell)):
            rates = _cv_rates_ok(m, rates_for, notes)
            if rates is None:
                continue
            mean_i = [float(np.mean(np.abs(s.current))) for s in m.cv]
            if any(b < a for a, b in zip(mean_i, mean_i[1:])):
                notes.append(f"{batch} {m.label} CV {rng}: mean |i| does not rise with scan rate "
                             f"({', '.join(f'{x * 1e3:.2f}' for x in mean_i)} mA for {rates} mV/s) - "
                             f"check the trace order in `{m.source.name}` {m.where}")
            cols = {}
            for k, (s, r) in enumerate(zip(m.cv, rates), start=1):
                keep = s.cycle == s.cycle.max() if CV_CYCLE == "last" else np.ones(len(s.current), bool)
                cols[f"V{k}"] = pd.Series(s.e_applied[keep])
                cols[float(r)] = pd.Series(s.current[keep])
            sheet = f"{m.sample} c{m.cell}"[:31]
            pd.DataFrame(cols).to_excel(xw, sheet_name=sheet, index=False)
            cells.append({"sample": m.sample, "sheet": sheet, "voltage_prefix": "V",
                          "scan_rates": [float(r) for r in rates]})
    if not cells:
        path.unlink(missing_ok=True)
        return None
    return {"excel_path": f"CV/{batch}/CV_{rng}.xlsx", "cells": cells}


# ===========================================================================
# 5. Technique YAMLs - written once, then only ever appended to
# ===========================================================================

_HEADER = "# Written by battery_analysis.ingest. Edit freely: re-running ingest only ADDS\n" \
          "# batches that are missing here, it never changes or removes what you have.\n\n"

# Nyquist plot window for every new EIS batch: square, so the "small" figure
# keeps its shape (equal-aspect axes shrink to fit unequal limits otherwise).
EIS_XLIM, EIS_YLIM = [0, 200], [-200, 0]


def _pct(name: str) -> float:
    m = re.match(r"([\d.]+)", name)
    return float(m.group(1)) if m else float("inf")


def _eis_batch(batch: str, samples=()) -> dict:
    """Same shape as your hand-written eis.yaml batches: one FRA spectrum per cell."""
    return {"_comment": [f"{batch}: one FRA spectrum per cell (from battery_analysis.ingest).",
                         "Hide cells/samples with exclude: [0%_SiO24] (one cell) or [0%_SiO2] (whole sample);",
                         "choose a sample's plotted cell with sample_overrides: [{name: 0%_SiO2, plot: [0%_SiO22]}],",
                         "or let representative: pick it (best_fit | lowest_Rct | first)."],
            "exclude": [],
            "samples": sorted(samples, key=_pct),
            "representative": "best_fit",
            "data_subfolder": f"EIS/{batch}",
            "cycle_mapping": {}, "filename_mapping": {},
            "sheet_names": {"1": "FRA"}, "sheet_order": ["1"],
            "sheets": [{"sheet": "FRA", "xlim": EIS_XLIM, "ylim": EIS_YLIM}],
            # automatic equivalent-circuit fit (analysis/eis/fitting.py): guesses, inductance
            # and the circuit itself are chosen from the data
            "fitting": {"use_model": True, "circuit_key": "auto", "extract_at_freq": False},
            "plotting": {"figsize": "small", "legend_loc": "best",
                         "axis_defaults": {"xlim": None, "ylim": None}}}


def _drt_batch(batch: str) -> dict:
    """DRT rides on the EIS batch of the same name (run_drt(batch=...))."""
    return {"_comment": [f"{batch}: DRT of the FRA spectra imported under eis.yaml's {batch}."],
            "plotting": {"figsize": "medium", "legend_loc": "best",
                         "sheets": [{"sheet": "FRA", "save": f"FRA_comparison_{batch}.svg"}]}}


def _append_batches(path: Path, batches: Dict[str, dict], header: str, default_batch: Optional[str]) -> List[str]:
    """Write path if it does not exist; otherwise append only the batch keys it lacks."""
    if not batches:
        return []
    if not path.exists():
        text = header + (f"default_batch: {default_batch}\n\n" if default_batch else "") + "batches:\n"
        path.write_text(text + _batches_text(batches))
        return list(batches)
    text = path.read_text()
    have = (yaml.safe_load(text) or {}).get("batches") or {}
    # a batch you commented out (#  D20260917:) is switched off on purpose — never re-add it
    switched_off = {k for k in batches if re.search(rf"(?m)^#\s*{re.escape(k)}:", text)}
    missing = {k: v for k, v in batches.items() if k not in have and k not in switched_off}
    if missing:
        with open(path, "a") as fh:
            fh.write("\n" + _batches_text(missing))
    return list(missing)


def _batches_text(batches: Dict[str, dict]) -> str:
    out = []
    for key, body in batches.items():
        comment = body.pop("_comment", None)
        samples = body.pop("samples", None)
        block = yaml.safe_dump({key: body}, sort_keys=False, default_flow_style=None, width=110)
        lines = [f"  # {c}" for c in (comment or [])] + ["  " + ln for ln in block.splitlines()]
        if samples:                       # one per line, so a sample can be commented out
            lines += ["    # Samples drawn — comment a line out (#) to hide it.", "    samples:"]
            lines += [f"      - {name}" for name in samples]
        out.append("\n".join(lines) + "\n")
    return "\n".join(out)


def _gcd_batch(batch: str, samples=()) -> dict:
    """gcd.yaml batch for supercapacitor charge-discharge (analysis/gcd/supercap.py)."""
    return {"_comment": [f"{batch}: supercapacitor GCD — by composition and by current density (2nd cycle at each current)."],
            "data_subfolder": f"GCD/{batch}", "mode": "supercap", "gcd_cycle": 2,
            "plot": {"figsize": "medium", "legend_loc": "best"},
            # several cells of one sample: plot the best one (or pin one with sample_overrides: plot: [<sample><cell>])
            "representative": "best",
            "samples": sorted(samples, key=_pct)}


def _rate_batch(batch: str, written: List[dict], notes, samples=()) -> dict:
    layouts = Counter(tuple(n for _, _, n in w["steps"]) for w in written)
    layout, count = layouts.most_common(1)[0]
    lines = [float(x) for x in np.cumsum(layout)[:-1]]
    rates = [j for j, _, _ in written[0]["steps"]]
    comment = [f"{batch}: rate capability, " + ", ".join(f"{n} cycles at {j:g} A/g" for (j, _, _), n
                                                         in zip(written[0]["steps"], layout)) + "."]
    if count < len(written):
        comment.append(f"c_rate_lines fit {count} of {len(written)} cells. Cycles per step elsewhere:")
        comment += [f"  {w['file'].name}: " + ", ".join(f"{n} at {j:g} A/g" for j, _, n in w["steps"])
                    for w in written if tuple(n for _, _, n in w["steps"]) != layout]
        notes.append(f"Rate {batch}: cells do not all have the same cycles per current density; "
                     f"c_rate_lines follow the majority ({layout})")
    firsts = [int(x) + 1 for x in [0, *lines]]
    return {"_comment": comment, "data_subfolder": f"Rate/{batch}", "electrode_type": "cathode",
            "c_rate_lines": lines,
            "profiles": {"formation_cycles": [], "custom_cycles": firsts, "modes": ["charge", "discharge"]},
            "rate_capability": {"interval": int(layout[0]) if len(set(layout)) == 1 else None,
                                "modes": ["discharge"]},
            "capacity_plot": {"figsize": "medium", "legend_loc": "best"},
            "ce_plot": {"figsize": "medium", "legend_loc": "best"},
            # several cells of one sample: plot the best one (or pin one with sample_overrides: plot: [<sample><cell>])
            "representative": "best",
            "samples": sorted(samples, key=_pct)}


# ===========================================================================
# 6. Driver
# ===========================================================================

def ingest(project_dir=".", data_root: Optional[str] = None) -> Path:
    """Convert every NOVA file under Data/ and return the path of the report."""
    project_dir = Path(project_dir).resolve()
    # config.yaml and the technique YAMLs sit in notebooks/ (older layout: project root)
    yaml_dir = project_dir / "notebooks" if (project_dir / "notebooks" / "config.yaml").exists() else project_dir
    if data_root is None:
        cfg = yaml.safe_load((yaml_dir / "config.yaml").read_text()) or {}
        data_root = (cfg.get("paths") or {}).get("data_root", "Data")
    data = (project_dir / data_root).resolve()
    known = {s.get("name") for s in (yaml.safe_load((yaml_dir / "config.yaml").read_text()) or {})
             .get("samples") or []} if (yaml_dir / "config.yaml").exists() else set()
    # notebooks/ingest.yaml (optional): loose folder name -> batch, e.g. {fresh FRA: Batch5}
    map_file = yaml_dir / "ingest.yaml"
    batch_map = ((yaml.safe_load(map_file.read_text()) or {}).get("batches") or {}) if map_file.exists() else {}

    notes: List[str] = []
    found = collect(data, notes, sorted(known, key=_pct), batch_map)
    grids, rates_for = _learn_protocols(found)
    chosen = select(found, notes)

    written = defaultdict(list)                      # technique -> [(batch, label, path)]
    gcd_by_cell, cv_by_batch = defaultdict(list), defaultdict(list)
    for (batch, sample, cell, tech, setting), m in sorted(chosen.items()):
        if tech == "eis":
            p = write_eis(m, data, grids, notes)
            if p:
                written["eis"].append((batch, m.label, p))
        elif tech == "gcd":
            gcd_by_cell[(batch, sample, cell)].append(m)
        elif tech == "cv":
            cv_by_batch[(batch, setting)].append(m)

    rate_batches = defaultdict(list)
    for (batch, sample, cell), runs in sorted(gcd_by_cell.items()):
        w = write_rate(runs, data, notes)
        if w:
            rate_batches[batch].append(w)
            written["rate"].append((batch, f"{sample}{cell}", w["file"]))

    cv_batches = {}
    for (batch, rng), items in sorted(cv_by_batch.items()):
        c = write_cv(items, data, batch, rng, rates_for, notes)
        if c:
            cv_batches[f"{batch}_CV_{rng}"] = {
                **c, "scan_rate_unit": "mV/s",
                # active_mass_g: {sample: grams} turns current into current density (A/g)
                "active_mass_g": {},
                "plot": {"figsize": "medium", "xlabel": "Voltage (V)", "ylabel": "Current (A)", "legend_loc": "upper left"},
                "scan_rate_plot": {"figsize": "medium", "legend_loc": "upper left"},
                "samples": sorted({x["sample"] for x in c["cells"]}, key=_pct)}
            written["cv"] += [(batch, x["sheet"], data / c["excel_path"]) for x in c["cells"]]

    eis_batches = sorted({b for b, _, _ in written["eis"]})
    in_batch = defaultdict(set)                      # (technique, batch) -> samples
    for (batch, sample, _, tech, _) in chosen:
        in_batch[(tech, batch)].add(sample)
    added = {
        "eis.yaml": _append_batches(yaml_dir / "eis.yaml", {b: _eis_batch(b, in_batch[("eis", b)]) for b in eis_batches}, _HEADER, None),
        "drt.yaml": _append_batches(yaml_dir / "drt.yaml", {b: _drt_batch(b) for b in eis_batches}, _HEADER, None),
        "cv.yaml": _append_batches(yaml_dir / "cv.yaml", cv_batches, _HEADER, next(iter(cv_batches), None)),
        "gcd.yaml": _append_batches(yaml_dir / "gcd.yaml",
                                    {b: _gcd_batch(b, in_batch[("gcd", b)]) for b in sorted(rate_batches)},
                                    _HEADER, next(iter(sorted(rate_batches)), None)),
        "rate.yaml": _append_batches(yaml_dir / "rate.yaml",
                                     {b: _rate_batch(b, ws, notes, in_batch[("gcd", b)]) for b, ws in sorted(rate_batches.items())},
                                     _HEADER, next(iter(sorted(rate_batches)), None)),
    }

    samples = sorted({m.sample for m in found})
    unknown = [s for s in samples if known and s not in known]
    if unknown:
        notes.append(f"samples not listed in config.yaml (they will get automatic colours): {unknown}")

    grid_txt = "; ".join(f"{n} points from `{src}`" for n, (_, src) in grids.items()) or "none found"
    lines = [
        "# NOVA ingest report", "",
        f"- files read: {len({m.source for m in found})} ({sum(m.origin == 'nox' for m in found)} .nox measurements, "
        f"{sum(m.origin == 'xlsx' for m in found)} Excel plot copies)",
        f"- samples: {', '.join(samples)}",
        f"- EIS frequency grid for Excel FRA sheets: {grid_txt}",
        f"- CV scan rates for Excel sheets: {rates_for}",
        f"- CV points kept per scan rate: {CV_CYCLE} scan",
        "- YAML batches added: " + "; ".join(f"{f} {v or 'none'}" for f, v in added.items()), "",
        "## Written", "",
        "| technique | batch | cell | file |", "|---|---|---|---|",
        *[f"| {t} | {b} | {lab} | `{Path(p).relative_to(data)}` |" for t in ("eis", "cv", "rate")
          for b, lab, p in written[t]],
        "", "## Notes, skips and warnings", "", *[f"- {n}" for n in notes],
    ]
    report = data / "ingest_report.md"
    report.write_text("\n".join(lines) + "\n")
    print(f"✅  NOVA ingest: {len(written['eis'])} EIS, {len(written['cv'])} CV, {len(written['rate'])} rate "
          f"file(s) written under {data}")
    print(f"   {len(notes)} note(s) — read {report}")
    # the notes that need a human, printed here so they are never missed
    for prefix in ("SKIPPED", "SAME DATA", "DROPPED"):
        hits = [n for n in notes if n.startswith(prefix)]
        for n in hits[:12]:
            print(f"   ⚠️  {n}")
        if len(hits) > 12:
            print(f"   ⚠️  ... and {len(hits) - 12} more {prefix} — see the report")
    return report
