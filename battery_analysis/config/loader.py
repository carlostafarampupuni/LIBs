"""
load_config(path) -> ProjectConfig (the shared project-level file), and
load_technique_config(path, model_cls) -> a validated per-technique config
(eis.yaml -> EISTechniqueConfig, drt.yaml -> DRTTechniqueConfig, etc.).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from .schema import ProjectConfig

T = TypeVar("T", bound=BaseModel)


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or fails validation."""


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, "r") as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"Could not parse YAML in {path}:\n{e}") from e
    if raw is None:
        raise ConfigError(f"Config file {path} is empty.")
    return raw


_JUNK_DIR_NAMES = {"__pycache__"}


def _apply_auto_discovery(raw: dict, path: Path, data_root: Optional[Path]) -> dict:
    """
    If the YAML sets an `auto_discover:` block, e.g.

        auto_discover:
          subfolder: EIS          # scan <data_root>/EIS/ for batch folders
          defaults:                # (optional) applied to every discovered batch
            plotting: {...}

    every immediate subfolder of `<data_root>/<subfolder>/` that ISN'T
    already a key under `batches:` gets a batch entry synthesized for it
    automatically (`data_subfolder: "<subfolder>/<foldername>"`, plus
    whatever `defaults:` supplies) — so dropping a brand new batch of raw
    files into that folder is enough on its own; nothing needs to be
    hand-typed into the YAML for it to show up. An explicit `batches:`
    entry with the same name always wins over an auto-discovered one, so
    you can still hand-tune any individual batch's settings.

    `auto_discover:` is popped out before validation — none of the
    technique schema classes need to know about it, they only ever see a
    (possibly larger) `batches:` dict, exactly as if you'd typed every
    entry yourself.
    """
    spec = raw.pop("auto_discover", None)
    if not spec:
        return raw
    if data_root is None:
        # load_technique_config() was called without a data_root (e.g. a
        # standalone script validating a YAML file) — nothing to scan
        # against, so just leave whatever batches: was typed by hand.
        return raw
    subfolder = spec.get("subfolder")
    if not subfolder:
        raise ConfigError(
            f"{path}: auto_discover: needs a 'subfolder:' naming which folder "
            "under Data/ to scan for batches, e.g. 'auto_discover: {subfolder: EIS}'."
        )
    defaults = spec.get("defaults") or {}

    batches = raw.setdefault("batches", {})
    base_dir = data_root / subfolder
    if base_dir.is_dir():
        for entry in sorted(base_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name in _JUNK_DIR_NAMES:
                continue
            if entry.name in batches:
                continue  # an explicit batches: entry for this name always wins
            batches[entry.name] = {**defaults, "data_subfolder": f"{subfolder}/{entry.name}"}
    return raw


def _resolve_labbook_refs(value, labbook_dir: Optional[Path]):
    """
    Replace every string value of the form

        labbook:<entry id>/<electrode name>            -> its am_mass_g
        labbook:<entry id>/<electrode name>.<field>    -> any computed field

    with the number from that lab-book entry (LabBook/entries/), e.g.

        active_mass_g: labbook:2026-10-24a/P1-C2

    so a mass you weighed once at the bench is never typed in twice.
    Walks dicts and lists recursively; anything else is left alone.
    """
    if labbook_dir is None:
        return value
    if isinstance(value, dict):
        return {k: _resolve_labbook_refs(v, labbook_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_labbook_refs(v, labbook_dir) for v in value]
    if isinstance(value, str) and value.startswith("labbook:"):
        from battery_analysis.labbook.book import LabBook
        try:
            return LabBook(labbook_dir).lookup(value[len("labbook:"):])
        except (KeyError, FileNotFoundError) as e:
            raise ConfigError(f"Could not resolve {value!r}: {e}") from e
    return value


def load_config(path: str | Path, base_dir: Optional[str | Path] = None) -> ProjectConfig:
    """
    Load and validate a project's shared config.yaml (project, paths,
    cell_defaults, palette, samples). Raises ConfigError with a readable
    message (not a raw KeyError deep inside a plotting call) if the file
    is missing or malformed.

    base_dir: the folder relative paths in the YAML (data_root, database,
    figures_root, ...) resolve against. Defaults to the config file's own
    folder; Project passes the project root, since config.yaml now sits
    in notebooks/ next to the notebooks that use it.
    """
    path = Path(path)
    raw = _read_yaml(path)
    try:
        config = ProjectConfig(**raw)
    except ValidationError as e:
        raise ConfigError(
            f"config.yaml at {path} has {e.error_count()} problem(s):\n{e}"
        ) from e

    # Private attribute set after construction so relative paths in the
    # YAML (data_root, database, figures_root, ...) resolve against the
    # config file's own folder (or base_dir), not the notebook's current
    # working dir.
    config._config_dir = Path(base_dir) if base_dir is not None else path.parent
    return config


def load_technique_config(path: str | Path, model_cls: Type[T],
                           data_root: Optional[Path] = None,
                           labbook_dir: Optional[Path] = None) -> T:
    """
    Load and validate one technique's own YAML file (eis.yaml, drt.yaml,
    cycle.yaml, rate.yaml, ...) against its schema class. The file's
    top-level keys ARE that model's fields directly — no wrapping key.

    data_root (this project's resolved Data/ folder) is only used if the
    YAML has an `auto_discover:` block — see _apply_auto_discovery() —
    to turn new raw-data folders into batches automatically. Pass None
    (the default) for files that don't need it; auto_discover: is simply
    ignored in that case rather than erroring.

    labbook_dir (this project's LabBook/ folder) lets any value in the
    YAML be a `labbook:<entry>/<electrode>` reference — see
    _resolve_labbook_refs().
    """
    path = Path(path)
    raw = _read_yaml(path)
    raw = _apply_auto_discovery(raw, path, data_root)
    raw = _resolve_labbook_refs(raw, labbook_dir)
    try:
        return model_cls(**raw)
    except ValidationError as e:
        raise ConfigError(
            f"{path} has {e.error_count()} problem(s):\n{e}"
        ) from e
