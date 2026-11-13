"""
LabBook — the whole notebook: every entry, the materials shelf, photos.

    Results/
    ├── LabBook/
    │   ├── materials.yaml        chemicals + stock solutions, written once
    │   ├── templates/            starting points for `new`
    │   ├── entries/              one YAML per experiment, <id>_<words>.yaml
    │   └── reports/              PDFs land here
    └── Figures/Photos/<id>/      photos for that entry (in the figures folder
                                  set by config.yaml figures_root, e.g. ../Figures)

From Python (a notebook cell, or the PyCharm console):

    from battery_analysis.labbook import LabBook
    book = LabBook("../LabBook")
    book.new("1 wt% AdditiveA in 1 M NaPF6/G2", template="electrolyte_additive")
    print(book.get("2026-10-22a").calc_text())
    book.report("2026-10-22a")          # -> PDF, opens it

From a terminal / PyCharm run configuration: see cli.py.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path
from typing import Optional

from .entry import ID_RE, LabEntry
from .materials import MaterialLibrary


def project_figures_dir(project_dir: Path) -> Path:
    """
    The project's figures folder exactly as the plots use it: paths.figures_root
    in the project's config.yaml (notebooks/config.yaml, or config.yaml at the
    project root), resolved against the project folder. Falls back to
    <project>/Figures when there is no readable config.
    """
    from battery_analysis.config.loader import load_config
    for cfg_path in (project_dir / "notebooks" / "config.yaml", project_dir / "config.yaml"):
        if cfg_path.exists():
            try:
                config = load_config(cfg_path, base_dir=project_dir)
                return Path(config.resolve(config.paths.figures_root)).resolve()
            except Exception:
                break
    return project_dir / "Figures"


class LabBook:
    def __init__(self, root: str | Path, photos_dir: Optional[str | Path] = None):
        self.root = Path(root).resolve()
        self.entries_dir = self.root / "entries"
        self.templates_dir = self.root / "templates"
        self.reports_dir = self.root / "reports"
        # Photos live in the project's figures folder (config.yaml figures_root),
        # which may sit outside Results/, e.g. <project>/Figures/Photos.
        self.photos_dir = (Path(photos_dir).resolve() if photos_dir is not None
                           else project_figures_dir(self.root.parent) / "Photos")
        self.library = MaterialLibrary(self.root / "materials.yaml")
        self._cache: dict[str, LabEntry] = {}

    # ----- finding entries -----

    def _files(self) -> dict[str, Path]:
        out = {}
        for p in sorted(self.entries_dir.glob("*.yaml")):
            entry_id = p.stem.split("_")[0]
            if ID_RE.match(entry_id):
                out[entry_id] = p
        return out

    def ids(self) -> list[str]:
        return sorted(self._files())

    def get(self, entry_id: str = "latest") -> LabEntry:
        files = self._files()
        if not files:
            raise FileNotFoundError(f"No entries yet in {self.entries_dir}.")
        if entry_id == "latest":
            entry_id = max(files)
        # a file name works too ("2026-10-22a_1-wt-AdditiveA..." -> "2026-10-22a"),
        # so PyCharm's $FileNameWithoutExtension$ can pass the open file
        entry_id = entry_id.split("_")[0]
        if entry_id not in files:
            raise KeyError(f"No lab-book entry {entry_id!r}. Entries: {', '.join(sorted(files))}")
        if entry_id not in self._cache:
            self._cache[entry_id] = LabEntry.load(files[entry_id], self.library, book=self)
        return self._cache[entry_id]

    def entries(self, since: Optional[str] = None, until: Optional[str] = None) -> list[LabEntry]:
        """Every entry in date order, optionally only from `since` to `until` (YYYY-MM-DD)."""
        out = []
        for i in self.ids():
            day = i[:10]
            if (since and day < since) or (until and day > until):
                continue
            out.append(self.get(i))
        return out

    def lookup(self, ref: str):
        """'2026-10-24a/P1-C2' -> that electrode's am_mass_g (see LabEntry.lookup)."""
        entry_id, _, what = ref.partition("/")
        return self.get(entry_id).lookup(what)

    def list(self) -> str:
        rows = [f"{e.id:<13}{(e.raw.get('status') or ''):<13}{e.title}" for e in self.entries()]
        return "\n".join(rows) or "(no entries yet)"

    # ----- making a new entry -----

    def next_id(self, date: Optional[str] = None) -> str:
        day = date or dt.date.today().isoformat()
        used = {i[-1] for i in self.ids() if i.startswith(day)}
        for letter in "abcdefghijklmnopqrstuvwxyz":
            if letter not in used:
                return f"{day}{letter}"
        raise ValueError(f"26 entries already on {day}.")

    def templates(self) -> list[str]:
        return sorted(p.stem for p in self.templates_dir.glob("*.yaml"))

    def new(self, title: str, template: str = "blank", date: Optional[str] = None) -> Path:
        """Copy a template into entries/ with the next free id, and make its photo folder."""
        src = self.templates_dir / f"{template}.yaml"
        if not src.exists():
            raise FileNotFoundError(f"No template {template!r}. Templates: {', '.join(self.templates())}")
        entry_id = self.next_id(date)
        slug = re.sub(r"[^A-Za-z0-9.]+", "-", title).strip("-")[:50]
        dst = self.entries_dir / f"{entry_id}_{slug}.yaml"
        text = src.read_text()
        text = text.replace("{{id}}", entry_id).replace("{{date}}", entry_id[:10]).replace("{{title}}", title)
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text(text)
        (self.photos_dir / entry_id).mkdir(parents=True, exist_ok=True)
        print(f"New entry {entry_id}: {dst}\nPhotos go in: {self.photos_dir / entry_id}")
        return dst

    # ----- reports -----

    def report(self, which: str = "latest", *, since: Optional[str] = None,
               until: Optional[str] = None, open_pdf: bool = True) -> Path:
        """
        which = an entry id, "latest", or "all" (the whole book, or only
        since/until). Writes a PDF to LabBook/reports/ and returns its path.
        """
        from .report import build_pdf
        if which == "all":
            entries = self.entries(since, until)
            if since or until:
                name = f"LabBook_{since or 'start'}_to_{until or 'now'}"
            else:
                name = "LabBook_all"
        else:
            entries = [self.get(which)]
            name = entries[0].path.stem
        return build_pdf(self, entries, self.reports_dir / f"{name}.pdf", open_pdf=open_pdf)

    def photos_for(self, entry_id: str) -> list[Path]:
        folder = self.photos_dir / entry_id
        exts = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"}
        return sorted(p for p in folder.glob("*") if p.suffix.lower() in exts) if folder.is_dir() else []
