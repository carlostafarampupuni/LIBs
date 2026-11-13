"""Narrative summary of imported SEM metrics, for project.story()."""
from __future__ import annotations

from .plot import sample_metric_means


def _quality_note(score: float) -> str:
    if score >= 90:
        return "excellent interface contact"
    if score >= 70:
        return "good interface contact"
    if score >= 50:
        return "moderate interface degradation"
    return "significant delamination/gap formation"


def run_analysis(config, sem_cfg, repo, project_id: int, verbose: bool = True, batch_name: str | None = None) -> dict:
    """
    Per-sample narrative combining whichever roles (cross_section/surface/
    powder) actually have imported data. Returns a dict keyed by sample
    name: {"narrative": str}.
    """
    cross = sample_metric_means(repo, project_id, batch_name, "cross_section")
    surface = sample_metric_means(repo, project_id, batch_name, "surface")
    powder = sample_metric_means(repo, project_id, batch_name, "powder")

    samples = set()
    for df in (cross, surface, powder):
        if not df.empty:
            samples.update(df["Sample"].tolist())

    out = {}
    for sample in sorted(samples):
        lines = [f"**{sample}**"]
        if not cross.empty and sample in cross["Sample"].values:
            r = cross[cross["Sample"] == sample].iloc[0]
            lines.append(
                f"- Cross-section: {r['contact_area_pct']:.1f}% contact area, "
                f"{r['mean_gap_um']:.2f} µm mean gap, quality {r['quality_score']:.0f}/100 "
                f"({_quality_note(r['quality_score'])})"
            )
        if not surface.empty and sample in surface["Sample"].values:
            r = surface[surface["Sample"] == sample].iloc[0]
            lines.append(
                f"- Surface: {r['porosity_pct']:.1f}% porosity, "
                f"{r['mean_particle_um']:.2f} ± {r['std_particle_um']:.2f} µm particle size"
            )
        if not powder.empty and sample in powder["Sample"].values:
            r = powder[powder["Sample"] == sample].iloc[0]
            lines.append(f"- Powder: {r['mean_particle_um']:.2f} ± {r['std_particle_um']:.2f} µm particle size")

        text = "\n".join(lines)
        if verbose:
            print(text)
        out[sample] = {"narrative": text}

    return out
