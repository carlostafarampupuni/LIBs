"""Reads RIC points back from the database and produces the degradation-mechanism narrative."""
from __future__ import annotations

from .analyzer import analyze_sample, narrative as _narrative


def run_analysis(config, ric_cfg, repo, project_id: int, verbose: bool = True, batch_name: str | None = None) -> dict:
    """
    For every representative RIC experiment, load its points from the
    database and run the degradation-mechanism analysis. Returns a dict
    keyed by sample name: {"narrative": str, **analyze_sample() result}.
    """
    experiments_df = repo.list_ric_experiments(project_id, batch_name=batch_name)

    out = {}
    for _, row in experiments_df.iterrows():
        if not row["is_representative"]:
            continue
        df = repo.load_ric_points(row["experiment_id"])
        if df.empty:
            continue

        cycle = df["cycle_number"].to_numpy()
        cc = df["cc"].to_numpy()
        cd = df["cd"].to_numpy()
        ric = df["ric"].to_numpy()
        # ric_disconn/ric_sei are NULL on the last row by construction — drop it.
        ric_disconn = df["ric_disconn"].dropna().to_numpy()
        ric_sei = df["ric_sei"].dropna().to_numpy()

        result = analyze_sample(cycle, cc, cd, ric, ric_disconn, ric_sei, row["sample"], ric_cfg.trend_window)
        text = _narrative(result)
        if verbose:
            print(text)

        out[row["sample"]] = {"narrative": text, **result}

    return out
