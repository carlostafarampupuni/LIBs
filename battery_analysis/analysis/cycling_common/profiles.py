"""
Voltage-vs-capacity profile import — shared by Cycle and Rate. Reads
formation/custom (and, for Rate, the auto rate-capability sequence)
cycle numbers straight out of each sample's *_Cycle.csv (already
discovered by discover_cycling_files), via cycling_common.io's
extract_cycle_profile, and stores them into profiles_point keyed to the
SAME experiment_id import_cycle()/import_rate() already created for that
sample — so run import_cycle()/import_rate() first.
"""
from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Union

import numpy as np

from .discovery import discover_cycling_files, representative_file
from .io import extract_cycle_profile

# Safety valve against a runaway loop on malformed/never-ending data.
_MAX_RATE_CAPABILITY_STEPS = 500


def rate_capability_cycles(formation_count: int, interval: int, profile_path, modes) -> List[int]:
    """
    The auto-numbered rate-capability profile sequence: actual ordinals
    formation_count+1, formation_count+1+interval, formation_count+1+2*
    interval, ... — i.e. with 2 formation cycles and interval=5, cycle 3
    becomes "cycle 1" of the sequence, then every 5 cycles after that
    (3, 8, 13, 18, ...). Stops automatically at the first ordinal that
    has no data in ANY of `modes` for this sample's file, so you never
    have to know in advance how many rate steps were actually run.
    """
    interval = interval or 1
    cycles: List[int] = []
    cycle_num = formation_count + 1
    while len(cycles) < _MAX_RATE_CAPABILITY_STEPS:
        found = any(extract_cycle_profile(profile_path, cycle_num, mode)[0] is not None for mode in modes)
        if not found:
            break
        cycles.append(cycle_num)
        cycle_num += interval
    return cycles


def _report_formation(sample_name: str, file, report_cycles: Iterable[int]) -> None:
    """
    Print charge capacity / discharge capacity / CE for each formation
    cycle — this is the number that tells you whether formation cycle 1
    or 2 "looks nicer" (battery data on the very first cycle especially
    can be messy), so you can decide what to set formation_plot_cycle to
    without having to render a figure first.
    """
    for cycle_num in report_cycles:
        _, q_ch = extract_cycle_profile(file.profile_path, cycle_num, "charge")
        _, q_dis = extract_cycle_profile(file.profile_path, cycle_num, "discharge")
        if q_ch is None or q_dis is None:
            continue
        q_charge = float(np.max(q_ch))
        q_discharge = float(np.max(q_dis))
        ce = 100 * q_discharge / q_charge if q_charge else float("nan")
        print(
            f"   {sample_name} — formation cycle {cycle_num}: "
            f"charge {q_charge:.1f} mAh/g, discharge {q_discharge:.1f} mAh/g, CE {ce:.1f}%"
        )


def import_profile_points(
    repo, project_id: int, technique: str, data_dir,
    cycle_numbers: Union[Iterable[int], Callable[[object], Iterable[int]]],
    modes: Iterable[str], batch_name=None,
    report_cycles: Optional[Iterable[int]] = None,
) -> int:
    """
    For every sample's representative *_Cycle.csv under data_dir, extract
    voltage/capacity for each (cycle_number, mode) pair and store it into
    profiles_point, keyed to the existing technique='cycle'|'rate'
    experiment already created for that sample (via import_cycle()/
    import_rate()). Returns the number of (cycle, mode) curves imported.

    cycle_numbers may be a fixed list (same cycles for every sample, e.g.
    Cycle's formation+custom lists) or a callable(file) -> list, called
    once per sample's DiscoveredCyclingFile, for when the cycle list
    depends on that sample's own data (Rate's auto rate-capability
    sequence, which stops wherever that sample's data happens to end).

    report_cycles: cycle numbers to print charge/discharge capacity + CE
    for, per sample (typically the formation cycles) — printed once per
    sample regardless of whether that cycle is also in cycle_numbers.
    """
    list_experiments = repo.list_cycle_experiments if technique == "cycle" else repo.list_rate_experiments

    discovered = discover_cycling_files(data_dir)
    if not discovered:
        print(f"⚠️  No *_Cycle.csv files found under {data_dir}")
        return 0

    experiments_df = list_experiments(project_id, batch_name=batch_name)
    total = 0
    for sample in discovered:
        file = representative_file(sample, experiments_df)
        if file is None or file.profile_path is None:
            print(f"⚠️  {sample.name}: no *_Cycle.csv profile file found, skipping")
            continue

        rows = experiments_df[
            (experiments_df["sample"] == sample.name) & (experiments_df["is_representative"] == 1)
        ]
        if rows.empty:
            print(f"⚠️  {sample.name}: no {technique} experiment found — run import_{technique}() first")
            continue
        experiment_id = int(rows.iloc[0]["experiment_id"])
        repo.clear_profile_points(experiment_id)

        numbers = cycle_numbers(file) if callable(cycle_numbers) else cycle_numbers
        for cycle_num in numbers:
            for mode in modes:
                voltage, capacity = extract_cycle_profile(file.profile_path, cycle_num, mode)
                if voltage is None:
                    continue
                repo.save_profile_points(experiment_id, cycle_num, mode, voltage, capacity)
                total += len(voltage)

        if report_cycles:
            _report_formation(sample.name, file, report_cycles)

    if total == 0:
        print("⚠️  No profile points imported.")
    else:
        print(f"✅  Imported {total} profile point(s) into the database.")
    return total
