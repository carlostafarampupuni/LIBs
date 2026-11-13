"""
ESPEC raw-format reading — a second supported raw-data format for Cycle
and Rate, alongside the Wonatech *_Capacity.csv/*_Cycle.csv format
read by io.py/discovery.py.

Why this file exists (and why ESPEC can't just reuse discovery.py)
--------------------------------------------------------------------
Wonatech's format embeds everything auto-discovery needs straight in the
filename: sample name, replicate number, and the file itself already
carries Q_Ch/M [mAh/g] and Q_Dis/M [mAh/g] pre-normalized by whatever
active mass Wonatech's own instrument software was told at export time.

ESPEC's raw export carries none of that. One file per CHANNEL (e.g.
"C1S1CH10_LFP-CYC_All.csv"), with capacity in raw mAh — never per-gram —
and a multi-section header (Test/Pattern/Step Setting Information, then
[Test Data]) instead of a flat CSV. There is no way to recover which
sample a channel belongs to, or the active electrode mass needed to
convert mAh -> mAh/g, from the raw file alone; both are genuinely
external metadata that has to be declared once per channel in the
YAML (`espec_channels:` in a cycle.yaml/rate.yaml batch — see
ESPECChannelConfig / ESPECRateChannelConfig in config/schema.py).
Guessing either would silently corrupt every downstream number (fade %,
resistance trends, EOL forecasts, feature export) with no visible error
— the same "ask, don't guess" reasoning already used elsewhere in this
package (e.g. missing EC-Lab columns raise CyclingFileFormatError
instead of a silent NaN column) is even more important here, since a
wrong active_mass_g doesn't fail loudly, it just produces a plausible-
looking wrong number.

Everything below is ESPECParser, ported near-verbatim from the
espec_parser.py you supplied — same class, same method names, same
Wonatech-compatible column names in cycle_summary()/rate_summary() —
plus two small additions at the bottom (find_espec_file,
cycle_series_from_espec, rate_series_from_espec) that adapt its output
into this package's own CyclingSeries shape, so the importers
(cycle/importer.py, rate/importer.py) and everything downstream of them
(analyzer, plots, degradation diagnosis, forecasting, feature export)
need zero ESPEC-specific code anywhere else.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .models import CyclingSeries

# =========================================================
# LOW-LEVEL FILE READER
# =========================================================


def _find_data_start(path):
    """Return the line index (0-based) of the header row inside [Test Data]."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if line.strip() == "[Test Data]":
                return i + 1   # next line is the column header
    raise ValueError(f"[Test Data] section not found in {path}")


