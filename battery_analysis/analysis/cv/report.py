"""Narrative summary of imported CV sweeps, for project.story()."""
from __future__ import annotations


def run_analysis(config, cv_cfg, repo, project_id: int, verbose: bool = True, batch_name: str | None = None) -> dict:
    exps = repo.list_cv_experiments(project_id, batch_name=batch_name)
    out = {}
    if exps.empty:
        return out
    for sample in sorted(exps["sample"].unique()):
        rows = exps[exps["sample"] == sample]
        rates = set()
        for _, row in rows.iterrows():
            rates.update(repo.list_cv_scan_rates(int(row["experiment_id"])))
        rates = sorted(rates)
        text = f"**{sample}**\n- Scan rates imported: {', '.join(str(r) for r in rates)}"
        if verbose:
            print(text)
        out[sample] = {"narrative": text}
    return out
