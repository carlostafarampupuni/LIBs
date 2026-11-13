"""
ProjectRepository: save_*/load_* per technique, transaction handling.

This is the ONLY module that touches SQL. Analysis/plotting modules call
repository.save_eis_measurement(...), repository.load_eis_fit_results(...),
etc. — never raw cursors.

Pattern for migrating a new technique: add save_<technique>_point /
load_<technique>_... methods here, targeting the table already created in
database/schema.py, following the eis_* methods below as the template.
Every technique table hangs off experiment_id — get_or_create_experiment()
is shared by all of them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .connection import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRepository:
    def __init__(self, db_path: str | Path):
        self.conn = get_connection(db_path)

    def close(self) -> None:
        self.conn.close()

    # =====================================================
    # SHARED: project / sample / experiment / raw_file
    # =====================================================

    def get_or_create_project(self, name: str, config_path: str | None = None) -> int:
        cur = self.conn.execute("SELECT id FROM project WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO project (name, config_path, created_at) VALUES (?, ?, ?)",
            (name, config_path, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_or_create_sample(
        self,
        project_id: int,
        name: str,
        color: str | None = None,
        group_label: str | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        cur = self.conn.execute(
            "SELECT id FROM sample WHERE project_id = ? AND name = ?", (project_id, name)
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO sample (project_id, name, color, group_label, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, name, color, group_label, json.dumps(metadata or {})),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_or_create_experiment(
        self,
        project_id: int,
        sample_id: int,
        technique: str,
        source_folder: Optional[str],
        batch_name: Optional[str],
        replicate_number: Optional[int] = None,
        is_representative: bool = False,
        cathode: Optional[str] = None,
        anode: Optional[str] = None,
        cell_type: Optional[str] = None,
        electrolyte: Optional[str] = None,
        additives: Optional[List[str]] = None,
        temperature_c: Optional[float] = None,
        experiment_date: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """
        One row per physical cell (sample + technique + source_folder +
        batch). Re-running an importer updates this row's metadata in
        place (e.g. if you edit cell_defaults in config.yaml and re-run)
        rather than creating a duplicate.
        """
        cur = self.conn.execute(
            "SELECT id FROM experiment WHERE project_id = ? AND sample_id = ? AND technique = ? "
            "AND source_folder IS ? AND batch_name IS ?",
            (project_id, sample_id, technique, source_folder, batch_name),
        )
        row = cur.fetchone()
        additives_json = json.dumps(additives or [])
        is_rep = 1 if is_representative else 0

        if row:
            experiment_id = row["id"]
            self.conn.execute(
                "UPDATE experiment SET replicate_number=?, is_representative=?, cathode=?, anode=?, "
                "cell_type=?, electrolyte=?, additives_json=?, temperature_c=?, experiment_date=?, notes=? "
                "WHERE id = ?",
                (replicate_number, is_rep, cathode, anode, cell_type, electrolyte, additives_json,
                 temperature_c, experiment_date, notes, experiment_id),
            )
        else:
            cur = self.conn.execute(
                "INSERT INTO experiment (project_id, sample_id, technique, source_folder, batch_name, "
                "replicate_number, is_representative, cathode, anode, cell_type, electrolyte, additives_json, "
                "temperature_c, experiment_date, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, sample_id, technique, source_folder, batch_name,
                 replicate_number, is_rep, cathode, anode, cell_type, electrolyte, additives_json,
                 temperature_c, experiment_date, notes, _now()),
            )
            experiment_id = cur.lastrowid
        self.conn.commit()
        return experiment_id

    def save_raw_file(self, experiment_id: int, path: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO raw_file (experiment_id, path, imported_at) VALUES (?, ?, ?)",
            (experiment_id, str(path), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_samples(self, project_id: int) -> pd.DataFrame:
        """Every sample actually registered for this project — the canonical
        source for plotting/analysis code (never re-reads config.samples)."""
        return pd.read_sql_query(
            "SELECT id AS sample_id, name, color, group_label FROM sample "
            "WHERE project_id = ? ORDER BY id",
            self.conn, params=[project_id],
        )

    def describe_experiment(self, experiment_id: int) -> Dict[str, Any]:
        """
        The 'which experiment was this' lookup: every recorded detail for
        one physical cell, in one call.
        """
        row = self.conn.execute(
            "SELECT e.*, s.name AS sample_name, p.name AS project_name "
            "FROM experiment e "
            "JOIN sample s ON s.id = e.sample_id "
            "JOIN project p ON p.id = e.project_id "
            "WHERE e.id = ?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"No experiment with id={experiment_id}")
        d = dict(row)
        d["additives"] = json.loads(d.pop("additives_json") or "[]")
        files = self.conn.execute(
            "SELECT path FROM raw_file WHERE experiment_id = ?", (experiment_id,)
        ).fetchall()
        d["raw_files"] = [f["path"] for f in files]
        return d

    def find_experiments(
        self,
        project_id: int,
        sample_name: Optional[str] = None,
        technique: Optional[str] = None,
        cathode: Optional[str] = None,
        electrolyte: Optional[str] = None,
    ) -> pd.DataFrame:
        """Flexible lookup — 'which experiments used electrolyte X on sample Y'."""
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.technique, e.source_folder, "
            "e.batch_name, e.replicate_number, e.cathode, e.anode, e.cell_type, "
            "e.electrolyte, e.temperature_c, e.experiment_date, e.notes "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ?"
        )
        params: List[Any] = [project_id]
        if sample_name is not None:
            query += " AND s.name = ?"
            params.append(sample_name)
        if technique is not None:
            query += " AND e.technique = ?"
            params.append(technique)
        if cathode is not None:
            query += " AND e.cathode = ?"
            params.append(cathode)
        if electrolyte is not None:
            query += " AND e.electrolyte = ?"
            params.append(electrolyte)
        return pd.read_sql_query(query, self.conn, params=params)

    # =====================================================
    # EIS (migrated)
    # =====================================================

    def clear_eis_measurements(self, experiment_id: int) -> None:
        """Wipe this experiment's EIS rows before a re-import — cascades to
        any DRT results computed from those measurements too, since
        drt_result references eis_measurement_id."""
        meas_ids = [
            r["id"] for r in self.conn.execute(
                "SELECT id FROM eis_measurement WHERE experiment_id = ?", (experiment_id,)
            )
        ]
        for mid in meas_ids:
            self.conn.execute("DELETE FROM eis_spectrum_point WHERE measurement_id = ?", (mid,))
            self.conn.execute("DELETE FROM eis_fit_result WHERE measurement_id = ?", (mid,))
            self.clear_drt_result(mid)
        self.conn.execute("DELETE FROM eis_measurement WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_eis_measurement(
        self,
        experiment_id: int,
        sheet_name: str,
        cycle_number: str,
        freq: np.ndarray,
        z_real: np.ndarray,
        z_imag: np.ndarray,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO eis_measurement (experiment_id, sheet_name, cycle_number) VALUES (?, ?, ?)",
            (experiment_id, sheet_name, cycle_number),
        )
        measurement_id = cur.lastrowid
        rows = [
            (measurement_id, i, float(f), float(zr), float(zi))
            for i, (f, zr, zi) in enumerate(zip(freq, z_real, z_imag))
        ]
        self.conn.executemany(
            "INSERT INTO eis_spectrum_point (measurement_id, idx, freq, z_real, z_imag) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return measurement_id

    def save_eis_fit_result(
        self, measurement_id: int, circuit_key: str, params: Dict[str, float], method: str,
    ) -> None:
        # Idempotent: clear this measurement's prior fit results first, so
        # re-running fit_eis() after a config tweak doesn't duplicate rows.
        self.conn.execute("DELETE FROM eis_fit_result WHERE measurement_id = ?", (measurement_id,))
        rows = [(measurement_id, circuit_key, name, float(value), method) for name, value in params.items()]
        self.conn.executemany(
            "INSERT INTO eis_fit_result (measurement_id, circuit_key, param_name, value, method) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def list_eis_measurements(
        self, project_id: int, sheet_name: Optional[str] = None, batch_name: Optional[str] = None
    ) -> pd.DataFrame:
        query = (
            "SELECT m.id AS measurement_id, m.experiment_id, s.name AS sample, "
            "e.batch_name, e.source_folder, e.replicate_number, e.is_representative, "
            "m.sheet_name, m.cycle_number "
            "FROM eis_measurement m "
            "JOIN experiment e ON e.id = m.experiment_id "
            "JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ?"
        )
        params: List[Any] = [project_id]
        if sheet_name is not None:
            query += " AND m.sheet_name = ?"
            params.append(sheet_name)
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_eis_spectrum(self, measurement_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT idx, freq, z_real, z_imag FROM eis_spectrum_point "
            "WHERE measurement_id = ? ORDER BY idx",
            self.conn,
            params=[measurement_id],
        )

    def set_representative(self, chosen_experiment_id: int, other_experiment_ids) -> None:
        """Make one experiment the plotted/analysed replicate of its sample, the others not."""
        self.conn.execute("UPDATE experiment SET is_representative = 1 WHERE id = ?", (int(chosen_experiment_id),))
        self.conn.executemany("UPDATE experiment SET is_representative = 0 WHERE id = ?",
                              [(int(i),) for i in other_experiment_ids])
        self.conn.commit()

    def load_eis_model_fit(self, measurement_id: int):
        """(circuit string, {param: value}) of this spectrum's equivalent-circuit fit, or None
        when it was only frequency-extracted (or not fitted at all)."""
        rows = self.conn.execute(
            "SELECT circuit_key, param_name, value FROM eis_fit_result "
            "WHERE measurement_id = ? AND method = 'model_fit'", (measurement_id,)
        ).fetchall()
        if not rows:
            return None
        return rows[0][0], {name: value for _, name, value in rows}

    def load_eis_fit_results(self, project_id: int, batch_name: Optional[str] = None,
                             representative_only: bool = True) -> pd.DataFrame:
        """
        Wide-format DataFrame: one row per (sheet, sample), one column per
        fitted/extracted parameter. Scoped to one batch by default caller
        behaviour (see run_fit/run_degradation_analysis) — a project whose
        database holds multiple batches must not silently merge their
        cycle trends into one, which would corrupt the degradation analysis.
        """
        query = (
            "SELECT m.sheet_name AS Sheet, s.name AS Sample, m.cycle_number AS Cycle, "
            "e.batch_name AS Batch, e.source_folder AS Folder, f.param_name, f.value "
            "FROM eis_fit_result f "
            "JOIN eis_measurement m ON m.id = f.measurement_id "
            "JOIN experiment e ON e.id = m.experiment_id "
            "JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ?"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        if representative_only:      # one trial per sample: the one plotted (see eis.yaml representative:)
            query += " AND e.is_representative = 1"
        long_df = pd.read_sql_query(query, self.conn, params=params)
        if long_df.empty:
            return long_df
        wide = long_df.pivot_table(
            index=["Sheet", "Sample", "Cycle", "Batch", "Folder"],
            columns="param_name",
            values="value",
            aggfunc="first",
        ).reset_index()
        wide.columns.name = None
        return wide

    # =====================================================
    # Cycle (migrated)
    # =====================================================

    def clear_cycle_points(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM cycle_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_cycle_points(
        self, experiment_id: int, cycle_number, capacity_charge, capacity_discharge, ce_pct, retention_pct,
    ) -> None:
        rows = [
            (experiment_id, int(c), float(qc), float(qd), float(ce), float(r))
            for c, qc, qd, ce, r in zip(cycle_number, capacity_charge, capacity_discharge, ce_pct, retention_pct)
        ]
        self.conn.executemany(
            "INSERT INTO cycle_point (experiment_id, cycle_number, capacity_charge, "
            "capacity_discharge, ce_pct, retention_pct) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def list_cycle_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder, "
            "e.replicate_number, e.is_representative "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'cycle'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_cycle_points(self, experiment_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT cycle_number, capacity_charge, capacity_discharge, ce_pct, retention_pct "
            "FROM cycle_point WHERE experiment_id = ? ORDER BY cycle_number",
            self.conn, params=[experiment_id],
        )

    # =====================================================
    # Rate (migrated)
    # =====================================================

    def clear_rate_points(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM rate_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_rate_points(
        self, experiment_id: int, rate_cycle_index, capacity_charge, capacity_discharge, ce_pct,
    ) -> None:
        rows = [
            (experiment_id, int(i), float(qc), float(qd), float(ce))
            for i, qc, qd, ce in zip(rate_cycle_index, capacity_charge, capacity_discharge, ce_pct)
        ]
        self.conn.executemany(
            "INSERT INTO rate_point (experiment_id, rate_cycle_index, capacity_charge, "
            "capacity_discharge, ce_pct) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def list_technique_experiments(self, project_id: int, technique: str,
                                   batch_name: Optional[str] = None) -> pd.DataFrame:
        """Experiments of any one technique (sample, source file, representative flag) — e.g. supercap GCD."""
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder, "
            "e.replicate_number, e.is_representative "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = ?"
        )
        params: List[Any] = [project_id, technique]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def list_rate_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder, "
            "e.replicate_number, e.is_representative "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'rate'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_rate_points(self, experiment_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT rate_cycle_index, capacity_charge, capacity_discharge, ce_pct "
            "FROM rate_point WHERE experiment_id = ? ORDER BY rate_cycle_index",
            self.conn, params=[experiment_id],
        )

    # =====================================================
    # Voltage-vs-capacity profiles (Cycle + Rate formation/arbitrary-cycle
    # plots) — rides on the same experiment_id import_cycle()/import_rate()
    # already created for that sample; not its own technique.
    # =====================================================

    def clear_profile_points(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM profiles_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_profile_points(self, experiment_id: int, cycle_number: int, mode: str, voltage, capacity) -> None:
        rows = [
            (experiment_id, int(cycle_number), mode, idx, float(v), float(q))
            for idx, (v, q) in enumerate(zip(voltage, capacity))
        ]
        self.conn.executemany(
            "INSERT INTO profiles_point (experiment_id, cycle_number, mode, idx, voltage, capacity) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def load_profile_points(self, experiment_id: int, cycle_number: int, mode: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT idx, voltage, capacity FROM profiles_point "
            "WHERE experiment_id = ? AND cycle_number = ? AND mode = ? ORDER BY idx",
            self.conn, params=[experiment_id, int(cycle_number), mode],
        )

    def list_profile_cycles(self, experiment_id: int) -> pd.DataFrame:
        """Every (cycle_number, mode) pair actually imported for this experiment."""
        return pd.read_sql_query(
            "SELECT DISTINCT cycle_number, mode FROM profiles_point "
            "WHERE experiment_id = ? ORDER BY cycle_number, mode",
            self.conn, params=[experiment_id],
        )

    # =====================================================
    # GCD / CE (migrated — same tables, technique='gcd')
    # =====================================================

    def clear_gcd(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM gcd_trace_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM gcd_cycle_result WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM gcd_anomaly WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM gcd_method3_result WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def delete_gcd_experiment(self, experiment_id: int) -> None:
        """A GCD experiment row and everything stored under it."""
        self.clear_gcd(experiment_id)
        self.conn.execute("DELETE FROM experiment WHERE id = ?", (experiment_id,))
        self.conn.commit()

    def save_gcd_trace(self, experiment_id: int, time_h, voltage_v) -> None:
        rows = [(experiment_id, i, float(t), float(v)) for i, (t, v) in enumerate(zip(time_h, voltage_v))]
        self.conn.executemany(
            "INSERT INTO gcd_trace_point (experiment_id, idx, time_h, voltage_v) VALUES (?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def load_gcd_trace(self, experiment_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT idx, time_h, voltage_v FROM gcd_trace_point WHERE experiment_id = ? ORDER BY idx",
            self.conn, params=[experiment_id],
        )

    def save_gcd_cycle_results(self, experiment_id: int, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows = [
            (
                experiment_id, int(r["cycle"]), r["Q_plate (mAh/cm2)"], r["Q_strip (mAh/cm2)"],
                r["CE (%)"], r["LLI (mAh/cm2)"], r["LLI_cumul (mAh/cm2)"], r["cap_ret (%)"],
                r["t_nucleation (h)"], r["V_nucleation (V)"], int(bool(r["genuine_nuc_dip"])),
                r["V_plateau_plate (V)"], r["eta_nuc (V)"], r["t_pol_max (h)"], r["V_pol_max (V)"],
                r["V_plateau_strip (V)"], r["strip_shape"], r["hysteresis (V)"],
            )
            for _, r in df.iterrows()
        ]
        self.conn.executemany(
            "INSERT INTO gcd_cycle_result (experiment_id, cycle, q_plate, q_strip, ce_pct, lli, "
            "lli_cumul, cap_ret_pct, t_nucleation_h, v_nucleation, genuine_nuc_dip, v_plateau_plate, "
            "eta_nuc, t_pol_max_h, v_pol_max, v_plateau_strip, strip_shape, hysteresis) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def save_gcd_anomalies(self, experiment_id: int, anomalies: list) -> None:
        rows = [(experiment_id, cyc, phase, t, v, dv) for cyc, phase, t, v, dv in anomalies]
        if rows:
            self.conn.executemany(
                "INSERT INTO gcd_anomaly (experiment_id, cycle, phase, time_h, voltage_v, delta_v) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            self.conn.commit()

    def save_gcd_method3_result(self, experiment_id: int, result: dict) -> None:
        self.conn.execute(
            "INSERT INTO gcd_method3_result (experiment_id, qt, qs, qc_avg, ce_avg, ce_initial) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (experiment_id, result["QT"], result["QS"], result["Qc_avg"], result["CE_avg"], result["CE_initial"]),
        )
        self.conn.commit()

    def list_gcd_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder, "
            "e.replicate_number, e.is_representative "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'gcd'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_gcd_cycle_results(self, experiment_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM gcd_cycle_result WHERE experiment_id = ? ORDER BY cycle",
            self.conn, params=[experiment_id],
        )

    def load_gcd_anomalies(self, experiment_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM gcd_anomaly WHERE experiment_id = ? ORDER BY cycle",
            self.conn, params=[experiment_id],
        )

    # =====================================================
    # XPS (migrated)
    # =====================================================

    def clear_xps(self, experiment_id: int, orbital: Optional[str] = None) -> None:
        if orbital is None:
            self.conn.execute("DELETE FROM xps_trace_point WHERE experiment_id = ?", (experiment_id,))
            self.conn.execute("DELETE FROM xps_peak WHERE experiment_id = ?", (experiment_id,))
            self.conn.execute("DELETE FROM xps_peak_curve WHERE experiment_id = ?", (experiment_id,))
        else:
            self.conn.execute(
                "DELETE FROM xps_trace_point WHERE experiment_id = ? AND orbital = ?",
                (experiment_id, orbital),
            )
            self.conn.execute(
                "DELETE FROM xps_peak WHERE experiment_id = ? AND orbital = ?",
                (experiment_id, orbital),
            )
            self.conn.execute(
                "DELETE FROM xps_peak_curve WHERE experiment_id = ? AND orbital = ?",
                (experiment_id, orbital),
            )
        self.conn.commit()

    def save_xps_trace(
        self, experiment_id: int, orbital: str, be, raw_intensity, peak_sum=None, background=None,
    ) -> None:
        n = len(be)
        peak_sum = peak_sum if peak_sum is not None else [None] * n
        background = background if background is not None else [None] * n
        rows = [
            (experiment_id, orbital, i, float(be[i]), float(raw_intensity[i]),
             None if peak_sum[i] is None else float(peak_sum[i]),
             None if background[i] is None else float(background[i]))
            for i in range(n)
        ]
        self.conn.executemany(
            "INSERT INTO xps_trace_point (experiment_id, orbital, idx, binding_energy, "
            "raw_intensity, peak_sum, background) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def save_xps_peaks(self, experiment_id: int, orbital: str, peaks: list) -> None:
        rows = [
            (experiment_id, orbital, p.binding_energy, p.area, p.fraction_pct, p.assignment)
            for p in peaks
        ]
        self.conn.executemany(
            "INSERT INTO xps_peak (experiment_id, orbital, binding_energy, area, fraction_pct, assignment) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def save_xps_peak_curves(self, experiment_id: int, orbital: str, peak_curves) -> None:
        """peak_curves: 2D array, one column per fitted peak curve (see analyzer.py docstring)."""
        rows = []
        n_points, n_peaks = peak_curves.shape
        for peak_index in range(n_peaks):
            for idx in range(n_points):
                rows.append((experiment_id, orbital, peak_index, idx, float(peak_curves[idx, peak_index])))
        self.conn.executemany(
            "INSERT INTO xps_peak_curve (experiment_id, orbital, peak_index, idx, value) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def load_xps_peak_curves(self, experiment_id: int, orbital: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT peak_index, idx, value FROM xps_peak_curve "
            "WHERE experiment_id = ? AND orbital = ? ORDER BY peak_index, idx",
            self.conn, params=[experiment_id, orbital],
        )

    # =====================================================
    # dQ/dV (migrated) — reuses Cycle's raw *_Cycle.csv profile files
    # =====================================================

    def clear_dqdv(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM dqdv_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM dqdv_peak WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_dqdv_curve(self, experiment_id: int, cycle_number: int, mode: str, voltage, dqdv) -> None:
        rows = [
            (experiment_id, cycle_number, mode, i, float(v), float(d))
            for i, (v, d) in enumerate(zip(voltage, dqdv))
        ]
        self.conn.executemany(
            "INSERT INTO dqdv_point (experiment_id, cycle_number, mode, idx, voltage, dqdv) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def save_dqdv_peaks(self, experiment_id: int, cycle_number: int, mode: str, peaks: list) -> None:
        rows = [(experiment_id, cycle_number, mode, float(v), float(i)) for v, i in peaks]
        if rows:
            self.conn.executemany(
                "INSERT INTO dqdv_peak (experiment_id, cycle_number, mode, voltage, intensity) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self.conn.commit()

    def list_dqdv_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder, "
            "e.replicate_number, e.is_representative "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'dqdv'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_dqdv_curve(self, experiment_id: int, cycle_number: int, mode: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT idx, voltage, dqdv FROM dqdv_point "
            "WHERE experiment_id = ? AND cycle_number = ? AND mode = ? ORDER BY idx",
            self.conn, params=[experiment_id, cycle_number, mode],
        )

    def load_dqdv_peaks(self, experiment_id: int, cycle_number: int, mode: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT voltage, intensity FROM dqdv_peak "
            "WHERE experiment_id = ? AND cycle_number = ? AND mode = ? ORDER BY voltage",
            self.conn, params=[experiment_id, cycle_number, mode],
        )

    def list_dqdv_cycles(self, experiment_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT DISTINCT cycle_number, mode FROM dqdv_point WHERE experiment_id = ? "
            "ORDER BY cycle_number, mode",
            self.conn, params=[experiment_id],
        )

    def list_xps_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.source_folder AS condition, "
            "e.batch_name FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'xps'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_xps_trace(self, experiment_id: int, orbital: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT idx, binding_energy, raw_intensity, peak_sum, background "
            "FROM xps_trace_point WHERE experiment_id = ? AND orbital = ? ORDER BY idx",
            self.conn, params=[experiment_id, orbital],
        )

    def load_xps_peaks(self, experiment_id: int, orbital: Optional[str] = None) -> pd.DataFrame:
        query = "SELECT * FROM xps_peak WHERE experiment_id = ?"
        params: List[Any] = [experiment_id]
        if orbital is not None:
            query += " AND orbital = ?"
            params.append(orbital)
        return pd.read_sql_query(query, self.conn, params=params)

    # =====================================================
    # DRT (migrated) — a transform of an existing EIS spectrum
    # =====================================================

    def clear_drt_result(self, eis_measurement_id: int) -> None:
        result_ids = [
            r["id"] for r in self.conn.execute(
                "SELECT id FROM drt_result WHERE eis_measurement_id = ?", (eis_measurement_id,)
            )
        ]
        for rid in result_ids:
            self.conn.execute("DELETE FROM drt_point WHERE result_id = ?", (rid,))
            self.conn.execute("DELETE FROM drt_peak WHERE result_id = ?", (rid,))
        self.conn.execute(
            "DELETE FROM drt_result WHERE eis_measurement_id = ?", (eis_measurement_id,)
        )
        self.conn.commit()

    def save_drt_result(
        self,
        eis_measurement_id: int,
        r_inf: float,
        l_ind: float,
        tau: np.ndarray,
        gamma: np.ndarray,
        peaks: List[Dict[str, Any]],
        n_tau: int, lambda_reg: float, rbf_type: str, data_use: str,
        reg_order: int, fwhm_coeff: float,
    ) -> int:
        self.clear_drt_result(eis_measurement_id)
        cur = self.conn.execute(
            "INSERT INTO drt_result (eis_measurement_id, r_inf, l_ind, n_tau, lambda_reg, "
            "rbf_type, data_use, reg_order, fwhm_coeff) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eis_measurement_id, r_inf, l_ind, n_tau, lambda_reg, rbf_type, data_use, reg_order, fwhm_coeff),
        )
        result_id = cur.lastrowid

        point_rows = [(result_id, i, float(t), float(g)) for i, (t, g) in enumerate(zip(tau, gamma))]
        self.conn.executemany(
            "INSERT INTO drt_point (result_id, idx, tau, gamma) VALUES (?, ?, ?, ?)", point_rows
        )

        peak_rows = [
            (result_id, p["label"], p["description"], p["tau_peak"], p["gamma_peak"],
             p["log_tau"], p["frequency_hz"])
            for p in peaks
        ]
        self.conn.executemany(
            "INSERT INTO drt_peak (result_id, label, description, tau_peak, gamma_peak, "
            "log_tau, frequency_hz) VALUES (?, ?, ?, ?, ?, ?, ?)",
            peak_rows,
        )
        self.conn.commit()
        return result_id

    def list_drt_results(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        """One row per computed DRT result, joined back to sample/sheet for browsing."""
        query = (
            "SELECT r.id AS result_id, r.eis_measurement_id, s.name AS sample, "
            "e.batch_name, e.source_folder, m.sheet_name, m.cycle_number, r.r_inf, r.l_ind "
            "FROM drt_result r "
            "JOIN eis_measurement m ON m.id = r.eis_measurement_id "
            "JOIN experiment e ON e.id = m.experiment_id "
            "JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ?"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_drt_curve(self, result_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT idx, tau, gamma FROM drt_point WHERE result_id = ? ORDER BY idx",
            self.conn, params=[result_id],
        )

    def load_drt_peaks(self, result_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT label, description, tau_peak, gamma_peak, log_tau, frequency_hz "
            "FROM drt_peak WHERE result_id = ? ORDER BY log_tau",
            self.conn, params=[result_id],
        )

    # =====================================================
    # RIC (reversible/irreversible capacity) — derived from the same
    # *_Capacity.csv files Cycle reads, technique='ric'.
    # =====================================================

    def clear_ric_points(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM ric_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_ric_points(self, experiment_id: int, cycle_number, cc, cd, ric, ric_disconn, ric_sei) -> None:
        """
        ric_disconn/ric_sei are one element shorter than cycle_number (a
        between-consecutive-cycles quantity) — pass them padded with a
        trailing None/np.nan, matching cycle_number's length.
        """
        rows = []
        for i, c in enumerate(cycle_number):
            d = ric_disconn[i] if i < len(ric_disconn) else None
            s = ric_sei[i] if i < len(ric_sei) else None
            rows.append((
                experiment_id, int(c), float(cc[i]), float(cd[i]), float(ric[i]),
                None if d is None or (isinstance(d, float) and np.isnan(d)) else float(d),
                None if s is None or (isinstance(s, float) and np.isnan(s)) else float(s),
            ))
        self.conn.executemany(
            "INSERT INTO ric_point (experiment_id, cycle_number, cc, cd, ric, ric_disconn, ric_sei) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def list_ric_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder, "
            "e.replicate_number, e.is_representative "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'ric'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_ric_points(self, experiment_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT cycle_number, cc, cd, ric, ric_disconn, ric_sei "
            "FROM ric_point WHERE experiment_id = ? ORDER BY cycle_number",
            self.conn, params=[experiment_id],
        )

    # =====================================================
    # SEM — one experiment per (sample, role, image); metrics stored as
    # (metric_name, value) rows, technique='sem'.
    # =====================================================

    def clear_sem_metrics(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM sem_metric WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_sem_metrics(self, experiment_id: int, role: str, image_path: str, metrics: Dict[str, float]) -> None:
        rows = [
            (experiment_id, name, None if value is None else float(value), role, str(image_path))
            for name, value in metrics.items()
        ]
        self.conn.executemany(
            "INSERT INTO sem_metric (experiment_id, metric_name, value, role, image_path) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def save_sem_array(self, experiment_id: int, role: str, image_path: str, array_name: str, values) -> None:
        """Store a raw array (e.g. per-particle diameters) as one row per value,
        metric_name f'{array_name}[i]' — used for histograms (plot_sem reads
        these back via load_sem_array)."""
        rows = [
            (experiment_id, f"{array_name}[{i}]", float(v), role, str(image_path))
            for i, v in enumerate(values)
        ]
        if rows:
            self.conn.executemany(
                "INSERT INTO sem_metric (experiment_id, metric_name, value, role, image_path) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self.conn.commit()

    def load_sem_array(self, experiment_id: int, array_name: str) -> np.ndarray:
        rows = self.conn.execute(
            "SELECT metric_name, value FROM sem_metric WHERE experiment_id = ? AND metric_name LIKE ?",
            (experiment_id, f"{array_name}[%"),
        ).fetchall()
        # metric_name is f"{array_name}[i]" — sort by the numeric index, not lexicographically.
        def _idx(name: str) -> int:
            return int(name[len(array_name) + 1:-1])
        rows = sorted(rows, key=lambda r: _idx(r["metric_name"]))
        return np.array([r["value"] for r in rows])

    def list_sem_experiments(self, project_id: int, batch_name: Optional[str] = None,
                              role: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT DISTINCT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder, "
            "e.replicate_number, e.is_representative, m.role "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "JOIN sem_metric m ON m.experiment_id = e.id "
            "WHERE e.project_id = ? AND e.technique = 'sem'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        if role is not None:
            query += " AND m.role = ?"
            params.append(role)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_sem_metrics(self, experiment_id: int) -> Dict[str, float]:
        rows = self.conn.execute(
            "SELECT metric_name, value FROM sem_metric WHERE experiment_id = ? AND metric_name NOT LIKE '%[%]'",
            (experiment_id,),
        ).fetchall()
        return {r["metric_name"]: r["value"] for r in rows}

    # =====================================================
    # FTIR — curated-Excel spectra, technique='ftir'.
    # =====================================================

    def clear_ftir_points(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM ftir_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_ftir_points(self, experiment_id: int, spectrum_type: str, wavenumber, value) -> None:
        rows = [
            (experiment_id, spectrum_type, i, float(w), float(v))
            for i, (w, v) in enumerate(zip(wavenumber, value))
        ]
        self.conn.executemany(
            "INSERT INTO ftir_point (experiment_id, spectrum_type, idx, wavenumber, value) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def list_ftir_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'ftir'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def load_ftir_points(self, experiment_id: int, spectrum_type: Optional[str] = None) -> pd.DataFrame:
        query = "SELECT spectrum_type, idx, wavenumber, value FROM ftir_point WHERE experiment_id = ?"
        params: List[Any] = [experiment_id]
        if spectrum_type is not None:
            query += " AND spectrum_type = ?"
            params.append(spectrum_type)
        query += " ORDER BY idx"
        return pd.read_sql_query(query, self.conn, params=params)

    # =====================================================
    # CV — curated-Excel scan-rate sweeps, technique='cv'.
    # =====================================================

    def clear_cv_points(self, experiment_id: int) -> None:
        self.conn.execute("DELETE FROM cv_point WHERE experiment_id = ?", (experiment_id,))
        self.conn.execute("DELETE FROM raw_file WHERE experiment_id = ?", (experiment_id,))
        self.conn.commit()

    def save_cv_points(self, experiment_id: int, scan_rate: float, voltage, current) -> None:
        rows = [
            (experiment_id, scan_rate, i, float(v), float(c))
            for i, (v, c) in enumerate(zip(voltage, current))
        ]
        self.conn.executemany(
            "INSERT INTO cv_point (experiment_id, scan_rate, idx, voltage, current) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def list_cv_experiments(self, project_id: int, batch_name: Optional[str] = None) -> pd.DataFrame:
        query = (
            "SELECT e.id AS experiment_id, s.name AS sample, e.batch_name, e.source_folder "
            "FROM experiment e JOIN sample s ON s.id = e.sample_id "
            "WHERE e.project_id = ? AND e.technique = 'cv'"
        )
        params: List[Any] = [project_id]
        if batch_name is not None:
            query += " AND e.batch_name = ?"
            params.append(batch_name)
        return pd.read_sql_query(query, self.conn, params=params)

    def list_cv_scan_rates(self, experiment_id: int) -> List[float]:
        rows = self.conn.execute(
            "SELECT DISTINCT scan_rate FROM cv_point WHERE experiment_id = ? ORDER BY scan_rate",
            (experiment_id,),
        ).fetchall()
        return [r["scan_rate"] for r in rows]

    def load_cv_points(self, experiment_id: int, scan_rate: float) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT idx, voltage, current FROM cv_point WHERE experiment_id = ? AND scan_rate = ? ORDER BY idx",
            self.conn, params=[experiment_id, scan_rate],
        )
