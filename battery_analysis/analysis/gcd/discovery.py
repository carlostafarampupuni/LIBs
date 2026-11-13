"""
Auto-discovery for GCD raw files. Filenames look like:

    [LiLi-GCD1-PA800-2_024].csv          -> sample "PA800",      replicate 2
    [LiLi-GCD0.5-Bare-1_014].csv         -> sample "Bare",       replicate 1
    [GCDOpt-2.5%CFMP-2_015].csv          -> sample "2.5%CFMP",   replicate 2
    [GCD 0.5_1-2.5%MXLMP-1_003].csv      -> sample "2.5%MXLMP",  replicate 1

Loading-optimization runs name the sample with a leading percentage, e.g.
"2.5%CFMP"/"5%CFMP" for different mass-loading fractions of the same
material — the sample token can start with digits/a decimal point/a "%"
before the letters, not just letters, so those come through as their own
distinct samples instead of being silently skipped as unmatched files.

The folder name itself (GCD1, GCD0.5, ...) encodes the current density
in mA/cm^2 — used as the analyze_gcd default unless overridden in YAML.

NEWARE exports (Na‖Al / Na‖Cu, BTSDA .xlsx/.csv or native .ndax) are
named <sample>-<n>:

    SampleA-1.xlsx                   -> sample "SampleA", replicate 1

config.yaml's sample `aliases:` (or gcd.yaml's `sample_names:`) turn the file-name sample into the name shown
everywhere (SampleA -> Bare Al). Every file in the folder is imported;
`cells:` only sets the order (and which cells the analysis and plots use —
see report.selected_experiments).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_GCD_PATTERN = re.compile(r"[-\s]([\d.]*%?[A-Za-z]+\d*)-(\d+)_\d+\]\.csv$")   # "-Bare-1_001]" or " Bare-1_025]"
_NEWARE_PATTERN = re.compile(r"^(.+?)[-_ ]?(\d+)$")            # file stem
_NEWARE_EXTS = {".xlsx", ".xls", ".ndax", ".nda"}
_CURRENT_DENSITY_PATTERN = re.compile(r"GCD([\d.]+)", re.IGNORECASE)


@dataclass
class DiscoveredGCDFile:
    sample_name: str
    replicate_number: int
    path: Path
    order: int = 0               # position in gcd.yaml's cells: list (unlisted cells come after)


@dataclass
class DiscoveredGCDSample:
    name: str
    files: List[DiscoveredGCDFile] = field(default_factory=list)

    @property
    def default_representative(self) -> Optional[DiscoveredGCDFile]:
        if not self.files:
            return None
        return min(self.files, key=lambda f: (f.order, f.replicate_number))


def _match(path: Path):
    """(file-name sample, replicate) for a Zive or NEWARE raw file, else None."""
    if path.suffix.lower() == ".csv":
        m = _GCD_PATTERN.search(path.name)
        if m:
            return m.group(1), int(m.group(2))
        from .io import is_neware_csv
        if not is_neware_csv(path):
            return None
    elif path.suffix.lower() not in _NEWARE_EXTS:
        return None
    m = _NEWARE_PATTERN.match(path.stem)
    return (m.group(1), int(m.group(2))) if m else (path.stem, 1)


def discover_gcd_files(data_dir, cells=(), sample_names=None) -> List[DiscoveredGCDSample]:
    """
    Every raw file in data_dir, commented-out cells included, so the
    database always holds all the data. cells: file stems in the order
    you want them (unlisted files come after); the first listed cell of a
    sample becomes its representative. sample_names: file-name sample ->
    display name.
    """
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return []
    sample_names = sample_names or {}
    wanted = {c: i for i, c in enumerate(cells)}

    # The same cell exported twice (e.g. BareAl_Ref-1.ndax AND BareAl_Ref-1.xlsx) is ONE
    # cell: keep a single file per name, preferring the native .ndax (reads ~10x faster,
    # same data -- checked on Batch2), then .nda, then Excel/CSV.
    _pref = {".ndax": 0, ".nda": 1, ".xlsx": 2, ".xls": 3, ".csv": 4}
    chosen: Dict[str, Path] = {}
    for path in sorted(data_dir.iterdir()):
        if path.name.startswith(("~$", ".")) or path.suffix.lower() not in _pref:
            continue
        best = chosen.get(path.stem)
        if best is None or _pref[path.suffix.lower()] < _pref[best.suffix.lower()]:
            chosen[path.stem] = path

    samples: Dict[str, DiscoveredGCDSample] = {}
    for path in sorted(chosen.values()):
        hit = _match(path)
        if not hit:
            continue
        raw_name, replicate_number = hit
        sample_name = sample_names.get(raw_name, raw_name)
        sample = samples.setdefault(sample_name, DiscoveredGCDSample(name=sample_name))
        sample.files.append(DiscoveredGCDFile(sample_name, replicate_number, path, wanted.get(path.stem, len(wanted))))

    missing = [c for c in wanted if not any(f.path.stem == c for s in samples.values() for f in s.files)]
    if missing:
        print(f"⚠️  cells listed in gcd.yaml but not found in {data_dir}: {missing}")

    for sample in samples.values():
        sample.files.sort(key=lambda f: (f.order, f.replicate_number))

    if wanted:   # keep the order the cells are listed in
        return sorted(samples.values(), key=lambda s: min(f.order for f in s.files))
    return sorted(samples.values(), key=lambda s: s.name)


def current_density_from_folder(folder_name: str) -> Optional[float]:
    """e.g. "GCD1" -> 1.0, "GCD0.5" -> 0.5"""
    m = _CURRENT_DENSITY_PATTERN.search(folder_name)
    return float(m.group(1)) if m else None


def resolve_gcd_folder(data_subfolder) -> tuple[Optional[float], str]:
    """
    Your raw Data/GCD/ layout has an extra nesting level current_density_
    from_folder() alone doesn't handle: each density/label folder (GCD1,
    GCD0.5, GCDRate, CE10, GCDOptCFLMP, ...) holds one or more RUN
    subfolders of its own (Batch1, Batch2, ...) that hold the actual csv
    files, e.g.:

        GCD/GCD1/Batch1/*.csv
        GCD/GCD1/Batch2/*.csv
        GCD/GCD0.5/Batch1/*.csv
        GCD/GCDRate/Batch1/*.csv

    ...while others have no run-folder nesting at all and hold their csv
    files directly:

        GCD/CE10/*.csv
        GCD/GCDOptCFLMP/*.csv

    A gcd.yaml batch's data_subfolder has to point at whichever of those
    is the actual folder holding the csv files — a run folder like
    "GCD/GCD1/Batch1", or a flat label folder like "GCD/CE10" — which
    means the folder's own *immediate* name is sometimes the run folder
    ("Batch1"), not the label ("GCD1"). Two things break if that's not
    accounted for: current_density_from_folder(data_subfolder.name) can't
    find "GCD1" in "Batch1" (current density can't auto-detect), and
    using that bare immediate name as this experiment's database
    batch_name collides every label's "Batch1" run into the exact same
    batch_name — silently merging GCD1/Batch1, GCD0.5/Batch1 and
    GCDRate/Batch1 in the database and in this project's batch selectors.

    Fix: fold in the parent folder's name whenever this folder is nested
    one level under something other than the GCD technique root itself
    (i.e. its parent isn't literally named "GCD") — regardless of
    whether that parent name happens to encode a numeric density
    ("GCDRate" doesn't, but its runs still need "GCDRate_Batch1", not a
    bare "Batch1" that collides with every other label's Batch1). A
    label folder used directly, with no run-folder nesting (own name
    already encodes a density, e.g. "GCD/GCD1" with no Batch1/Batch2
    underneath, OR the parent IS the GCD root, e.g. "GCD/CE10"), keeps
    just its own name.

    Returns (current_density_mA_cm2 or None, batch_name).
    """
    path = Path(data_subfolder)
    own_density = current_density_from_folder(path.name)
    if own_density is not None:
        # This folder's own name already encodes a density and is used
        # directly (no separate run-folder nesting), e.g. "GCD/GCD1".
        return own_density, path.name

    parent_name = path.parent.name
    parent_is_root = parent_name.strip().lower() == "gcd"
    if not parent_is_root:
        # Nested one level under a named label folder (a run folder like
        # "Batch1" under "GCD1"/"GCD0.5"/"GCDRate"/...) — always fold
        # both names together, whether or not the parent's name itself
        # encodes a numeric density.
        return current_density_from_folder(parent_name), f"{parent_name}_{path.name}"

    # Parent IS the GCD root — a flat label folder with no run-folder
    # nesting and no auto-detectable density, e.g. "GCD/CE10",
    # "GCD/GCDOptCFLMP". Its own name is already unique.
    return None, path.name
