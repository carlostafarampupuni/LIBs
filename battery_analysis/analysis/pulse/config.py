"""
YAML handling shared by tafel.yaml and gitt.yaml (dict configs, like xps.yaml):

    settings: {...}                 # shared defaults
    figures:  {...}
    default_batch: Batch1
    batches:
      Batch1:
        settings: {...}             # optional per-batch overrides
        samples:
          Big:
            folder: Tafel/Big       # relative to Data/ (config.yaml paths.data_root)
            particle: {...} | area_cm2: ...
            ...                     # any setting can also be overridden per sample
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Dict, Optional


def load_yaml(path) -> dict:
    import yaml
    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg["_path"] = str(path)
    return cfg


def batch_names(cfg: dict) -> list:
    return list((cfg.get("batches") or {}).keys())


def default_batch(cfg: dict) -> str:
    names = batch_names(cfg)
    if not names:
        raise KeyError(f"No batches in {cfg.get('_path')}")
    return cfg.get("default_batch") if cfg.get("default_batch") in names else names[0]


def batch_samples(cfg: dict, batch: Optional[str], data_root) -> Dict[str, dict]:
    """
    {sample: merged spec} for one batch: global settings < batch settings <
    sample entries, with `folder` resolved against Data/. Samples whose line is
    commented out simply aren't there.
    """
    name = batch or default_batch(cfg)
    b = (cfg.get("batches") or {}).get(name)
    if b is None:
        raise KeyError(f"Batch {name!r} not in {cfg.get('_path')} (have {batch_names(cfg)})")
    base = {**(cfg.get("settings") or {}), **(b.get("settings") or {})}
    out = {}
    for sample, spec in (b.get("samples") or {}).items():
        merged = copy.deepcopy(base)
        merged.update(spec or {})
        folder = merged.get("folder")
        if folder:
            p = Path(folder)
            merged["folder"] = str(p if p.is_absolute() else Path(data_root) / p)
        ref = merged.get("reference_workbook")
        if ref:
            p = Path(ref)
            merged["reference_workbook"] = str(p if p.is_absolute() else Path(data_root) / p)
        out[sample] = merged
    return out


def state_labels(spec: dict, n: int) -> list:
    """Labels for the n pulse files, in file order: `states: [10, 20, ...]` or start/step."""
    st = spec.get("states")
    if isinstance(st, (list, tuple)):
        labels = list(st)[:n]
        return labels + [f"#{k + 1}" for k in range(len(labels), n)]
    st = st or {}
    start, step = st.get("start", 10), st.get("step", 10)
    return [start + k * step for k in range(n)]
