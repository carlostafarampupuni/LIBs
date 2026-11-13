"""
Install (or update) the XPS workflow in a project — the same files in every
project, from one source (this package's templates/ folder).

    from battery_analysis.analysis.xps.template import install
    install("path/to/Project/Results")          # the folder that holds notebooks/ (or config.yaml)

Result:
    <yaml dir>/xps.yaml                         peak models, figure style, batches
    <notebooks dir>/xps.ipynb                   fit / plot / export, per batch
    <notebooks dir>/xps_validate.ipynb          one-time check against KherveFitting
    <notebooks dir>/XPS_validation_checklist.md
    Data/XPS/Batch1/                            drop the batch's files here
<yaml dir> is wherever the project's config.yaml lives (what Project() uses).

Safe to run again at any time:
  * a current xps.yaml / xps.ipynb is never touched (your peak models stay yours)
  * an OLD database-style xps.yaml / xps.ipynb is replaced by the new one
  * an xps_fit.yaml from the earlier naming becomes xps.yaml (keeps its chemistry);
    leftover xps_fit.* files are removed
  * an existing validation notebook keeps its VALIDATE list when upgraded
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

TEMPLATES = Path(__file__).parent / "templates"


def _locate(project_dir: Path):
    for cand in (project_dir / "notebooks" / "config.yaml", project_dir / "config.yaml"):
        if cand.exists():
            yaml_dir = cand.parent
            nb_dir = project_dir / "notebooks" if (project_dir / "notebooks").is_dir() else yaml_dir
            return yaml_dir, nb_dir
    raise FileNotFoundError(f"No config.yaml in {project_dir} or {project_dir / 'notebooks'}")


def _is_new_yaml(path: Path) -> bool:
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return False
    return isinstance(data, dict) and "regions" in data


def _nb_text(path: Path) -> str:
    try:
        return "\n".join("".join(c.get("source", "")) for c in json.loads(path.read_text(encoding="utf-8"))["cells"])
    except Exception:  # noqa: BLE001
        return ""


def _is_new_notebook(path: Path) -> bool:
    """Current XPS notebooks use the batch API (fit_xps / xps_config); the old
    database-route xps.ipynb used import_xps(), so it does not qualify."""
    t = _nb_text(path)
    return ("fit_xps(" in t or "xps_config(" in t) and "import_xps(" not in t and "xps_fit" not in t


def _existing_validate(path: Path) -> Optional[list]:
    if not path.exists():
        return None
    m = re.search(r"VALIDATE = (\[[^\]\n]*\])", _nb_text(path))
    try:
        v = ast.literal_eval(m.group(1)) if m else None
    except (ValueError, SyntaxError):
        return None
    return v or None


def install(project_dir, validate: Optional[List[tuple]] = None, overwrite: Sequence[str] = (),
            create_batch_folder: bool = True, dry_run: bool = False) -> Dict[str, str]:
    """
    Install/update the XPS workflow in one project. `validate` pre-fills the
    validation notebook's VALIDATE list, e.g. [("Bare", "F1s")]; `overwrite`
    lists file names to replace even if current. Returns {path: action}.
    """
    import yaml
    project_dir = Path(project_dir).resolve()
    yaml_dir, nb_dir = _locate(project_dir)
    actions: Dict[str, str] = {}

    def do(path: Path, msg: str, fn):
        actions[str(path)] = msg
        if not dry_run:
            fn()

    def copy_template(name: str, dest: Path, transform=None):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(transform(text) if transform else text, encoding="utf-8")

    # --- xps.yaml -------------------------------------------------------
    y, legacy = yaml_dir / "xps.yaml", yaml_dir / "xps_fit.yaml"
    if y.exists() and _is_new_yaml(y) and "xps.yaml" not in overwrite:
        actions[str(y)] = "kept (current)"
        if legacy.exists():
            do(legacy, "removed (old name)", legacy.unlink)
    elif legacy.exists() and _is_new_yaml(legacy):
        do(y, "migrated from xps_fit.yaml (your peak models kept)", lambda: legacy.replace(y))
    else:
        do(y, "replaced old xps.yaml" if y.exists() else "created", lambda: copy_template("xps.yaml", y))
        if legacy.exists():
            do(legacy, "removed (old name)", legacy.unlink)

    # --- notebooks --------------------------------------------------------
    nb = nb_dir / "xps.ipynb"
    if nb.exists() and _is_new_notebook(nb) and "xps.ipynb" not in overwrite:
        actions[str(nb)] = "kept (current)"
    else:
        do(nb, "replaced old xps.ipynb" if nb.exists() else "created", lambda: copy_template("xps.ipynb", nb))
    old_nb = nb_dir / "xps_fit.ipynb"
    if old_nb.exists():
        do(old_nb, "removed (old name)", old_nb.unlink)

    val = nb_dir / "xps_validate.ipynb"
    keep = list(validate) if validate else (_existing_validate(val) or [])
    if val.exists() and _is_new_notebook(val) and "xps_validate.ipynb" not in overwrite:
        actions[str(val)] = "kept (current)"
    else:
        do(val, ("updated" if val.exists() else "created") + f" (VALIDATE = {keep})",
           lambda: copy_template("xps_validate.ipynb", val, lambda t: t.replace("__VALIDATE__", repr(keep))))

    chk = nb_dir / "XPS_validation_checklist.md"
    if chk.exists() and "xps_fit" not in chk.read_text(encoding="utf-8") and chk.name not in overwrite:
        actions[str(chk)] = "kept (current)"
    else:
        do(chk, "updated" if chk.exists() else "created",
           lambda: copy_template("XPS_validation_checklist.md", chk))

    # --- Data/XPS/Batch1 ----------------------------------------------------
    # only when Batch1 actually reads from Data/XPS/Batch1 (not when it points at a file elsewhere)
    final_yaml = y if y.exists() and not dry_run else (legacy if legacy.exists() else TEMPLATES / "xps.yaml")
    b1 = ((yaml.safe_load(final_yaml.read_text(encoding="utf-8")) or {}).get("batches") or {}).get("Batch1") or {}
    if create_batch_folder and b1.get("data_subfolder") == "XPS/Batch1":
        cfg = yaml.safe_load((yaml_dir / "config.yaml").read_text(encoding="utf-8")) or {}
        data_root = (cfg.get("paths") or {}).get("data_root", "Data")
        batch_dir = project_dir / data_root / "XPS" / "Batch1"
        if not batch_dir.exists():
            do(batch_dir, "created (empty — put Batch1's XPS files here)",
               lambda: batch_dir.mkdir(parents=True, exist_ok=True))
    return actions
