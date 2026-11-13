"""
Batches for XPS fitting — same idea as every other technique's YAML
(eis.yaml, cycle.yaml, ...): one file holds every batch, so repeat
experiments (Batch1, Batch2, ...) are analysed with the same rules and
tracked separately.

xps.yaml layout:

    settings:   {...}          # shared: background, charge correction, quantification, ...
    figures:    {...}          # shared: figure style
    regions:    {...}          # shared: peak models (chemistry)

    default_batch: Batch1
    auto_discover:             # any Data/XPS/Batch*/ folder becomes a batch automatically
      subfolder: XPS
      pattern: "Batch*"
    batches:
      Batch1:
        source: ../Data/XPS/surface_total.xls     # file / folder / list (relative to the YAML)
        # data_subfolder: XPS/Batch1              # ...or a folder relative to Data/
        mode: auto                                # auto | plot | fit
        samples: [Bare, MxLMP, MxCu, PA800]       # comment a line out to leave a sample out
        regions:                                  # OPTIONAL per-batch changes to the shared models
          F1s: {fit_range: [680, 693]}

batch_config(cfg, "Batch1") returns a full fit config for that batch, so
everything downstream (sources.collect, pubplot, exports) is batch-agnostic.
A YAML without `batches:` still works: it is treated as one batch.
"""
from __future__ import annotations

import copy
import fnmatch
from pathlib import Path
from typing import Dict, List, Optional

from . import fitting as xf


def _raw(cfg: dict) -> dict:
    return cfg.get("raw", {}) or {}


def list_batches(cfg: dict, data_root=None) -> Dict[str, dict]:
    """{batch name: batch settings} — explicit batches plus auto-discovered folders."""
    raw = _raw(cfg)
    batches = {k: dict(v or {}) for k, v in (raw.get("batches") or {}).items()}
    if not batches:                                   # old single-batch YAML
        name = raw.get("default_batch") or "Batch1"
        batches[name] = {}
    ad = raw.get("auto_discover") or {}
    if ad and data_root is not None:
        folder = Path(data_root) / ad.get("subfolder", "XPS")
        pattern = ad.get("pattern", "Batch*")
        if folder.is_dir():
            for d in sorted(p for p in folder.iterdir() if p.is_dir()):
                if fnmatch.fnmatch(d.name, pattern) and d.name not in batches:
                    batches[d.name] = {"source": str(d), "_auto": True}
    return batches


def default_batch(cfg: dict, data_root=None) -> str:
    raw = _raw(cfg)
    names = list(list_batches(cfg, data_root))
    return raw.get("default_batch") if raw.get("default_batch") in names else names[0]


def batch_config(cfg: dict, batch: Optional[str] = None, data_root=None) -> dict:
    """A complete fit config for one batch (shared settings + that batch's overrides)."""
    batches = list_batches(cfg, data_root)
    name = batch or default_batch(cfg, data_root)
    if name not in batches:
        raise KeyError(f"XPS batch '{name}' not found. Available: {list(batches)}")
    b = batches[name]

    out = copy.deepcopy({k: v for k, v in cfg.items() if k != "regions"})
    out["regions"] = dict(cfg["regions"])
    st = out["settings"] = dict(cfg["settings"])
    base = Path(cfg.get("path", ".")).parent

    if b.get("data_subfolder") and data_root is not None:
        st["source"] = str(Path(data_root) / b["data_subfolder"])
    elif b.get("source") is not None:
        src = b["source"]
        st["source"] = [str(base / s) if not Path(s).is_absolute() else s for s in src] \
            if isinstance(src, (list, tuple)) else (src if Path(src).is_absolute() else str(base / src))
    for key in ("mode", "survey_source", "n_starts", "monte_carlo_runs", "charge_correction", "sample_names"):
        if key in b:
            st[key] = b[key]

    # samples: list in the batch (comment lines out to hide); None = every sample found
    out["samples"] = list(b["samples"]) if b.get("samples") else None

    # per-batch region overrides: merged over the shared YAML entry, re-parsed
    if b.get("regions"):
        shared_raw = (_raw(cfg).get("regions") or {})
        comp_defaults = (cfg["settings"].get("component_defaults") or {})
        tmp = {"settings": {**cfg["settings"]}, "regions": {}}
        for rname, over in b["regions"].items():
            merged = {**(shared_raw.get(rname) or {}), **(over or {})}
            tmp["regions"][rname] = merged
        parsed = _parse_regions(tmp, comp_defaults)
        out["regions"].update(parsed)

    # survey stack defaults to this batch's samples
    raw = out.setdefault("raw", {})
    figs = raw["figures"] = dict(raw.get("figures") or {})
    sv = figs["survey"] = dict(figs.get("survey") or {})
    stack = sv["stack"] = dict(sv.get("stack") or {})
    if b.get("stack_samples"):                     # batch-specific stack list wins
        stack["samples"] = list(b["stack_samples"])
    elif out["samples"]:                           # otherwise: this batch's samples
        stack["samples"] = out["samples"]

    out["batch"] = name
    return out


def _parse_regions(tmp_cfg: dict, comp_defaults: dict) -> Dict[str, xf.RegionModel]:
    """Re-use load_fit_config's parsing for override dicts (written to a temp YAML)."""
    import tempfile
    import yaml
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        yaml.safe_dump({"settings": {**tmp_cfg["settings"], "component_defaults": comp_defaults},
                        "regions": tmp_cfg["regions"]}, fh, allow_unicode=True)
        p = fh.name
    try:
        return xf.load_fit_config(p)["regions"]
    finally:
        Path(p).unlink(missing_ok=True)


def describe(cfg: dict, data_root=None) -> List[str]:
    lines = []
    for name, b in list_batches(cfg, data_root).items():
        src = b.get("data_subfolder") or b.get("source") or cfg["settings"].get("source")
        tag = " (auto-discovered)" if b.get("_auto") else ""
        lines.append(f"{name}{tag}: source={src}, samples={b.get('samples') or 'all'}")
    return lines
