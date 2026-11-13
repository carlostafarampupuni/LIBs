"""
Raw SEM images -> experiment + sem_metric rows in the database.

One `experiment` row per (sample, role, image) — mirrors Cycle/EIS's "one
experiment per raw replicate file" pattern, with source_folder set to the
image's path (relative to data_root) so it's unique per image and every
individual photo is independently browsable via describe_experiment().
replicate_number is the image's subindex within its position group; the
FIRST subindex for a given (sample, role) is marked representative.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from battery_analysis.config.schema import ProjectConfig, SEMBatchConfig, SEMRoleGroup
from battery_analysis.database.repository import ProjectRepository
from battery_analysis.discovery import color_for
from .discovery import discover_role_images, pixel_size_um, DiscoveredSemImage
from .quantify import sem_full_quantification, particle_size_distribution


def _experiment_date(config: ProjectConfig, image_path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(image_path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    except OSError:
        return None


def _scale_um_per_px(image: DiscoveredSemImage, width_px: int, cfg: SEMBatchConfig) -> tuple[float, str]:
    """Prefer the instrument sidecar's real per-image calibration; fall
    back to the crude scale-bar-is-25%-of-width guess otherwise."""
    if cfg.use_sidecar_scale:
        sidecar_scale = pixel_size_um(image.sidecar)
        if sidecar_scale is not None:
            return sidecar_scale, "sidecar"
    return cfg.scale_um_fallback / (width_px * 0.25), "fallback"


def _manual_as_discovered(manual, data_dir: Path, default_sample: str | None, index: int) -> DiscoveredSemImage:
    from .discovery import parse_sem_sidecar
    path = (data_dir / manual.file).resolve() if not Path(manual.file).is_absolute() else Path(manual.file)
    sidecar_path = path.with_suffix(".txt")
    sidecar = parse_sem_sidecar(sidecar_path) if sidecar_path.exists() else None
    return DiscoveredSemImage(
        sample_name=manual.sample, position=0, subindex=index + 1, path=path,
        sidecar_path=sidecar_path if sidecar_path.exists() else None, sidecar=sidecar,
    )


def _import_role(config: ProjectConfig, cfg: SEMBatchConfig, group: SEMRoleGroup, role: str,
                  data_dir: Path, batch_name: str, repo: ProjectRepository, project_id: int,
                  full_quant: bool) -> int:
    if not group.enabled:
        return 0

    images = list(discover_role_images(data_dir, group.folder, group.default_sample))
    manual_root = config.resolve(config.paths.data_root)
    images += [
        _manual_as_discovered(m, manual_root, group.default_sample, i)
        for i, m in enumerate(group.manual_images)
    ]
    if not images:
        return 0

    # First subindex per sample is representative.
    first_subindex: dict[str, int] = {}
    for img in images:
        first_subindex[img.sample_name] = min(first_subindex.get(img.sample_name, img.subindex), img.subindex)

    sample_colors: dict[str, str] = {}
    total = 0
    for img in images:
        if img.sample_name not in sample_colors:
            manual_color = next(
                (m.color for m in group.manual_images if m.sample == img.sample_name and m.color), None
            )
            global_override = config.sample_override(img.sample_name)
            sample_colors[img.sample_name] = (
                manual_color
                or (global_override.color if global_override and global_override.color else None)
                or color_for(len(sample_colors), config.palette or None)
            )
        color = sample_colors[img.sample_name]
        cell = config.cell_metadata_for(img.sample_name)
        sample_id = repo.get_or_create_sample(project_id, img.sample_name, color=color)

        bgr = cv2.imread(str(img.path))
        if bgr is None:
            print(f"⚠️  Could not load {img.path}")
            continue
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        scale, scale_source = _scale_um_per_px(img, gray.shape[1], cfg)

        override = group.override_for(img.sample_name)
        manual = next((m for m in group.manual_images if m.sample == img.sample_name), None)
        crop = (override.crop if override else None) or (manual.crop if manual else None)
        if crop is not None:
            top, bottom = crop
            gray = gray[top:bottom, :]
        gap_params = (override.gap_params if override else None) or (manual.gap_params if manual else None)

        try:
            relative_path = str(img.path.relative_to(config.resolve(config.paths.data_root)))
        except ValueError:
            relative_path = str(img.path)

        experiment_id = repo.get_or_create_experiment(
            project_id, sample_id, technique="sem",
            source_folder=relative_path, batch_name=batch_name,
            replicate_number=img.subindex,
            is_representative=(img.subindex == first_subindex[img.sample_name]),
            cathode=cell.cathode, anode=cell.anode, cell_type=cell.cell_type,
            electrolyte=cell.electrolyte, additives=cell.additives,
            temperature_c=cell.temperature_c,
            experiment_date=_experiment_date(config, img.path),
            notes=cell.notes,
        )
        repo.clear_sem_metrics(experiment_id)
        repo.save_raw_file(experiment_id, str(img.path))

        if full_quant:
            detect_params = gap_params.model_dump() if gap_params else None
            result = sem_full_quantification(gray, scale, detect_gaps_params=detect_params)
            metrics = {k: v for k, v in result.items()
                       if k not in ("binary_gaps", "gap_distribution", "particle_diameters")}
            metrics["scale_um_per_px"] = scale
            metrics["scale_source"] = 1.0 if scale_source == "sidecar" else 0.0
            repo.save_sem_metrics(experiment_id, role, relative_path, metrics)
            repo.save_sem_array(experiment_id, role, relative_path, "particle_diameters", result["particle_diameters"])
        else:
            # Powder role: particle sizing only, matching the original script.
            diam = particle_size_distribution(gray, scale, min_area=50)
            metrics = {
                "mean_particle_um": float(np.mean(diam)) if len(diam) else 0.0,
                "median_particle_um": float(np.median(diam)) if len(diam) else 0.0,
                "std_particle_um": float(np.std(diam)) if len(diam) else 0.0,
                "scale_um_per_px": scale,
                "scale_source": 1.0 if scale_source == "sidecar" else 0.0,
            }
            repo.save_sem_metrics(experiment_id, role, relative_path, metrics)
            repo.save_sem_array(experiment_id, role, relative_path, "particle_diameters", diam)

        total += 1

    return total


def import_sem(config: ProjectConfig, sem_cfg: SEMBatchConfig, repo: ProjectRepository, project_id: int) -> int:
    """Import every enabled role group (cross_section, surface, powder) for this batch."""
    data_dir = config.resolve(config.paths.data_root) / sem_cfg.data_subfolder
    batch_name = Path(sem_cfg.data_subfolder).name

    total = 0
    total += _import_role(config, sem_cfg, sem_cfg.cross_section, "cross_section", data_dir, batch_name, repo, project_id, full_quant=True)
    total += _import_role(config, sem_cfg, sem_cfg.surface, "surface", data_dir, batch_name, repo, project_id, full_quant=True)
    total += _import_role(config, sem_cfg, sem_cfg.powder, "powder", data_dir, batch_name, repo, project_id, full_quant=False)

    if total == 0:
        print(f"⚠️  No SEM images found/imported under {data_dir}")
    else:
        print(f"✅  Imported {total} SEM image(s) into the database.")
    return total
