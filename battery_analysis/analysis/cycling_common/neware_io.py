"""
NEWARE raw-format reading — a third cycler format, next to Wonatech
(io.py) and ESPEC (espec_io.py).

STATUS: written before any real NEWARE file from this lab existed.
Everything below follows NEWARE's documented/common export layouts, and
it is tested on synthetic files (test_neware_io.py), not yet on a real
one. When the first real file arrives, run

    from battery_analysis.analysis.cycling_common.neware_io import NewareParser
    p = NewareParser("Data/Cycle/.../file.ndax", active_mass_g=0.0032)
    p.describe()          # which columns were found, units, cycles, step types
    p.cycle_summary()

and check the capacities against BTSDA's own cycle sheet before trusting
it. Then hook it into cycle/importer.py the same way espec_channels: is.

Three ways a NEWARE file can arrive — all give the same table:
  .ndax / .nda   the native files from BTS/BTSDA. Read with the
                 open-source NewareNDA package (pip install NewareNDA).
  .xlsx / .xls   BTSDA "export to Excel". The record ("Detail"/"record")
                 sheet(s) are used; long tests split over several sheets
                 are joined back together.
  .csv           BTSDA CSV export of the record data.

Column names differ between BTSDA versions and languages (e.g.
"Voltage(V)", "电压(V)", "Current(A)" vs "Current(mA)"), so columns are
found by meaning, and units read from the header are converted to
V / mA / mAh. Like ESPEC, a NEWARE file does not know the active mass,
so mAh -> mAh/g needs active_mass_g from the YAML (or a lab-book
reference: active_mass_g: labbook:2026-10-24a/C1).

Capacity convention: NEWARE resets capacity at the start of every STEP,
so a half-cycle's capacity is the SUM over its steps of each step's
final capacity (a CC-then-CV charge is two steps). Verify this on the
first real file — if your export is cumulative per cycle instead, set
per_step_capacity=False.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .models import CyclingSeries

# meaning -> header names (normalized: lower-case, letters/digits only, unit removed)
_COLUMNS = {
    "cycle": ["cycleindex", "cycleid", "cycle", "cycleno", "循环号", "循环序号"],
    "step": ["stepindex", "stepid", "step", "stepno", "工步号", "工步序号"],
    "step_type": ["steptype", "stepname", "status", "mode", "工步类型", "工步名称", "状态"],
    "time": ["totaltime", "testtime", "time", "relativetime", "总时间", "测试时间"],
    "step_time": ["steptime", "工步时间"],
    "voltage": ["voltage", "volt", "电压"],
    "current": ["current", "电流"],
    "capacity": ["capacity", "cap", "容量"],
    "charge_capacity": ["chgcap", "chargecapacity", "chgcapacity", "充电容量"],
    "discharge_capacity": ["dchgcap", "dischargecapacity", "dchgcapacity", "放电容量"],
    "date": ["date", "realtime", "timestamp", "absolutetime", "systemtime", "绝对时间", "系统时间"],
}
_UNIT = {"v": 1.0, "mv": 1e-3, "a": 1e3, "ma": 1.0, "ua": 1e-3, "ah": 1e3, "mah": 1.0, "uah": 1e-3}


def _norm(header: str) -> tuple[str, Optional[str]]:
    """'Chg. Cap.(mAh)' -> ('chgcap', 'mah')"""
    m = re.search(r"\(([^)]*)\)\s*$", str(header))
    unit = m.group(1).strip().lower() if m else None
    base = str(header)[: m.start()] if m else str(header)
    return re.sub(r"[\W_]+", "", base.lower()), unit


def _match_columns(columns) -> dict:
    """{meaning: (original header, unit)} for every column we recognise."""
    found = {}
    normed = {c: _norm(c) for c in columns}
    for meaning, names in _COLUMNS.items():
        for name in names:                       # earlier names win
            hits = [c for c, (n, _) in normed.items() if n == name and c not in [v[0] for v in found.values()]]
            if hits:
                found[meaning] = (hits[0], normed[hits[0]][1])
                break
    return found


def _to_seconds(series: pd.Series) -> pd.Series:
    """Numbers stay numbers; 'h:mm:ss.ms' (or 'd-hh:mm:ss') text becomes seconds."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)

    def one(x):
        if pd.isna(x):
            return np.nan
        s = str(x).strip()
        days = 0
        if "-" in s and ":" in s:
            d, s = s.split("-", 1)
            days = float(d)
        parts = [float(p) for p in s.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        h, m, sec = parts[-3:]
        return days * 86400 + h * 3600 + m * 60 + sec
    return series.map(one)


def _direction(step_type: pd.Series, current: pd.Series) -> pd.Series:
    """'charge' / 'discharge' / 'rest' for every row."""
    if step_type is not None:
        t = step_type.astype(str).str.lower().str.replace(r"[\s_.-]", "", regex=True)
        out = pd.Series("rest", index=t.index)
        out[t.str.contains("dchg|dischg|discharge|放电")] = "discharge"
        out[(t.str.contains("chg|charge|充电")) & ~t.str.contains("dchg|dischg|discharge|放电")] = "charge"
        if (out != "rest").any():
            return out
    out = pd.Series("rest", index=current.index)
    out[current > 0] = "charge"
    out[current < 0] = "discharge"
    return out


# ---------------------------------------------------------
# Cache: a parsed raw file is saved once (parquet, or pickle when pyarrow
# is missing) and reused until the raw file changes (size or modified time).
# Lives outside Data/, so raw data stays untouched. Safe to delete any time.
# ---------------------------------------------------------
CACHE_DIR = Path.home() / ".cache" / "battery_analysis" / "neware"
READER_VERSION = 2   # bump when parsing changes, so old caches are re-parsed


def _cache_path(path: Path) -> Path:
    import hashlib
    st = path.stat()
    where = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:10]        # which file
    version = hashlib.sha1(f"{st.st_size}|{st.st_mtime_ns}|v{READER_VERSION}".encode()).hexdigest()[:10]  # which version of it
    return CACHE_DIR / f"{path.stem}_{where}_{version}"


