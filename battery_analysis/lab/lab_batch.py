"""
Batch mode — run every electrode measurement / slurry recipe / N-P pair
listed in a YAML file in one go, and write the results to CSV. This is
the "run batch experiments" mode: no prompts, just a YAML file in, CSVs
out, so you can keep every formulation you've ever tried in one
version-controlled file instead of a new spreadsheet tab per run.

Ported from the battery_lab_toolkit's electrode_lab/batch.py, folded
directly into battery_analysis (flat module, no new folder). Usually
called via project.run_lab_batch(...) (see project.py) rather than
directly — that resolves paths relative to the project folder and
defaults output into this project's exports/ folder.

    python -m battery_analysis.lab.lab_batch lab_batch_example.yaml

See lab_batch_example.yaml at the project root for the file schema.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .lab_electrode import ElectrodeMeasurement
from .lab_slurry import SlurryComponent, SlurryFormulation
from .lab_np_ratio import np_ratio_from_electrodes


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data or {}


def _build_electrode(spec: dict) -> ElectrodeMeasurement:
    spec = dict(spec)
    return ElectrodeMeasurement(**spec)


def _build_slurry(spec: dict) -> SlurryFormulation:
    spec = dict(spec)
    name = spec.pop("name", "unnamed")
    components = [
        SlurryComponent(
            name=c["name"], ratio=c["ratio"],
            concentration_fraction=c.get("concentration_pct", 100) / 100,
            is_solvent=c.get("is_solvent", False),
        )
        for c in spec.pop("components")
    ]
    formulation = SlurryFormulation(components=components, **spec)
    formulation._name = name   # stash for the output table; not a dataclass field
    return formulation


def run_batch(config_path: str | Path, out_dir: Optional[str | Path] = None) -> dict:
    config_path = Path(config_path)
    out_dir = Path(out_dir) if out_dir else config_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = _load_yaml(config_path)

    results = {}

    electrodes = {}
    if cfg.get("electrodes"):
        for spec in cfg["electrodes"]:
            e = _build_electrode(spec)
            electrodes[e.name] = e
        df = pd.DataFrame([e.summary() for e in electrodes.values()])
        out_path = out_dir / "electrodes_results.csv"
        df.to_csv(out_path, index=False)
        results["electrodes"] = df
        print(f"✅  {len(df)} electrode(s) -> {out_path}")

    if cfg.get("slurries"):
        rows = []
        totals = []
        for spec in cfg["slurries"]:
            formulation = _build_slurry(spec)
            s = formulation.summary()
            for r in s["components"]:
                rows.append({"formulation": formulation._name, **r})
            totals.append({
                "formulation": formulation._name,
                "extra_solvent_g": s["extra_solvent_g"],
                "total_dry_solids_g": s["total_dry_solids_g"],
                "total_slurry_mass_g": s["total_slurry_mass_g"],
                "total_solids_pct": s["total_solids_pct"],
            })
        components_df = pd.DataFrame(rows)
        totals_df = pd.DataFrame(totals)
        components_path = out_dir / "slurries_components.csv"
        totals_path = out_dir / "slurries_totals.csv"
        components_df.to_csv(components_path, index=False)
        totals_df.to_csv(totals_path, index=False)
        results["slurries_components"] = components_df
        results["slurries_totals"] = totals_df
        print(f"✅  {len(totals_df)} slurry formulation(s) -> {components_path}, {totals_path}")

    if cfg.get("np_pairs"):
        rows = []
        for pair in cfg["np_pairs"]:
            anode = electrodes.get(pair["anode"])
            cathode = electrodes.get(pair["cathode"])
            if anode is None or cathode is None:
                missing = pair["anode"] if anode is None else pair["cathode"]
                print(f"⚠️  N/P pair skipped — electrode {missing!r} isn't in this file's `electrodes:` list.")
                continue
            rows.append(np_ratio_from_electrodes(anode, cathode))
        if rows:
            df = pd.DataFrame(rows)
            out_path = out_dir / "np_ratios.csv"
            df.to_csv(out_path, index=False)
            results["np_ratios"] = df
            print(f"✅  {len(df)} N/P pair(s) -> {out_path}")

    if not results:
        print("Nothing to do — this YAML has no electrodes:, slurries:, or np_pairs: sections.")

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m battery_analysis.lab.lab_batch <config.yaml> [output_dir]")
        raise SystemExit(1)
    run_batch(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
