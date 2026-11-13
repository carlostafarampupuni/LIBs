"""Narrative summary of imported FTIR spectra, for project.story()."""
from __future__ import annotations


def run_analysis(config, ftir_cfg, repo, project_id: int, verbose: bool = True, batch_name: str | None = None) -> dict:
    exps = repo.list_ftir_experiments(project_id, batch_name=batch_name)
    out = {}
    if exps.empty:
        return out
    for sample in sorted(exps["sample"].unique()):
        rows = exps[exps["sample"] == sample]
        spectrum_types = []
        n_points = 0
        for _, row in rows.iterrows():
            df = repo.load_ftir_points(int(row["experiment_id"]))
            if df.empty:
                continue
            spectrum_types.extend(df["spectrum_type"].unique().tolist())
            n_points += len(df)
        spectrum_types = sorted(set(spectrum_types))
        text = f"**{sample}**\n- {', '.join(spectrum_types) or 'spectrum'} imported ({n_points} points)"
        if verbose:
            print(text)
        out[sample] = {"narrative": text}
    return out