def _cache_load(base: Path) -> Optional[pd.DataFrame]:
    for ext, reader in ((".parquet", pd.read_parquet), (".pkl", pd.read_pickle)):
        f = base.with_suffix(ext)
        if f.exists():
            try:
                return reader(f)
            except Exception:
                f.unlink(missing_ok=True)   # corrupt/old cache: re-parse
    return None


def _cache_save(base: Path, df: pd.DataFrame) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for old in CACHE_DIR.glob(f"{base.name.rsplit('_', 1)[0]}_*"):   # same file, older versions
            old.unlink(missing_ok=True)     # drop caches of older versions of this file
        try:
            df.to_parquet(base.with_suffix(".parquet"))
        except Exception:                   # no pyarrow, or a column parquet can't store
            base.with_suffix(".parquet").unlink(missing_ok=True)
            df.to_pickle(base.with_suffix(".pkl"))
    except Exception:
        pass                                # caching is only a speed-up; never fail the import


def _excel_engine() -> Optional[str]:
    """calamine (Rust, ~5-10x faster than openpyxl) when installed: pip install python-calamine."""
    try:
        import python_calamine  # noqa: F401
        return "calamine"
    except ImportError:
        return None


def read_records(path: str | Path, use_cache: bool = True) -> pd.DataFrame:
    """Raw NEWARE file -> one DataFrame of record rows, original column names.
    Excel/CSV exports are parsed once, then served from CACHE_DIR until the file
    changes (use_cache=False to force). Native .ndax/.nda are fast and never cached."""
    path = Path(path)
    # native .ndax/.nda files parse in about a second: caching them only costs disk space
    use_cache = use_cache and path.suffix.lower() not in (".ndax", ".nda")
    if use_cache:
        base = _cache_path(path)
        cached = _cache_load(base)
        if cached is not None:
            return cached
    df = _read_records_uncached(path)
    if use_cache:
        _cache_save(base, df)
    return df


