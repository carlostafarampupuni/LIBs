"""
Reader for NOVA's "Export to Excel" workbooks (the "SiO2 0% analisis (1).xlsx"
kind): one sheet per measurement, each sheet a stack of plot traces.

    row 0      Title: | <title>
    row 1      Label: | E | vs | t | (MAC90031#1)      <- starts trace 1
    rows ...   x | y
    row k      Label: | ...                              <- starts trace 2
    ...

What a trace is depends on the plot the sheet was exported from, read from
the Label row (sheet names are typed by hand and drift: "CHARGE-DISCHARGE
0.5", "CD 0.5A", "charge-discharge 0.5" all mean the same thing):

    "E vs t"          charge-discharge   one trace per step: rest, charge, discharge, ...
    "i vs E"          CV                 one trace per scan rate (2 scans each)
    "Nyquist -Z''"    EIS                one trace: Z' (x), -Z'' (y)

A sheet can hold more than one copy of its plot side by side (a second
"Title:" further right, e.g. column U). Every copy is returned as its own
`SheetGroup` so the caller can decide which copy is genuine — see
ingest.py's stale-template check.

Compared to the .nox, an Excel export loses three things: the EIS
frequencies, the CV scan rates and every measured current. Those are filled
in by ingest.py (frequencies and scan rates from a .nox of the same
procedure, current density from the sheet name), never guessed here.

Output uses the same data classes as nox.py (EISData / CVScan / GCStep), so
everything after this point treats both sources identically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
import openpyxl

from .nox import CVScan, EISData, GCStep


@dataclass
class SheetGroup:
    """One copy of one plot inside one sheet."""
    sheet: str
    column: int                    # 0-based column of its "Title:" cell
    technique: str                 # eis | cv | gcd | unknown
    setting: str                   # gcd: current density as text ("0.5"); cv: range ("5-500"); eis: ""
    title: str
    traces: List[np.ndarray] = field(default_factory=list)   # each (n, 2): x, y
    eis: Optional[EISData] = None
    cv: List[CVScan] = field(default_factory=list)
    gcd: List[GCStep] = field(default_factory=list)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _classify(sheet_name: str, label: str):
    """Technique from the Label row first (it is written by NOVA), sheet name second."""
    lab, low = label.lower(), sheet_name.lower()
    if "nyquist" in lab:
        tech = "eis"
    elif re.search(r"\be\s+vs\s+t\b", lab):
        tech = "gcd"
    elif re.search(r"\bi\s+vs\s+e\b", lab):
        tech = "cv"
    elif any(k in low for k in ("fra", "impedance", "nyquist", "eis")):
        tech = "eis"
    elif "charge" in low or re.match(r"cd\b", low):
        tech = "gcd"
    elif low.startswith("cv"):
        tech = "cv"
    else:
        return "unknown", ""
    if tech == "gcd":
        m = re.findall(r"(\d+(?:\.\d+)?)", low)
        return tech, (m[-1] if m else "")
    if tech == "cv":
        m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", low)
        return tech, (f"{m.group(1)}-{m.group(2)}" if m else "")
    return tech, ""


def _cv_scan_numbers(e: np.ndarray) -> np.ndarray:
    """Scan number per point from the potential sweep alone (an Excel export
    has no "Scan" column). A scan = one forward + one reverse sweep; a stub
    of <2% of the points after the last reversal folds into the scan before."""
    d = np.sign(np.diff(e))
    d[d == 0] = 1
    turns = np.where(np.diff(d) != 0)[0] + 1
    sweep = np.zeros(len(e), int)
    for t in turns:
        sweep[t:] += 1
    for k in range(sweep.max(), 0, -1):
        if (sweep == k).sum() < 0.02 * len(e):
            sweep[sweep == k] = k - 1
    return sweep // 2 + 1


def _gcd_steps(traces: List[np.ndarray], rest_span_v: float = 0.02) -> List[GCStep]:
    """Same rule as nox.py, but from the voltage alone (no current in the export):
    flat -> rest, rising -> charge, falling -> discharge."""
    steps, cycle = [], 0
    for tr in traces:
        t, e = tr[:, 0], tr[:, 1]
        if float(e.max() - e.min()) < rest_span_v:
            steps.append(GCStep("rest", t, e, None, 0))
        elif e[-1] > e[0]:
            cycle += 1
            steps.append(GCStep("charge", t, e, None, cycle))
        else:
            steps.append(GCStep("discharge", t, e, None, max(cycle, 1)))
    return steps


def _read_group(rows, sheet: str, col: int) -> SheetGroup:
    title, label, traces, cur = "", "", [], None
    for r in rows:
        if len(r) <= col + 1:
            continue
        v = r[col]
        if isinstance(v, str):
            tag = v.strip().lower()
            if tag.startswith("title"):
                title = next((str(x).strip() for x in r[col + 1:col + 3]
                              if x is not None and str(x).strip().lower() != "title"), title)
            elif tag.startswith("label"):
                if cur:
                    traces.append(np.array(cur, float))
                cur = []
                if not label:
                    label = " ".join(str(x) for x in r[col:col + 6] if x is not None)
            continue
        if cur is not None and _is_num(v) and _is_num(r[col + 1]):
            cur.append((float(v), float(r[col + 1])))
    if cur:
        traces.append(np.array(cur, float))

    tech, setting = _classify(sheet, label)
    g = SheetGroup(sheet, col, tech, setting, title, traces)
    if tech == "eis" and traces:
        zre, mz = traces[0][:, 0], traces[0][:, 1]
        n = len(zre)
        nan = np.full(n, np.nan)
        g.eis = EISData(freq=nan.copy(), zre=zre, zim=-mz, modulus=np.hypot(zre, mz),
                        phase_deg=np.degrees(np.arctan2(mz, zre)), time_s=nan.copy(),
                        e_dc=nan.copy(), i_dc=nan.copy())
    elif tech == "cv":
        g.cv = [CVScan(scan_rate_V_s=float("nan"), time=None, e_applied=tr[:, 0], e_measured=None,
                       current=tr[:, 1], cycle=_cv_scan_numbers(tr[:, 0])) for tr in traces]
    elif tech == "gcd":
        g.gcd = _gcd_steps(traces)
    return g


def read_nova_xlsx(path) -> List[SheetGroup]:
    """Every plot copy in every sheet of one NOVA Excel export."""
    wb = openpyxl.load_workbook(Path(path), read_only=True, data_only=True)
    out = []
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            title_cols = [i for i, v in enumerate(rows[0])
                          if isinstance(v, str) and v.strip().lower().startswith("title:")]
            for col in title_cols or [0]:
                g = _read_group(rows, ws.title, col)
                if g.traces:
                    out.append(g)
    finally:
        wb.close()
    return out
