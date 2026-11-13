"""Reads dQ/dV peaks back from the database and produces a narrative report."""
from __future__ import annotations

from .analyzer import narrative as _narrative
from .degradation import diagnose as _diagnose, narrative as _degradation_narrative
from .models import DQDVPeak


def run_analysis(config, dqdv_cfg, repo, project_id: int, verbose: bool = True, batch_name: str | None = None) -> dict:
    experiments_df = repo.list_dqdv_experiments(project_id, batch_name=batch_name)
    out = {}
    for _, row in experiments_df.iterrows():
        sample = row["sample"]
        experiment_id = int(row["experiment_id"])
        cycles_modes = repo.list_dqdv_cycles(experiment_id)
        if cycles_modes.empty:
            continue

        texts = []
        for _, cm in cycles_modes.iterrows():
            peaks_df = repo.load_dqdv_peaks(experiment_id, int(cm["cycle_number"]), cm["mode"])
            peaks = [DQDVPeak(voltage=r["voltage"], intensity=r["intensity"]) for _, r in peaks_df.iterrows()]
            text = _narrative(sample, int(cm["cycle_number"]), cm["mode"], peaks)
            texts.append(text)
            if verbose:
                print(text)

        # Cross-cycle LLI/LAM-like peak tracking — one diagnosis per mode
        # (charge/discharge tracked separately, same as everything else
        # here). See degradation.py's module docstring for what this is
        # and isn't a substitute for.
        degradation = {}
        for mode in sorted(cycles_modes["mode"].unique()):
            result = _diagnose(repo, experiment_id, mode, sample)
            degradation[mode] = result
            text = _degradation_narrative(result)
            texts.append(text)
            if verbose:
                print(text)

        out[sample] = {"narrative": "\n".join(texts), "n_curves": len(cycles_modes), "degradation": degradation}

    return out
