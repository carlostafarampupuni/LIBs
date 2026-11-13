"""
`libs`: run LIBs from the terminal, without opening a notebook.

    libs check  [PROJECT]            what would run, nothing written
    libs run    [PROJECT] [--only eis cycle ...]
    libs new    NAME                 start a new project from the template
    libs public list|download|ingest|figures [NAMES ...]
    libs labbook ...                 the lab book (same commands as `labbook`)

PROJECT is a project folder (the one with Data/ and notebooks/). It
defaults to the current folder, then to ./Project.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Figures are saved to files; never try to open a window.
os.environ.setdefault("MPLBACKEND", "Agg")

TECHNIQUES = ["eis", "drt", "cycle", "rate", "gcd", "xps", "tafel", "gitt",
              "dqdv", "ric", "sem", "ftir", "cv", "sheets"]


def _project_dir(arg: str | None) -> Path:
    def ok(d: Path) -> bool:
        return (d / "notebooks" / "config.yaml").exists() or (d / "config.yaml").exists()

    if arg:
        d = Path(arg).expanduser().resolve()
        if not ok(d):
            sys.exit(f"No project here: {d}\nA project folder holds notebooks/config.yaml.")
        return d
    here = Path.cwd()
    for d in (here, here / "Project", here.parent):
        if ok(d):
            return d
    sys.exit("No project found. Run this inside a project folder, or name it: libs run Project")


def _load(project_dir: Path, only: list[str] | None):
    from battery_analysis.project import Project

    project = Project(project_dir)
    for t in (only or TECHNIQUES):
        if (project.yaml_dir / f"{t}.yaml").exists():
            project.use(t, f"{t}.yaml")
    _skip_empty_batches(project)
    return project


def _skip_empty_batches(project) -> None:
    """Leave out batches whose Data/ folder is missing or empty, so the
    example batches in a fresh project don't 'run' on nothing."""
    data_root = project.config.resolve(project.config.paths.data_root)

    def has_files(folder: Path) -> bool:
        return folder.is_dir() and any(
            f.is_file() and not f.name.startswith(".") for f in folder.rglob("*"))

    for technique, tcfg in list(project._techniques.items()):
        batches = getattr(tcfg, "batches", None)
        if not isinstance(batches, dict):
            continue
        if batches is not getattr(tcfg, "batches", None):
            # batches is rebuilt on every read (XPS, tafel, gitt): filter through a wrapper
            tcfg = project._techniques[technique] = _Filtered(tcfg)
            batches = tcfg.batches
        for name, bcfg in list(batches.items()):
            if isinstance(bcfg, dict):
                sub = bcfg.get("data_subfolder") or bcfg.get("source")
            else:
                sub = getattr(bcfg, "data_subfolder", None)
            if technique == "xps" and isinstance(bcfg, dict) and not sub:
                sub = f"XPS/{name}"   # an XPS batch with no source reads Data/XPS/<batch>
            if sub and not has_files(data_root / sub):
                try:
                    del batches[name]
                except (KeyError, TypeError):
                    continue
                print(f"  (skipped {technique}/{name}: no files in Data/{sub})")


class _Filtered:
    """A technique whose batch list can be trimmed; everything else is the original."""

    def __init__(self, inner):
        self._inner = inner
        self.batches = dict(inner.batches)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def cmd_check(a) -> None:
    project_dir = _project_dir(a.project)
    print(f"Project: {project_dir}\n")
    project = _load(project_dir, a.only)
    plan = project.run_all(dry_run=True)
    data = project_dir / "Data"
    if data.exists():
        print("\nRaw files found:")
        for sub in sorted(p for p in data.iterdir() if p.is_dir() and p.name != "Public"):
            n = sum(1 for f in sub.rglob("*") if f.is_file() and not f.name.startswith("."))
            print(f"  Data/{sub.name}: {n} files")
    if not plan:
        print("\nNothing to run yet. Put raw files in Data/<technique>/<batch>/ "
              "and list the batch in notebooks/<technique>.yaml.")


