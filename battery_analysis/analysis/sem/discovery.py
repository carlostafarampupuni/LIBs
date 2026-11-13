"""
Auto-discovery for SEM raw images.

Real filenames look like "#<position>-<subindex><SampleName>.jpg", e.g.
"#1-1MXCu.jpg" (position 1, image 1, sample "MXCu") or "#5-3BareLMP.jpg"
(position 5, image 3, sample "BareLMP"). The trailing sample-name text is
optional — some sessions (e.g. a single-sample folder like "LiCu") have
no text at all ("#1-1.jpg"); in that case every image in the folder
belongs to one sample, supplied by the caller (SEMRoleGroup.default_sample,
falling back to the batch key itself).

Each image OPTIONALLY has a matching ".txt" sidecar — an INI-style export
from the SEM instrument (observed: Hitachi SU8200) containing an accurate
per-image PixelSize in nm/pixel. When present this is far more accurate
than any manual scale-bar guess and is used automatically; discover_sem_images()
just surfaces the parsed sidecar (or None) and leaves the choice of which
scale to actually use to the importer (config.use_sidecar_scale).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_IMAGE_PATTERN = re.compile(r"^#(?P<position>\d+)-(?P<sub>\d+)(?P<sample>.*)$")
_IMAGE_EXTS = (".jpg", ".jpeg", ".tif", ".tiff", ".png")


@dataclass
class DiscoveredSemImage:
    sample_name: str
    position: int
    subindex: int
    path: Path
    sidecar_path: Optional[Path]
    sidecar: Optional[Dict[str, str]] = None

    @property
    def replicate_number(self) -> int:
        return self.subindex


def parse_sem_sidecar(path: Path) -> Dict[str, str]:
    """
    Parse a SEM instrument's INI-style ".txt" export (a "[SemImageFile]"
    section of flat Key=Value lines) into a plain dict. Unknown/malformed
    lines are skipped rather than raising — a sidecar with a slightly
    different shape from a different instrument should still yield
    whatever keys it does have instead of failing the whole import.
    """
    out: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def pixel_size_um(sidecar: Optional[Dict[str, str]]) -> Optional[float]:
    """PixelSize in the sidecar is nm/pixel; return µm/pixel, or None if unavailable/unparseable."""
    if not sidecar or "PixelSize" not in sidecar:
        return None
    try:
        return float(sidecar["PixelSize"]) / 1000.0
    except (TypeError, ValueError):
        return None


def _find_role_folder(batch_dir: Path, folder_hint: Optional[str]) -> Optional[Path]:
    """Match folder_hint against batch_dir's subfolders case-insensitively —
    real folders vary in casing ("Cross Section" vs "Cross section")."""
    if folder_hint is None:
        return None
    if not batch_dir.exists():
        return None
    target = folder_hint.strip().lower()
    for sub in batch_dir.iterdir():
        if sub.is_dir() and sub.name.strip().lower() == target:
            return sub
    return None


def discover_role_images(batch_dir: Path, folder_hint: Optional[str],
                          default_sample: Optional[str]) -> List[DiscoveredSemImage]:
    """
    Discover every image under batch_dir/folder_hint (case-insensitive
    match), grouped by the '#<position>-<subindex><Sample>.jpg' filename
    convention. Returns [] if the role folder doesn't exist.
    """
    role_dir = _find_role_folder(Path(batch_dir), folder_hint)
    if role_dir is None:
        return []

    out: List[DiscoveredSemImage] = []
    for path in sorted(role_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_EXTS:
            continue
        m = _IMAGE_PATTERN.match(path.stem)
        if m:
            position = int(m.group("position"))
            subindex = int(m.group("sub"))
            sample = m.group("sample").strip() or default_sample or path.parent.parent.name
        else:
            # Doesn't follow the convention at all — treat as a single
            # standalone image of the default/fallback sample so it's
            # still importable rather than silently skipped.
            position, subindex = 0, 1
            sample = default_sample or path.stem

        sidecar_path = path.with_suffix(".txt")
        sidecar = parse_sem_sidecar(sidecar_path) if sidecar_path.exists() else None
        out.append(DiscoveredSemImage(
            sample_name=sample, position=position, subindex=subindex,
            path=path, sidecar_path=sidecar_path if sidecar_path.exists() else None,
            sidecar=sidecar,
        ))
    return out