def _read_records_uncached(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in (".nda", ".ndax"):
        try:
            import NewareNDA
        except ImportError as e:
            raise ImportError(
                f"{path.name} is a native NEWARE file. Install the reader once with\n"
                "    ~/venvs/echem/bin/pip install NewareNDA\n"
                "or export the test from BTSDA to Excel/CSV instead."
            ) from e
        # cycle_mode="auto": a cycle starts at the first non-rest step type, like BTSDA's
        # Excel export (plating-first GCD: DChg+Chg = one cycle; cathode half-cells: Chg first).
        # NewareNDA's default "chg" would pair each stripping with the NEXT plating.
        return NewareNDA.read(str(path), cycle_mode="auto", log_level="WARNING")
    if suffix in (".xlsx", ".xls"):
        engine = _excel_engine() if suffix == ".xlsx" else None
        with pd.ExcelFile(path, engine=engine) as xl:
            names = [n for n in xl.sheet_names
                     if re.match(r"(record|detail|记录|详细)", n.strip().lower())]
            if names:   # only the record sheet(s) -- skip the step/cycle/info sheets
                return pd.concat([xl.parse(n) for n in names], ignore_index=True)
            sheets = {n: xl.parse(n) for n in xl.sheet_names}
        # unknown sheet names: take the sheet with a voltage column and the most rows
        best = max((df for df in sheets.values() if "voltage" in _match_columns(df.columns)),
                   key=len, default=None)
        if best is None:
            raise ValueError(f"{path.name}: no sheet with a voltage column found (sheets: {list(sheets)}).")
        return best
    if suffix == ".csv":
        for enc in ("utf-8-sig", "gbk", "latin-1"):
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
    raise ValueError(f"{path.name}: not a NEWARE file this reader knows (.ndax, .nda, .xlsx, .xls, .csv).")


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Any NEWARE record table -> columns
        cycle, step, direction, time_s, voltage_V, current_mA,
        charge_capacity_mAh, discharge_capacity_mAh
    """
    cols = _match_columns(raw.columns)
    for need in ("cycle", "voltage", "current"):
        if need not in cols:
            raise ValueError(f"No {need} column found. Columns in the file: {list(raw.columns)}")

    def num(meaning, default_unit):
        header, unit = cols[meaning]
        factor = _UNIT.get((unit or default_unit).replace("µ", "u"), 1.0)
        return pd.to_numeric(raw[header], errors="coerce") * factor

    df = pd.DataFrame({
        "cycle": pd.to_numeric(raw[cols["cycle"][0]], errors="coerce").astype("Int64"),
        "voltage_V": num("voltage", "v"),
        "current_mA": num("current", "ma"),
    })
    df["step"] = (pd.to_numeric(raw[cols["step"][0]], errors="coerce") if "step" in cols
                  else (df["current_mA"].diff().abs() > 0).cumsum())
    step_type = raw[cols["step_type"][0]] if "step_type" in cols else None
    df["direction"] = _direction(step_type, df["current_mA"]).values
    if "time" in cols:
        df["time_s"] = _to_seconds(raw[cols["time"][0]]).values
        # NewareNDA (.ndax) names STEP time "Time" -- it resets at every step. When the
        # clock goes backwards, rebuild total test time from the absolute timestamp
        # (matches BTSDA's "Total Time" to < 1 s, checked on Batch2 xlsx vs ndax).
        if (np.diff(df["time_s"].to_numpy(dtype=float)) < 0).any() and "date" in cols:
            ts = pd.to_datetime(raw[cols["date"][0]], errors="coerce")
            if ts.notna().all():
                df["time_s"] = (ts - ts.iloc[0]).dt.total_seconds().values

    if "charge_capacity" in cols and "discharge_capacity" in cols:
        df["charge_capacity_mAh"] = num("charge_capacity", "mah").abs()
        df["discharge_capacity_mAh"] = num("discharge_capacity", "mah").abs()
    elif "capacity" in cols:
        cap = num("capacity", "mah").abs()
        df["charge_capacity_mAh"] = cap.where(df["direction"] == "charge", 0.0)
        df["discharge_capacity_mAh"] = cap.where(df["direction"] == "discharge", 0.0)
    else:
        raise ValueError(f"No capacity column found. Columns in the file: {list(raw.columns)}")
    return df.dropna(subset=["cycle", "voltage_V"]).reset_index(drop=True)


class NewareParser:
    """
    One NEWARE file (one channel/cell).

    ce: how coulombic efficiency is defined for this cell.
      "discharge/charge"  cathode half cells (e.g. LFP vs Na/Li metal) — the default
      "charge/discharge"  plating/stripping cells (Na‖Al, Na‖Cu, anode-free):
                          the metal is PLATED on discharge and STRIPPED on
                          charge, so CE = stripped / plated = charge / discharge.
    """

    def __init__(self, path: str | Path, active_mass_g: Optional[float] = None,
                 per_step_capacity: bool = True, ce: str = "discharge/charge"):
        if ce not in ("discharge/charge", "charge/discharge"):
            raise ValueError(f"ce must be 'discharge/charge' or 'charge/discharge', got {ce!r}.")
        self.path = Path(path)
        self.mass_g = active_mass_g
        self.per_step_capacity = per_step_capacity
        self.ce = ce
        self._raw = read_records(self.path)
        self.records = normalize(self._raw)

    def describe(self) -> str:
        cols = _match_columns(self._raw.columns)
        lines = [f"{self.path.name}: {len(self.records)} rows, cycles "
                 f"{self.records['cycle'].min()}–{self.records['cycle'].max()}"]
        for meaning, (header, unit) in cols.items():
            lines.append(f"  {meaning:<20} <- {header!r}" + (f" [{unit}]" if unit else ""))
        lines.append("  directions: " + ", ".join(f"{k} {v}" for k, v in self.records["direction"].value_counts().items()))
        return "\n".join(lines)

    def _half_cycle_mAh(self) -> pd.DataFrame:
        """cycle, Q_Charge_mAh, Q_Discharge_mAh."""
        r = self.records
        out = {}
        for direction, col in (("charge", "charge_capacity_mAh"), ("discharge", "discharge_capacity_mAh")):
            part = r[r["direction"] == direction]
            if self.per_step_capacity:
                q = part.groupby(["cycle", "step"])[col].max().groupby(level="cycle").sum()
            else:
                q = part.groupby("cycle")[col].max()
            out[f"Q_{direction.capitalize()}_mAh"] = q
        return pd.DataFrame(out).reset_index()

    def cycle_summary(self) -> pd.DataFrame:
        """Cycle, Q_Charge, Q_Discharge (mAh/g if active_mass_g is set, else mAh), CE (%)."""
        df = self._half_cycle_mAh().dropna()
        scale = 1 / self.mass_g if self.mass_g else 1.0
        out = pd.DataFrame({
            "Cycle": df["cycle"].astype(int).to_numpy(),
            "Q_Charge": df["Q_Charge_mAh"].to_numpy() * scale,
            "Q_Discharge": df["Q_Discharge_mAh"].to_numpy() * scale,
        })
        top, bottom = ("Q_Discharge", "Q_Charge") if self.ce == "discharge/charge" else ("Q_Charge", "Q_Discharge")
        with np.errstate(divide="ignore", invalid="ignore"):
            out["CE (%)"] = 100 * out[top] / out[bottom]
        return out

    def voltage_profile(self, cycle: int):
        """(q_ch, v_ch, q_dis, v_dis) for one cycle — capacity in mAh/g (or mAh), steps chained."""
        r = self.records[self.records["cycle"] == cycle]
        scale = 1 / self.mass_g if self.mass_g else 1.0
        result = []
        for direction, col in (("charge", "charge_capacity_mAh"), ("discharge", "discharge_capacity_mAh")):
            part = r[r["direction"] == direction]
            q = part[col].to_numpy(dtype=float)
            if self.per_step_capacity and len(part):
                # add each finished step's capacity onto the next, so CC+CV reads as one curve
                offsets = part.groupby("step")[col].max().cumsum().shift(fill_value=0)
                q = q + part["step"].map(offsets).to_numpy(dtype=float)
            result += [q * scale, part["voltage_V"].to_numpy(dtype=float)]
        return tuple(result)


# =========================================================
# Adapters — same shape as espec_io's, for cycle/ and rate/ importers
# =========================================================

def find_neware_file(data_dir, channel: str) -> Optional[Path]:
    """First file under data_dir whose name contains `channel` and has a NEWARE extension."""
    exts = {".ndax", ".nda", ".xlsx", ".xls", ".csv"}
    matches = sorted(p for p in Path(data_dir).glob(f"**/*{channel}*") if p.suffix.lower() in exts)
    return matches[0] if matches else None


def cycle_series_from_neware(parser: NewareParser, sample_name: str) -> Optional[CyclingSeries]:
    df = parser.cycle_summary()
    if df.empty:
        return None
    return CyclingSeries(
        sample_name=sample_name,
        cycle_number=df["Cycle"].to_numpy(),
        capacity_charge=df["Q_Charge"].to_numpy(),
        capacity_discharge=df["Q_Discharge"].to_numpy(),
        ce_pct=df["CE (%)"].to_numpy(),
        source_file=parser.path,
    )


def rate_series_from_neware(parser: NewareParser, sample_name: str, electrode_type: str = "cathode") -> Optional[CyclingSeries]:
    """Same as cycle_series_from_neware but 0-based cycle index, anode charge/discharge swapped."""
    df = parser.cycle_summary()
    if df.empty:
        return None
    q_ch, q_dis = df["Q_Charge"].to_numpy(), df["Q_Discharge"].to_numpy()
    if electrode_type == "anode":
        q_ch, q_dis = q_dis, q_ch
    with np.errstate(divide="ignore", invalid="ignore"):
        ce = 100 * q_dis / q_ch
    return CyclingSeries(
        sample_name=sample_name,
        cycle_number=np.arange(len(df)),
        capacity_charge=q_ch, capacity_discharge=q_dis, ce_pct=ce,
        source_file=parser.path,
    )