def cmd_run(a) -> None:
    project_dir = _project_dir(a.project)
    print(f"Project: {project_dir}")
    project = _load(project_dir, a.only)
    results = project.run_all(stop_on_error=a.stop)

    try:
        project.export_features()
    except Exception as e:  # features are a bonus, never fail the run for them
        print(f"⚠️  Features table skipped: {e}")

    exports = project.config.resolve(project.config.paths.exports_root)
    ran = [t for t, b in results.items() if any(s == "ok" for s in b.values())]
    try:
        story = project.story(ran) if ran else "Nothing was analysed."
        exports.mkdir(parents=True, exist_ok=True)
        (exports / "Findings.md").write_text(story, encoding="utf-8")
    except Exception as e:
        print(f"⚠️  Written findings skipped: {e}")

    figures = project.config.resolve(project.config.paths.figures_root)
    failed = sum(1 for b in results.values() for s in b.values() if s != "ok")
    print("\nDone.")
    print(f"  Figures:  {figures}")
    print(f"  Numbers:  {exports / 'Features.csv'}")
    print(f"  Findings: {exports / 'Findings.md'}")
    if failed:
        print(f"  {failed} batch(es) failed; the reason is printed above each one.")
    if a.open:
        _open(figures)
    sys.exit(1 if failed else 0)


def cmd_new(a) -> None:
    template = _template_dir()
    target = Path(a.name).expanduser().resolve()
    if target.exists():
        sys.exit(f"{target} already exists. Choose another name.")

    def skip(folder, names):
        rel = Path(folder).relative_to(template)
        drop = {".DS_Store", "__pycache__", ".ipynb_checkpoints"}
        if rel.parts[:1] in (("Figures",), ("exports",)) or rel.parts[:2] == ("Data", "Public"):
            drop |= {n for n in names if n != ".gitkeep"}
        if rel.parts[:1] == ("Data",) and len(rel.parts) >= 2:
            drop |= {n for n in names if n != ".gitkeep"}
        if rel.parts == ("database",) or rel.parts[:2] in (("LabBook", "entries"), ("LabBook", "reports")):
            drop |= {n for n in names if n != ".gitkeep"}
        return drop & set(names)

    shutil.copytree(template, target, ignore=skip)
    print(f"New project: {target}")
    print("Next: put raw files in its Data/ folder, name your samples in notebooks/config.yaml,")
    print(f"then run:  libs run \"{target.name}\"")


def _template_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    for d in (root / "Project", Path.cwd() / "Project"):
        if (d / "notebooks" / "config.yaml").exists():
            return d
    sys.exit("Template project not found (expected LIBs/Project).")


def cmd_public(a) -> None:
    from battery_analysis.datasets import load_spec, download, ingest, DatasetStore
    from battery_analysis.datasets.plot import plot_figures

    project_dir = _project_dir(a.project)
    spec_path = project_dir / "notebooks" / "datasets.yaml"
    if not spec_path.exists():
        sys.exit(f"No datasets.yaml in {spec_path.parent}")
    spec = load_spec(spec_path)
    names = a.names or None

    if a.action == "list":
        print(download(spec, names, dry_run=True).to_string())
    elif a.action == "download":
        print(download(spec, names).to_string())
    elif a.action == "ingest":
        report = ingest(spec, names, force=a.force)
        print(report.to_string())
        store = DatasetStore(spec["_store"])
        print("\n", store.summary())
    elif a.action == "figures":
        store = DatasetStore(spec["_store"])
        for name, path in plot_figures(store, spec, names).items():
            print(f"  {name}: {path}")


def cmd_labbook(a, rest: list[str]) -> None:
    from battery_analysis.labbook.cli import main as labbook_main

    project_dir = _project_dir(a.project)
    labbook_main(["--book", str(project_dir / "LabBook"), *rest])


def _open(path: Path) -> None:
    import subprocess
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="libs",
        description="LIBs: raw battery files in, figures and numbers out.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="show what would run; writes nothing")
    p.add_argument("project", nargs="?")
    p.add_argument("--only", nargs="+", metavar="TECHNIQUE")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("run", help="import, analyse and draw every batch")
    p.add_argument("project", nargs="?")
    p.add_argument("--only", nargs="+", metavar="TECHNIQUE", help="e.g. --only gcd eis")
    p.add_argument("--stop", action="store_true", help="stop at the first error and show it in full")
    p.add_argument("--open", action="store_true", help="open the Figures folder when done")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("new", help="start a new project from the template")
    p.add_argument("name")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("public", help="public datasets: list, download, ingest, figures")
    p.add_argument("action", choices=["list", "download", "ingest", "figures"])
    p.add_argument("names", nargs="*", help="dataset names from datasets.yaml (default: all enabled)")
    p.add_argument("--project")
    p.add_argument("--force", action="store_true", help="ingest again even if unchanged")
    p.set_defaults(fn=cmd_public)

    p = sub.add_parser("labbook", help="lab book: list, new, calc, report, templates")
    p.add_argument("--project")

    a, rest = ap.parse_known_args(argv)
    if a.cmd == "labbook":
        cmd_labbook(a, rest)
    elif rest:
        ap.error(f"unrecognised arguments: {' '.join(rest)}")
    else:
        a.fn(a)


if __name__ == "__main__":
    main()
