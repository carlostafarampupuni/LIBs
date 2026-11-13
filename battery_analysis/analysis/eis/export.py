"""
Optional database -> Excel export. This is a one-way convenience for
handoffs (sending a colleague a spreadsheet, archiving a snapshot) — it
is NOT part of the pipeline anymore. import_eis/fit_eis/analyze_eis/
plot_eis all read and write the database directly; nothing needs to
round-trip through Excel.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def export_eis_to_excel(
    config, repo, project_id: int, filename: str = "EIS_export.xlsx", batch_name: str | None = None
) -> Path:
    """
    Write two sheets to exports/<filename>:
      - 'fit_results': the wide fit/extraction table (Sheet, Sample, Cycle, Batch, params...)
      - 'experiments': every experiment's full metadata (chemistry, electrolyte, dates, ...)

    Scoped to batch_name by default so the export reflects the batch this
    config targets, not every batch ever imported into this project.
    """
    out_dir = config.resolve(config.paths.exports_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    fit_df = repo.load_eis_fit_results(project_id, batch_name=batch_name, representative_only=False)  # every trial
    experiments_df = repo.find_experiments(project_id, technique="eis")
    if batch_name is not None and not experiments_df.empty:
        experiments_df = experiments_df[experiments_df["batch_name"] == batch_name]

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        (fit_df if not fit_df.empty else pd.DataFrame()).to_excel(writer, sheet_name="fit_results", index=False)
        experiments_df.to_excel(writer, sheet_name="experiments", index=False)

    print(f"✅  Exported → {out_path}")
    return out_path