def _read_raw(path):
    """Load the [Test Data] section into a DataFrame."""
    header_row = _find_data_start(path)
    df = pd.read_csv(path, skiprows=header_row, encoding="utf-8", encoding_errors="replace")
    df.columns = [c.strip() for c in df.columns]
    for col in ["Ptn No.", "Step No.", "Ptn cnt.", "Capacity(mAh)", "Volt(V)",
                "Current(mA)", "Power(mW)", "Test Time(s)", "Step Time(s)"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _parse_pattern_meta(path):
    """
    Parse [Pattern Infomation] section and return:
        pattern_names  : list of str   (index 0 = Ptn 1)
        pattern_counts : list of int
    """
    names, counts = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        in_section = False
        for line in f:
            l = line.strip()
            if l == "[Pattern Infomation]":
                in_section = True
                continue
            if in_section and l.startswith("["):
                break
            if in_section:
                if l.startswith("Pattern Name,"):
                    names = [x.strip() for x in l.split(",")[1:] if x.strip()]
                elif l.startswith("Pattern Count,"):
                    counts = [int(x.strip()) for x in l.split(",")[1:] if x.strip().isdigit()]
    return names, counts


# =========================================================
# MAIN PARSER CLASS
# =========================================================


class ESPECParser:
    """
    Parse a single ESPEC _All.csv file and expose Wonatech-compatible
    DataFrames for cycle, rate, and dQ/dV analysis.

    Parameters
    ----------
    path : str or Path
        Path to the ESPEC CSV file.
    active_mass_g : float
        Active electrode mass in grams. Used to convert mAh -> mAh/g.
        Example: 0.00434 for a 4.34 mg electrode.
    formation_ptn : int
        Pattern number (1-based) that contains the formation cycles.
        Defaults to 2 (Rest-10H is usually Ptn 1, formation is Ptn 2).
    cycle_ptn : int or None
        Pattern number for the main cycling data. Set to None for rate files.
    rate_ptns : dict or None
        {ptn_no: label} for rate-capability patterns, e.g. {3: "0.5C", 4: "1C"}.
        Set to None for cycling files.
    """

    def __init__(self, path,
                 active_mass_g,
                 formation_ptn=2,
                 cycle_ptn=None,
                 rate_ptns=None):
        self.path = Path(path)
        self.active_mass_g = active_mass_g       # exposed attribute (used by rate.py etc.)
        self.mass_g = active_mass_g               # internal alias used throughout
        self.formation_ptn = formation_ptn
        self.cycle_ptn = cycle_ptn
        self.rate_ptns = rate_ptns or {}

        self._raw = _read_raw(path)
        self._ptn_names, self._ptn_counts = _parse_pattern_meta(path)

    # ----------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------

    def _half_cycle_capacities(self, ptn_nos=None):
        """
        For each (Ptn No., Ptn cnt.) pair return the maximum Capacity(mAh)
        reached during Charge and Discharge half-cycles.

        Returns a DataFrame with columns:
            Ptn No., Ptn cnt., Q_Charge_mAh, Q_Discharge_mAh
        """
        df = self._raw.copy()
        if ptn_nos is not None:
            df = df[df["Ptn No."].isin(ptn_nos)]

        charge_mask = df["Mode"] == "CC-CV Charge"
        dis_mask = df["Mode"] == "CC DisCharge"

        chg = (df[charge_mask]
               .groupby(["Ptn No.", "Ptn cnt."])["Capacity(mAh)"]
               .max()
               .rename("Q_Charge_mAh"))
        dis = (df[dis_mask]
               .groupby(["Ptn No.", "Ptn cnt."])["Capacity(mAh)"]
               .max()
               .rename("Q_Discharge_mAh"))

        combined = pd.concat([chg, dis], axis=1).reset_index()
        combined = combined.dropna(subset=["Q_Charge_mAh", "Q_Discharge_mAh"])
        return combined

    def _to_mAhg(self, col_mAh):
        """Convert a column in mAh to mAh/g using active mass."""
        return col_mAh / self.mass_g

    # ----------------------------------------------------------
    # CYCLE SUMMARY  (Wonatech-compatible)
    # ----------------------------------------------------------

    def cycle_summary(self, include_formation=True):
        """
        Return a per-cycle DataFrame matching Wonatech Capacity CSV format:
            Cycle, Q_Ch/M [mAh/g], Q_Dis/M [mAh/g], CE (%), Retention (%)

        Parameters
        ----------
        include_formation : bool
            If True, formation cycles are prepended (numbered 1, 2);
            main cycles start at 3. If False, only main cycling cycles
            are returned (numbered from 1).
        """
        rows = []
        cycle_idx = 1

        if include_formation and self.formation_ptn:
            form = self._half_cycle_capacities([self.formation_ptn])
            for _, row in form.iterrows():
                rows.append({
                    "Cycle": cycle_idx,
                    "Q_Ch/M [mAh/g]": row["Q_Charge_mAh"] / self.mass_g,
                    "Q_Dis/M [mAh/g]": row["Q_Discharge_mAh"] / self.mass_g,
                    "Formation": True,
                })
                cycle_idx += 1

        if self.cycle_ptn:
            main = self._half_cycle_capacities([self.cycle_ptn])
            for _, row in main.iterrows():
                rows.append({
                    "Cycle": cycle_idx,
                    "Q_Ch/M [mAh/g]": row["Q_Charge_mAh"] / self.mass_g,
                    "Q_Dis/M [mAh/g]": row["Q_Discharge_mAh"] / self.mass_g,
                    "Formation": False,
                })
                cycle_idx += 1

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df["CE (%)"] = 100 * df["Q_Dis/M [mAh/g]"] / df["Q_Ch/M [mAh/g]"]
        main_rows = df[~df["Formation"]] if "Formation" in df.columns else df
        if not main_rows.empty:
            first_dis = main_rows["Q_Dis/M [mAh/g]"].iloc[0]
            df["Retention (%)"] = 100 * df["Q_Dis/M [mAh/g]"] / first_dis
        else:
            df["Retention (%)"] = np.nan

        # Wonatech-compatibility aliases (same convention cycling_common.io
        # produces, so both formats hand parser.py identically-shaped frames)
        df["Q_Charge"] = df["Q_Ch/M [mAh/g]"]
        df["Q_Discharge"] = df["Q_Dis/M [mAh/g]"]

        return df

    # ----------------------------------------------------------
    # RATE SUMMARY  (Wonatech-compatible)
    # ----------------------------------------------------------

    def rate_summary(self, include_formation=True):
        """
        Return a per-cycle DataFrame for rate capability:
            Rate_Cycle, Q_Ch/M [mAh/g], Q_Dis/M [mAh/g], CE (%), Rate_Label

        Formation cycles are prepended if include_formation=True.
        """
        rows = []
        cycle_idx = 0

        if include_formation and self.formation_ptn:
            form = self._half_cycle_capacities([self.formation_ptn])
            for _, row in form.iterrows():
                rows.append({
                    "Rate_Cycle": cycle_idx,
                    "Q_Ch/M [mAh/g]": row["Q_Charge_mAh"] / self.mass_g,
                    "Q_Dis/M [mAh/g]": row["Q_Discharge_mAh"] / self.mass_g,
                    "Rate_Label": "0.1C",
                    "Ptn No.": self.formation_ptn,
                })
                cycle_idx += 1

        for ptn_no, label in self.rate_ptns.items():
            caps = self._half_cycle_capacities([ptn_no])
            for _, row in caps.iterrows():
                rows.append({
                    "Rate_Cycle": cycle_idx,
                    "Q_Ch/M [mAh/g]": row["Q_Charge_mAh"] / self.mass_g,
                    "Q_Dis/M [mAh/g]": row["Q_Discharge_mAh"] / self.mass_g,
                    "Rate_Label": label,
                    "Ptn No.": ptn_no,
                })
                cycle_idx += 1

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df["CE (%)"] = 100 * df["Q_Dis/M [mAh/g]"] / df["Q_Ch/M [mAh/g]"]
        return df

    def rate_transition_cycles(self, include_formation=True):
        """
        Return the cycle indices (0-based Rate_Cycle) where the C-rate changes.
        Useful for drawing vertical C-rate separator lines on the plot.
        """
        df = self.rate_summary(include_formation=include_formation)
        transitions = []
        prev_label = None
        for i, row in df.iterrows():
            label = row["Rate_Label"]
            if label != prev_label and prev_label is not None:
                transitions.append(row["Rate_Cycle"])
            prev_label = label
        return transitions

    # ----------------------------------------------------------
    # VOLTAGE PROFILES  (for dQ/dV and Cycle/Rate profile plots)
    # ----------------------------------------------------------

    def profile_long(self, ptns=None, max_points_per_halfcycle=2000):
        """
        Return a long-format DataFrame of voltage profiles:
            Ptn No., Ptn cnt., Mode ('charge'|'discharge'),
            Capacity_mAhg, Volt_V
        """
        if ptns is None:
            ptns = []
            if self.formation_ptn:
                ptns.append(self.formation_ptn)
            if self.cycle_ptn:
                ptns.append(self.cycle_ptn)

        df = self._raw[self._raw["Ptn No."].isin(ptns)].copy()

        charge_mask = df["Mode"] == "CC-CV Charge"
        dis_mask = df["Mode"] == "CC DisCharge"

        records = []
        for mode_str, mask in [("charge", charge_mask), ("discharge", dis_mask)]:
            sub = df[mask].copy()
            for (ptn, cnt), grp in sub.groupby(["Ptn No.", "Ptn cnt."]):
                grp = grp.dropna(subset=["Capacity(mAh)", "Volt(V)"])
                if len(grp) == 0:
                    continue
                cap = grp["Capacity(mAh)"].values / self.mass_g
                vol = grp["Volt(V)"].values

                if len(cap) > max_points_per_halfcycle:
                    idx = np.round(np.linspace(0, len(cap) - 1, max_points_per_halfcycle)).astype(int)
                    cap, vol = cap[idx], vol[idx]

                for c, v in zip(cap, vol):
                    records.append({
                        "Ptn No.": ptn,
                        "Ptn cnt.": cnt,
                        "Mode": mode_str,
                        "Capacity_mAhg": c,
                        "Volt_V": v,
                    })

        return pd.DataFrame(records)

    def profile_for_cycle(self, cycle_number, ptn=None):
        """
        Extract charge and discharge arrays for a specific cycle number.

        The mapping from cycle_number -> (ptn, ptn_cnt) follows the same
        convention as cycle_summary(): formation cycles are numbered first.

        Returns
        -------
        q_ch, v_ch, q_dis, v_dis : numpy arrays (or None if not found)
        """
        df_summary = self.cycle_summary(include_formation=True)
        if df_summary.empty or cycle_number not in df_summary["Cycle"].values:
            return None, None, None, None

        row = df_summary[df_summary["Cycle"] == cycle_number].iloc[0]
        is_formation = row.get("Formation", False)
        target_ptn = self.formation_ptn if is_formation else self.cycle_ptn
        if is_formation:
            target_cnt = cycle_number
        else:
            n_form = df_summary["Formation"].sum() if "Formation" in df_summary.columns else 0
            target_cnt = cycle_number - n_form

        raw = self._raw
        chg = raw[(raw["Ptn No."] == target_ptn) &
                  (raw["Ptn cnt."] == target_cnt) &
                  (raw["Mode"] == "CC-CV Charge")].dropna(subset=["Capacity(mAh)", "Volt(V)"])
        dis = raw[(raw["Ptn No."] == target_ptn) &
                  (raw["Ptn cnt."] == target_cnt) &
                  (raw["Mode"] == "CC DisCharge")].dropna(subset=["Capacity(mAh)", "Volt(V)"])

        q_ch = chg["Capacity(mAh)"].values / self.mass_g if len(chg) > 0 else None
        v_ch = chg["Volt(V)"].values if len(chg) > 0 else None
        q_dis = dis["Capacity(mAh)"].values / self.mass_g if len(dis) > 0 else None
        v_dis = dis["Volt(V)"].values if len(dis) > 0 else None

        return q_ch, v_ch, q_dis, v_dis

    # ----------------------------------------------------------
    # PRINT SUMMARY
    # ----------------------------------------------------------

    def summary(self):
        """Print a brief summary of what was parsed."""
        print("=" * 60)
        print(f"  ESPEC File: {self.path.name}")
        print("=" * 60)
        print(f"  Active mass  : {self.active_mass_g:.6f} g")
        print(f"  Total rows   : {len(self._raw)}")
        print()
        print("  Patterns detected:")
        for ptn_no in sorted(self._raw["Ptn No."].dropna().unique()):
            sub = self._raw[self._raw["Ptn No."] == ptn_no]
            maxc = int(sub["Ptn cnt."].max())
            name = (self._ptn_names[int(ptn_no) - 1]
                    if self._ptn_names and int(ptn_no) <= len(self._ptn_names)
                    else "?")
            modes = list(sub["Mode"].unique())
            print(f"    Ptn {int(ptn_no)}: {name}  ({maxc} repeat(s))  modes={modes}")
        print()

        df_cyc = self.cycle_summary(include_formation=True)
        if not df_cyc.empty:
            print(f"  Cycle summary ({len(df_cyc)} cycles):")
            print(df_cyc[["Cycle", "Q_Ch/M [mAh/g]", "Q_Dis/M [mAh/g]", "CE (%)"]].to_string(index=False))
        print()


# =========================================================
# ADAPTERS — ESPECParser output -> this package's own shapes
# =========================================================
# Everything above this line is the parser, kept close to what you
# supplied. Everything below is new: the glue that lets cycle/importer.py
# and rate/importer.py treat an ESPEC channel exactly like a Wonatech
# *_Capacity.csv file once these three calls have run.

def find_espec_file(data_dir, channel: str) -> Optional[Path]:
    """
    Locate the one raw *_All.csv for `channel` under data_dir, searching
    recursively — ESPEC exports nest each channel in its own subfolder,
    typically under a dated run folder (e.g.
    data_dir/260529-25degCYCLE/C1S1CH10/C1S1CH10_LFP-CYC_All.csv), and
    that run-folder name isn't something the YAML should have to know.
    Returns None if no match is found (caller skips with a warning).
    """
    data_dir = Path(data_dir)
    matches = sorted(data_dir.glob(f"**/{channel}*_All.csv"))
    return matches[0] if matches else None


def cycle_series_from_espec(parser: "ESPECParser", sample_name: str) -> Optional[CyclingSeries]:
    """ESPECParser.cycle_summary() -> CyclingSeries, for Cycle's importer."""
    df = parser.cycle_summary(include_formation=True)
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


def rate_series_from_espec(parser: "ESPECParser", sample_name: str, electrode_type: str = "cathode") -> Optional[CyclingSeries]:
    """
    ESPECParser.rate_summary() -> CyclingSeries, for Rate's importer.
    cycle_number is the parser's own Rate_Cycle (already a sequential
    0-based index, formation included) — the same convention Wonatech's
    rate/parser.py builds via np.arange(len(df)).
    """
    df = parser.rate_summary(include_formation=True)
    if df.empty:
        return None
    q_ch, q_dis = df["Q_Ch/M [mAh/g]"].copy(), df["Q_Dis/M [mAh/g]"].copy()
    if electrode_type == "anode":
        # Same convention as rate/parser.py: anode charge = lithiation =
        # what the instrument calls discharge -> swap.
        q_ch, q_dis = q_dis, q_ch
    ce_pct = 100 * q_dis / q_ch
    return CyclingSeries(
        sample_name=sample_name,
        cycle_number=df["Rate_Cycle"].to_numpy(),
        capacity_charge=q_ch.to_numpy(),
        capacity_discharge=q_dis.to_numpy(),
        ce_pct=ce_pct.to_numpy(),
        source_file=parser.path,
    )
