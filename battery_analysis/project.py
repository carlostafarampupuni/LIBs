"""
Project — a thin facade over config + database + analysis modules.

    project = Project(".")            # loads config.yaml: project, paths,
                                       # cell_defaults, palette, samples —
                                       # everything shared across techniques
    project.use("eis", "eis.yaml")    # each technique's own tweaking file
    project.use("drt", "drt.yaml")
    project.use("cycle", "cycle.yaml")
    project.use("rate", "rate.yaml")

    project.import_eis(); project.fit_eis(); project.analyze_eis(); project.plot_eis()
    project.run_drt(); project.plot_drt()
    project.import_cycle(); project.plot_cycle()
    project.import_rate(); project.plot_rate()

`Project` contains no scientific logic itself — every method is a short
call into the moved modules (analysis/<technique>/*.py) + the repository.
Each technique's own YAML file holds only what genuinely differs per run
(which raw folder, cycle mapping, axis limits, styling) — samples,
chemistry, and colors live once in the shared config.yaml.

Every technique's own YAML holds one or more BATCHES — a self-contained
raw folder plus everything about it worth tweaking. Pick a batch once per
notebook run, e.g. `project.import_cycle(batch="25DEG")`, and every later
call for that technique in the same notebook (`analyze_cycle()`,
`plot_cycle()`, ...) remembers it automatically until you pass batch=
again — you only ever pass batch= once per run. `default_batch` in the
YAML is used whenever you don't pass batch= at all. This works exactly
the same way for every technique (eis, drt, cycle, rate, gcd, xps, dqdv).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from battery_analysis.config.loader import load_config, load_technique_config
from battery_analysis.database.repository import ProjectRepository

# technique name -> its config schema class, used by use()
from battery_analysis.config.schema import (
    EISTechniqueConfig, DRTTechniqueConfig, CycleTechniqueConfig, RateTechniqueConfig,
    GCDTechniqueConfig, DQDVTechniqueConfig,
    RICTechniqueConfig, SEMTechniqueConfig, FTIRTechniqueConfig, CVTechniqueConfig,
)

_TECHNIQUE_SCHEMAS = {
    "eis": EISTechniqueConfig,
    "drt": DRTTechniqueConfig,
    "cycle": CycleTechniqueConfig,
    "rate": RateTechniqueConfig,
    "gcd": GCDTechniqueConfig,
    "dqdv": DQDVTechniqueConfig,
    "ric": RICTechniqueConfig,
    "sem": SEMTechniqueConfig,
    "ftir": FTIRTechniqueConfig,
    "cv": CVTechniqueConfig,
}

# Techniques whose YAML is a flat batches:{} config (get_batch() resolves
# a *BatchConfig directly). "drt" IS included — it has its own batches:
# in drt.yaml (plot styling per batch) even though those batches have no
# data_subfolder of their own; the raw EIS spectra come from whichever
# EIS batch is separately selected via _eis_batch().
_BATCHED_TECHNIQUES = {
    "eis", "drt", "cycle", "rate", "gcd", "dqdv", "ric", "sem", "ftir", "cv",
}

# Techniques whose batch has no raw folder to derive a name from
# (FTIR/CV read one curated Excel file, not a data_subfolder) — batch_name()
# falls back to the resolved batch KEY itself for these instead of
# Path(cfg.data_subfolder).name.
_NO_FOLDER_TECHNIQUES = {"ftir", "cv"}

# =====================================================
# run_all() step tables — used only by Project.run_all() below. Each
# technique's list is exactly the same method calls, in the same order,
# that its notebook already makes per batch by hand (see notebooks/*.ipynb)
# — run_all() doesn't invent a different pipeline, it just stops making
# you type it out once per batch. "eis" always comes before "drt" here
# since run_drt()/plot_drt() read whichever EIS batch of the same name was
# already imported.
# =====================================================

def _no_extra_kwargs(batch_name: str) -> dict:
    return {}


def _eis_export_kwargs(batch_name: str) -> dict:
    return {"filename": f"EIS_{batch_name}.xlsx"}


_RUN_ALL_ORDER = ["eis", "drt", "cycle", "rate", "gcd", "xps", "tafel", "gitt", "dqdv", "ric", "sem", "ftir", "cv", "sheets"]

# =====================================================
# export_features() — flattens ANY technique's analyze_x() output into
# numeric feature columns generically, rather than a hand-written
# per-technique field list that has to be kept in sync as analyze_x()
# grows new numbers (EIS's ohmic/diffusion additions, dQ/dV's LLI/LAM
# tracking, ...). See Project.export_features() below for the rationale.
# =====================================================

def _flatten_numeric(value, prefix: str = "") -> dict:
    out: dict = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("sample", "narrative", "mode"):
                continue  # labels, not features
            out.update(_flatten_numeric(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(value, bool):
        pass  # bool is a subclass of int — exclude explicitly, not a numeric feature
    elif isinstance(value, (int, float)):
        out[prefix] = value
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        # A list of dicts (e.g. dQ/dV's per-cycle peak "tracks") isn't
        # itself one feature — reduced to a count plus the mean of each
        # numeric field across entries, so e.g. dqdv.charge.tracks
        # becomes "...tracks.count" and "...tracks.mean_intensity_change_pct"
        # instead of silently vanishing.
        out[f"{prefix}.count"] = len(value)
        numeric_keys = {k for item in value for k, v in item.items()
                         if isinstance(v, (int, float)) and not isinstance(v, bool)}
        for k in numeric_keys:
            vals = [item[k] for item in value
                    if isinstance(item.get(k), (int, float)) and not isinstance(item.get(k), bool)]
            if vals:
                out[f"{prefix}.mean_{k}"] = sum(vals) / len(vals)
    return out

_RUN_ALL_STEPS = {
    "eis": [
        ("import_eis", _no_extra_kwargs),
        ("fit_eis", _no_extra_kwargs),
        ("analyze_eis", _no_extra_kwargs),
        ("plot_eis", _no_extra_kwargs),
        ("export_eis", _eis_export_kwargs),
    ],
    "drt": [
        ("run_drt", _no_extra_kwargs),
        ("plot_drt", _no_extra_kwargs),
    ],
    "cycle": [
        ("import_cycle", _no_extra_kwargs),
        ("import_cycle_profiles", _no_extra_kwargs),
        ("analyze_cycle", _no_extra_kwargs),
        ("plot_cycle_formation", _no_extra_kwargs),
        ("plot_cycle", _no_extra_kwargs),
        ("plot_cycle_profiles", _no_extra_kwargs),
    ],
    "rate": [
        ("import_rate", _no_extra_kwargs),
        ("import_rate_profiles", _no_extra_kwargs),
        ("plot_rate_formation", _no_extra_kwargs),
        ("plot_rate", _no_extra_kwargs),
        ("plot_rate_profiles", _no_extra_kwargs),
    ],
    "gcd": [
        ("import_gcd", _no_extra_kwargs),
        ("analyze_gcd", _no_extra_kwargs),
        ("plot_gcd", _no_extra_kwargs),
    ],
    "xps": [
        ("fit_xps", _no_extra_kwargs),
        ("plot_xps", _no_extra_kwargs),
    ],
    "tafel": [
        ("fit_tafel", _no_extra_kwargs),
        ("plot_tafel", _no_extra_kwargs),
    ],
    "gitt": [
        ("run_gitt", _no_extra_kwargs),
        ("plot_gitt", _no_extra_kwargs),
    ],
    "dqdv": [
        ("import_dqdv", _no_extra_kwargs),
        ("analyze_dqdv", _no_extra_kwargs),
        ("plot_dqdv", _no_extra_kwargs),
    ],
    "ric": [
        ("import_ric", _no_extra_kwargs),
        ("analyze_ric", _no_extra_kwargs),
        ("plot_ric", _no_extra_kwargs),
    ],
    "sem": [
        ("import_sem", _no_extra_kwargs),
        ("analyze_sem", _no_extra_kwargs),
        ("plot_sem", _no_extra_kwargs),
    ],
    "ftir": [
        ("import_ftir", _no_extra_kwargs),
        ("analyze_ftir", _no_extra_kwargs),
        ("plot_ftir", _no_extra_kwargs),
    ],
    "cv": [
        ("import_cv", _no_extra_kwargs),
        ("analyze_cv", _no_extra_kwargs),
        ("plot_cv", _no_extra_kwargs),
    ],
    "sheets": [
        ("plot_sheets", _no_extra_kwargs),
    ],
}


class _XPSTechnique:
    """xps.yaml is read by analysis/xps (not a pydantic schema); this gives it the
    same `.batches` interface run_all() uses for every other technique."""

    def __init__(self, cfg: dict, data_root: Path):
        self.cfg, self.data_root = cfg, data_root

    @property
    def batches(self) -> dict:
        from battery_analysis.analysis.xps import batches as xb
        return xb.list_batches(self.cfg, self.data_root)


class _DictTechnique:
    """tafel.yaml / gitt.yaml: plain dict configs (analysis/pulse/config.py) with the
    same `.batches` interface run_all() uses for every other technique."""

    def __init__(self, cfg: dict, data_root: Path):
        self.cfg, self.data_root = cfg, data_root

    @property
    def batches(self) -> dict:
        return dict(self.cfg.get("batches") or {})


def _find_project_dir(start: Path, config: str = "config.yaml") -> Path:
    """
    The folder that holds notebooks/config.yaml (or config.yaml). Project("..") is relative
    to the kernel's working folder, which an IDE can set somewhere else (e.g. an old run
    setting pointing into Results/_legacy/). If `start` has no config, look in the folders
    above it, never inside a _legacy/ folder, and say which one was used.
    """
    def has_config(d: Path) -> bool:
        return (d / "notebooks" / config).exists() or (d / config).exists()

    if has_config(start) and "_legacy" not in start.parts:
        return start
    for d in [start, *start.parents]:
        if "_legacy" in d.parts or d == d.parent:
            continue
        if has_config(d):
            print(f"ℹ️  No project config in {start} — using {d}. (The notebook's working folder is "
                  f"not where it should be: Project('..') is relative to it. In PyCharm, set the "
                  f"Jupyter working directory to the notebook's folder.)")
            return d
    return start          # load_config then reports the missing file


class Project:
    def __init__(self, project_dir: str | Path, config: str = "config.yaml"):
        self.project_dir = _find_project_dir(Path(project_dir).resolve(), config)
        # The YAMLs live in notebooks/ next to the notebooks that use them;
        # a config.yaml at the project root still works (older layout).
        self.config_path = self.project_dir / "notebooks" / config
        if not self.config_path.exists():
            self.config_path = self.project_dir / config
        self.yaml_dir = self.config_path.parent
        self.config = load_config(self.config_path, base_dir=self.project_dir)

        db_path = self.config.resolve(self.config.paths.database)
        self.repo = ProjectRepository(db_path)
        # figure plot data (Origin/SigmaPlot) -> <exports_root>/PlotData/, mirroring Figures/
        from battery_analysis.plotting.plotdata import register_project
        register_project(self.config.resolve(self.config.paths.figures_root),
                         self.config.resolve(self.config.paths.exports_root))
        self.project_id = self.repo.get_or_create_project(
            self.config.project.name, str(self.config_path)
        )

        self._techniques: dict = {}
        self._xps_results: dict = {}
        self._pulse_results: dict = {}
        # Remembers the last batch= passed per technique, so later calls
        # in the same notebook can omit batch= and reuse it.
        self._batch_keys: dict[str, Optional[str]] = {}

    def close(self) -> None:
        self.repo.close()

    def use(self, technique: str, filename: str) -> "Project":
        """
        Load and validate one technique's own YAML file (e.g. "eis.yaml"),
        relative to the folder config.yaml is in (notebooks/). Call once
        per technique per notebook; every import_x()/fit_x()/plot_x() call
        for that technique then uses whatever file you last pointed it at.

        A YAML that doesn't exist is skipped with a note (the technique is
        simply not loaded), so deleting a technique you don't use never
        stops run_all.ipynb or any other notebook.
        """
        if not (self.yaml_dir / filename).exists():
            print(f"ℹ️  {filename} not found in {self.yaml_dir.name}/ — {technique} skipped "
                  f"(add the file to use {technique}).")
            return self
        if technique in ("tafel", "gitt", "sheets"):
            from battery_analysis.analysis.pulse.config import load_yaml
            self._techniques[technique] = _DictTechnique(
                load_yaml(self.yaml_dir / filename), self.config.resolve(self.config.paths.data_root))
            return self
        if technique == "xps":
            from battery_analysis.analysis.xps.fitting import load_fit_config
            self._techniques["xps"] = _XPSTechnique(
                load_fit_config(self.yaml_dir / filename),
                self.config.resolve(self.config.paths.data_root))
            return self
        if technique not in _TECHNIQUE_SCHEMAS:
            raise ValueError(
                f"Unknown technique {technique!r}. Known: {sorted(_TECHNIQUE_SCHEMAS)}"
            )
        path = self.yaml_dir / filename
        data_root = self.config.resolve(self.config.paths.data_root)
        self._techniques[technique] = load_technique_config(
            path, _TECHNIQUE_SCHEMAS[technique], data_root=data_root,
            labbook_dir=self.project_dir / "LabBook",
        )
        return self

    @property
    def labbook(self):
        """This project's lab notebook (LabBook/) — see battery_analysis/labbook/."""
        from battery_analysis.labbook.book import LabBook
        return LabBook(self.project_dir / "LabBook")

    def get(self, technique: str):
        """The loaded settings of one technique (after project.use(...)), e.g. project.get("tafel").batches."""
        return self._get(technique)

    def _get(self, technique: str):
        if technique not in self._techniques and (self.yaml_dir / f"{technique}.yaml").exists():
            # e.g. DRT reads the EIS settings: load eis.yaml from next to the notebooks
            print(f"ℹ️  loading {technique}.yaml (needed here; add project.use({technique!r}, "
                  f"\"{technique}.yaml\") to choose another file)")
            self.use(technique, f"{technique}.yaml")
        if technique not in self._techniques:
            raise RuntimeError(
                f"No config loaded for {technique!r} — call "
                f"project.use({technique!r}, \"{technique}.yaml\") first."
            )
        return self._techniques[technique]

    def _batch(self, technique: str, batch: Optional[str] = None):
        """
        Resolve which batch inside <technique>.yaml to use. Pass
        batch="..." once (e.g. on import_cycle) and later calls in the
        same notebook (analyze_cycle, plot_cycle, ...) reuse it
        automatically — call any method with batch="..." again to switch.
        """
        if batch is not None:
            self._batch_keys[technique] = batch
        return self._get(technique).get_batch(self._batch_keys.get(technique))

    # Kept as a thin alias — "eis" is by far the most common caller, and
    # DRT reads its batch off of it too (see run_drt/plot_drt below).
    def _eis_batch(self, batch: Optional[str] = None):
        return self._batch("eis", batch)

    def batch_name(self, technique: str = "eis") -> Optional[str]:
        """
        The batch a technique's loaded config targets, derived from its
        data_subfolder (e.g. "EIS/Batch1" -> "Batch1"). Every read across
        the database is scoped to this by default, so a project database
        holding multiple batches' data never silently merges their trends.
        """
        if technique not in self._techniques:
            return None
        if technique in _NO_FOLDER_TECHNIQUES:
            # No raw folder to name a batch after (a curated Excel file,
            # not a data_subfolder) — the batch NAME here just IS the
            # resolved YAML key.
            key = self._batch_keys.get(technique)
            if key is not None:
                return key
            technique_cfg = self._techniques[technique]
            if technique_cfg.default_batch:
                return technique_cfg.default_batch
            if len(technique_cfg.batches) == 1:
                return next(iter(technique_cfg.batches))
            return None
        try:
            cfg = self._batch(technique, None) if technique in _BATCHED_TECHNIQUES else self._techniques.get(technique)
        except (RuntimeError, ValueError):
            return None
        if cfg is None or not getattr(cfg, "data_subfolder", None):
            return None
        if technique == "gcd" and getattr(cfg, "mode", "plating") != "supercap":
            # GCD's raw folders nest one level deeper than every other
            # technique (density folder -> run folder -> csv files) — see
            # resolve_gcd_folder()'s docstring. Using that same helper
            # here (instead of just Path(...).name like every other
            # technique below) keeps this in sync with whatever batch_name
            # import_gcd() actually stored, so plot_gcd()'s filtering
            # always matches what got imported.
            from battery_analysis.analysis.gcd.discovery import resolve_gcd_folder
            _, batch = resolve_gcd_folder(cfg.data_subfolder)
            return batch
        return Path(cfg.data_subfolder).name

    # =====================================================
    # EIS
    # =====================================================

    def import_eis(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.eis.importer import import_eis
        return import_eis(self.config, self._eis_batch(batch), self.repo, self.project_id)

    def fit_eis(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.eis.fitting import run_fit
        eis_cfg = self._eis_batch(batch)
        return run_fit(self.config, eis_cfg, self.repo, self.project_id,
                        batch_name=self.batch_name("eis"))

    def analyze_eis(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.eis.degradation import run_degradation_analysis
        eis_cfg = self._eis_batch(batch)  # resolve/remember, for batch_name() below
        return run_degradation_analysis(
            self.config, self.repo, self.project_id, verbose=verbose,
            batch_name=self.batch_name("eis"), eis_cfg=eis_cfg,
        )

    def plot_eis(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.eis.plot import run_plot
        eis_cfg = self._eis_batch(batch)
        run_plot(self.config, eis_cfg, self.repo, self.project_id,
                  batch_name=self.batch_name("eis"))

    def export_eis(self, filename: str = "EIS_export.xlsx", batch: Optional[str] = None):
        from battery_analysis.analysis.eis.export import export_eis_to_excel
        self._eis_batch(batch)
        return export_eis_to_excel(
            self.config, self.repo, self.project_id, filename, batch_name=self.batch_name("eis")
        )

    # =====================================================
    # DRT — a transform of EIS spectra already in the database
    # =====================================================

    def run_drt(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.drt.analyzer import run_drt
        self._eis_batch(batch)  # resolve/remember which EIS batch's spectra to use
        return run_drt(self.config, self._get("drt"), self.repo, self.project_id,
                        batch_name=self.batch_name("eis"))

    def plot_drt(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.drt.plot import run_plot
        self._eis_batch(batch)
        drt_batch_cfg = self._batch("drt", batch)
        run_plot(self.config, drt_batch_cfg, self.repo, self.project_id,
                 batch_name=self.batch_name("eis"))

    # =====================================================
    # Cycle
    # =====================================================

    def import_cycle(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.cycle.importer import import_cycle
        return import_cycle(self.config, self._batch("cycle", batch), self.repo, self.project_id)

    def analyze_cycle(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.cycle.analyzer import run_degradation_analysis
        self._batch("cycle", batch)
        return run_degradation_analysis(
            self.config, self.repo, self.project_id, verbose=verbose,
            batch_name=self.batch_name("cycle"),
        )

    def plot_cycle(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.cycle.plot import run_plot
        cycle_cfg = self._batch("cycle", batch)
        run_plot(self.config, cycle_cfg, self.repo, self.project_id,
                  batch_name=self.batch_name("cycle"))

    def import_cycle_profiles(self, batch: Optional[str] = None) -> int:
        """
        Voltage-vs-capacity profile points for this batch's formation_cycles
        + custom_cycles (set them under `profiles:` in cycle.yaml). Run
        import_cycle(batch=...) for this batch first.
        """
        from battery_analysis.analysis.cycle.importer import import_cycle_profiles
        return import_cycle_profiles(self.config, self._batch("cycle", batch), self.repo, self.project_id)

    def plot_cycle_formation(self, batch: Optional[str] = None) -> None:
        """Voltage-vs-capacity plot of this batch's formation cycles (1st, 2nd, or both — see profiles.formation_cycles)."""
        from battery_analysis.analysis.cycle.plot import plot_formation
        cycle_cfg = self._batch("cycle", batch)
        plot_formation(self.config, cycle_cfg, self.repo, self.project_id,
                        batch_name=self.batch_name("cycle"))

    def plot_cycle_profiles(self, batch: Optional[str] = None) -> None:
        """Voltage-vs-capacity plot of any cycle you chose in profiles.custom_cycles (2nd, 4th, 50th, 100th, ...)."""
        from battery_analysis.analysis.cycle.plot import plot_profiles
        cycle_cfg = self._batch("cycle", batch)
        plot_profiles(self.config, cycle_cfg, self.repo, self.project_id,
                       batch_name=self.batch_name("cycle"))

    # =====================================================
    # Rate
    # =====================================================

    def import_rate(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.rate.importer import import_rate
        return import_rate(self.config, self._batch("rate", batch), self.repo, self.project_id)

    def plot_rate(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.rate.plot import run_plot
        rate_cfg = self._batch("rate", batch)
        run_plot(self.config, rate_cfg, self.repo, self.project_id,
                  batch_name=self.batch_name("rate"))

    def import_rate_profiles(self, batch: Optional[str] = None) -> int:
        """
        Voltage-vs-capacity profile points for this batch's formation_cycles
        + custom_cycles + auto rate-capability sequence (set them under
        `profiles:`/`rate_capability:` in rate.yaml). Run import_rate(batch=...)
        for this batch first.
        """
        from battery_analysis.analysis.rate.importer import import_rate_profiles
        return import_rate_profiles(self.config, self._batch("rate", batch), self.repo, self.project_id)

    def plot_rate_formation(self, batch: Optional[str] = None) -> None:
        """Voltage-vs-capacity plot of this batch's formation cycles (1st, 2nd, or both — see profiles.formation_cycles)."""
        from battery_analysis.analysis.rate.plot import plot_formation
        rate_cfg = self._batch("rate", batch)
        plot_formation(self.config, rate_cfg, self.repo, self.project_id,
                        batch_name=self.batch_name("rate"))

    def plot_rate_profiles(self, batch: Optional[str] = None) -> None:
        """
        Voltage-vs-capacity plot of any cycle in profiles.custom_cycles
        (5, 10, 15, 20, ...) PLUS the auto rate-capability sequence — the
        first cycle of each C-rate step, renumbered so the cycle right
        after formation reads as "cycle 1" (see rate_capability: in
        rate.yaml).
        """
        from battery_analysis.analysis.rate.plot import plot_profiles
        rate_cfg = self._batch("rate", batch)
        plot_profiles(self.config, rate_cfg, self.repo, self.project_id,
                       batch_name=self.batch_name("rate"))

    # =====================================================
    # GCD / CE (same technique — Li-Li plating/stripping)
    # =====================================================

    # A gcd.yaml batch is either Li-Li plating/stripping (mode: plating, the
    # default) or supercapacitor charge-discharge (mode: supercap) — same
    # method names, routed to analysis/gcd/ or analysis/gcd/supercap.py.

    def import_gcd(self, batch: Optional[str] = None) -> int:
        gcd_cfg = self._batch("gcd", batch)
        if gcd_cfg.mode == "supercap":
            from battery_analysis.analysis.gcd.supercap import import_gcd
        else:
            from battery_analysis.analysis.gcd.importer import import_gcd
        return import_gcd(self.config, gcd_cfg, self.repo, self.project_id)

    def analyze_gcd(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        gcd_cfg = self._batch("gcd", batch)
        if gcd_cfg.mode == "supercap":
            from battery_analysis.analysis.gcd.supercap import run_analysis
        else:
            from battery_analysis.analysis.gcd.report import run_analysis
        return run_analysis(self.config, gcd_cfg, self.repo, self.project_id,
                             verbose=verbose, batch_name=self.batch_name("gcd"))

    def plot_gcd(self, batch: Optional[str] = None, kind: str = "all") -> None:
        """Plating batches: voltage traces. Supercap batches: GCD by composition and by
        current density — kind: "all" | "composition" | "current"."""
        gcd_cfg = self._batch("gcd", batch)
        if gcd_cfg.mode == "supercap":
            from battery_analysis.analysis.gcd.supercap import run_plot
            run_plot(self.config, gcd_cfg, self.repo, self.project_id,
                     batch_name=self.batch_name("gcd"), kind=kind)
            return
        from battery_analysis.analysis.gcd.plot import run_plot
        run_plot(self.config, gcd_cfg, self.repo, self.project_id,
                  batch_name=self.batch_name("gcd"))

    def gcd_tables(self, batch: Optional[str] = None) -> dict:
        """
        The numbers behind analyze_gcd() as DataFrames: {"cycles": every cell,
        every cycle (V_nucleation, V_growth, ΔV_nuc-growth, stripping voltages,
        hysteresis, CE, LLI); "summary": per sample, cycle 1 and all-cycle mean}.
        """
        from battery_analysis.analysis.gcd.report import value_tables
        gcd_cfg = self._batch("gcd", batch)
        return value_tables(self.config, gcd_cfg, self.repo, self.project_id, batch_name=self.batch_name("gcd"))

    # =====================================================
    # XPS
    # =====================================================
    # XPS — xps.yaml (analysis/xps: fitting, sources, pubplot, batches)
    # Raw spectra are fitted here; already-fitted exports (XPSPeak etc.) are
    # plotted as-is — decided per file. Batches work like every other technique.
    # =====================================================

    def _xps_cfg(self, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.xps import batches as xb
        tech = self._get("xps")
        if batch is not None:
            self._batch_keys["xps"] = batch
        return xb.batch_config(tech.cfg, self._batch_keys.get("xps"), data_root=tech.data_root)

    def xps_config(self, batch: Optional[str] = None) -> dict:
        """The full fit config of one XPS batch (shared settings + that batch's overrides)."""
        return self._xps_cfg(batch)

    def xps_batches(self) -> list:
        """Every XPS batch: declared in xps.yaml + auto-discovered Data/XPS/Batch* folders."""
        return list(self._get("xps").batches)

    def _xps_paths(self, batch_name: str):
        from battery_analysis.plotting.style import batch_filename
        exports = self.config.resolve(self.config.paths.exports_root)
        return {
            "results": exports / batch_filename("XPS.xlsx", batch_name),
            "figures": self.config.resolve(self.config.paths.figures_root) / "XPS",
        }

    def fit_xps(self, batch: Optional[str] = None, monte_carlo_runs: Optional[int] = None,
                export: bool = True, verbose: bool = True) -> list:
        """
        Load/fit every source of one XPS batch (xps.yaml) and write
        Exports/XPS_<batch>.xlsx (area %, atomic %, peak table with Monte
        Carlo errors, fit quality, every curve). Returns the list of
        FitOutcome. Figures (and their Origin/SigmaPlot data in Exports/PlotData/) come
        from plot_xps().
        """
        from battery_analysis.analysis.xps import fitting as xf, sources
        cfg = self._xps_cfg(batch)
        name = cfg["batch"]
        if verbose:
            print(f"XPS fit — {name}")
        results = sources.collect(cfg, verbose=verbose)
        n_mc = monte_carlo_runs if monte_carlo_runs is not None else int(cfg["settings"].get("monte_carlo_runs", 100))
        if n_mc:
            xf.monte_carlo_all(results, n=n_mc, verbose=verbose)
        self._xps_results[name] = (cfg, results)
        if export and results:
            paths = self._xps_paths(name)
            wb = sources.survey_workbook(cfg)
            xf.export_excel(results, paths["results"], cfg, wb)
            if verbose:
                print(f"✅  {paths['results'].name} -> {paths['results'].parent}")
        elif not results and verbose:
            print(f"⚠️  XPS batch {name}: nothing found in its source(s) — check `source:` in the YAML")
        return results

    def xps_results(self, batch: Optional[str] = None) -> tuple:
        """(batch config, results) of the last fit_xps() for this batch — fits it first if needed."""
        name = self._xps_cfg(batch)["batch"]
        if name not in self._xps_results:
            self.fit_xps(batch=name)
        return self._xps_results[name]

    def plot_xps(self, batch: Optional[str] = None, show: bool = False) -> list:
        """Publication figures for one batch -> Figures/XPS/<batch>/... (data for Origin/SigmaPlot: Exports/PlotData/XPS/<batch>/)."""
        from battery_analysis.analysis.xps import pubplot, sources
        cfg, results = self.xps_results(batch)
        if not results:
            return []
        root = self._xps_paths(cfg["batch"])["figures"]
        out = pubplot.export_publication_figures(results, sources.survey_workbook(cfg), cfg, root, show=show)
        n_fig = sum(1 for f in out if f.suffix in (".svg", ".png", ".pdf") and "PlotData" not in f.parts)
        print(f"✅  {n_fig} XPS figures (+ their data in Exports/PlotData/) -> {root / cfg['batch']}")
        return out

    def analyze_xps(self, batch: Optional[str] = None, verbose: bool = True) -> dict:
        """{sample: {"<region>.<component>": area %, "atomic.<element>": at %, "narrative": text}}."""
        from battery_analysis.analysis.xps import fitting as xf, sources
        cfg, results = self.xps_results(batch)
        out: dict = {}
        for o in results:
            if o.fitted:
                for _, r in o.table.iterrows():
                    out.setdefault(o.sample, {})[f"{o.region}.{r['component']}"] = float(r["area %"])
        try:
            at = xf.atomic_percent(results, cfg, sources.survey_workbook(cfg), by="element")
            for sample in at.columns:
                for el, v in at[sample].items():
                    out.setdefault(sample, {})[f"atomic.{el}"] = float(v)
        except (KeyError, ValueError) as e:
            if verbose:
                print(f"⚠️  atomic % skipped: {e}")
        for sample, d in out.items():           # one readable line per region, for story()
            lines = [f"XPS — {sample} ({cfg['batch']})"]
            for region in dict.fromkeys(k.split(".")[0] for k in d if not k.startswith("atomic.")):
                comps = sorted(((k.split(".", 1)[1], v) for k, v in d.items() if k.startswith(region + ".")),
                               key=lambda kv: -kv[1])
                lines.append(f"  {region}: " + ", ".join(f"{n} {v:.0f}%" for n, v in comps[:3]))
            top = sorted(((k[7:], v) for k, v in d.items() if k.startswith("atomic.")), key=lambda kv: -kv[1])[:5]
            if top:
                lines.append("  atomic %: " + ", ".join(f"{e} {v:.1f}" for e, v in top))
            d["narrative"] = "\n".join(lines)
            if verbose:
                print(d["narrative"])
        return out

    # =====================================================
    # Tafel and GITT — galvanostatic pulse files (analysis/pulse, tafel, gitt)
    # =====================================================

    def _pulse_batch(self, technique: str, batch: Optional[str]):
        from battery_analysis.analysis.pulse.config import batch_samples, default_batch
        tech = self._get(technique)
        if batch is not None:
            self._batch_keys[technique] = batch
        name = self._batch_keys.get(technique) or default_batch(tech.cfg)
        return name, batch_samples(tech.cfg, name, tech.data_root), tech.cfg

    def _pulse_out(self, kind: str, name: str):
        from battery_analysis.plotting.style import batch_filename
        return (self.config.resolve(self.config.paths.exports_root) / batch_filename(f"{kind}.xlsx", name),
                self.config.resolve(self.config.paths.figures_root) / kind)

    def fit_tafel(self, batch: Optional[str] = None, export: bool = True, verbose: bool = True) -> list:
        """
        Tafel / Butler–Volmer analysis of every sample in a batch (tafel.yaml): points from the
        pulse files and four i0 estimates per state (excel, butler_volmer, tafel_region, linear).
        Writes Exports/Tafel_<batch>.xlsx (results, points, and the check against a hand-built
        workbook when the sample names one).
        """
        import pandas as pd
        from battery_analysis.analysis.tafel.analysis import analyse_sample, compare_with_reference
        name, samples, cfg = self._pulse_batch("tafel", batch)
        if verbose:
            print(f"Tafel — {name}")
        out = [analyse_sample(s, spec, verbose=verbose) for s, spec in samples.items()]
        self._pulse_results[("tafel", name)] = out
        if export and out:
            xlsx, _ = self._pulse_out("Tafel", name)
            xlsx.parent.mkdir(parents=True, exist_ok=True)
            checks = [compare_with_reference(s) for s in out]
            checks = [c for c in checks if not c.empty]
            with pd.ExcelWriter(xlsx) as xw:
                pd.concat([s.results() for s in out], ignore_index=True).to_excel(xw, sheet_name="Results", index=False)
                pd.concat([s.points() for s in out], ignore_index=True).to_excel(xw, sheet_name="Points", index=False)
                if checks:
                    pd.concat(checks, ignore_index=True).to_excel(xw, sheet_name="Check vs workbook", index=False)
            if verbose:
                print(f"✅  {xlsx.name} -> {xlsx.parent}")
        return out

    def tafel_results(self, batch: Optional[str] = None) -> list:
        name, _, _ = self._pulse_batch("tafel", batch)
        if ("tafel", name) not in self._pulse_results:
            self.fit_tafel(batch=name, verbose=False)
        return self._pulse_results[("tafel", name)]

    def plot_tafel(self, batch: Optional[str] = None, show: bool = False) -> list:
        from battery_analysis.analysis.tafel.plot import export_figures
        name, _, cfg = self._pulse_batch("tafel", batch)
        figs = {**(cfg.get("figures") or {}), "_colours": {s.name: s.color for s in self.config.samples if s.color}}
        out = export_figures(self.tafel_results(name), figs, self._pulse_out("Tafel", name)[1], name, show)
        print(f"✅  {len(out)} Tafel figures -> {self._pulse_out('Tafel', name)[1] / name}")
        return out

    def analyze_tafel(self, batch: Optional[str] = None, verbose: bool = True, method: str = "butler_volmer") -> dict:
        """{sample: {"<state>.i0_mA_cm2": ..., "<state>.Rct_ohm_cm2": ..., "narrative": ...}} for one method."""
        out = {}
        for s in self.tafel_results(batch):
            r = s.results()
            r = r[(r["method"] == method) & r["ok"]]
            d = {f"{row.state}.i0_mA_cm2": row.i0_mA_cm2 for row in r.itertuples()}
            d.update({f"{row.state}.Rct_ohm_cm2": row.Rct_ohm_cm2 for row in r.itertuples()})
            d["narrative"] = f"Tafel ({method}) — {s.sample}: i0 " + ", ".join(f"{row.state}: {row.i0_mA_cm2:.3g}" for row in r.itertuples()) + " mA/cm²"
            out[s.sample] = d
            if verbose:
                print(d["narrative"])
        return out

    def run_gitt(self, batch: Optional[str] = None, export: bool = True, verbose: bool = True) -> list:
        """Diffusion coefficients (classic GITT and sqrt(t)) for every sample in a batch (gitt.yaml)."""
        import pandas as pd
        from battery_analysis.analysis.gitt.analysis import analyse_sample, read_reference_workbook
        name, samples, cfg = self._pulse_batch("gitt", batch)
        if verbose:
            print(f"GITT — {name}")
        out = [analyse_sample(s, spec, verbose=verbose) for s, spec in samples.items()]
        self._pulse_results[("gitt", name)] = out
        if export and out:
            xlsx, _ = self._pulse_out("GITT", name)
            xlsx.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(xlsx) as xw:
                pd.concat([s.table for s in out], ignore_index=True).to_excel(xw, sheet_name="Results", index=False)
                checks = []
                for s in out:
                    ref = s.settings.get("reference_workbook")
                    if ref and Path(ref).exists():
                        r = read_reference_workbook(ref)
                        if not r.empty:
                            m = r.merge(s.table[["state", "D_sqrt_t_cm2_s"]], on="state", how="left")
                            m.insert(0, "sample", s.sample)
                            m["ratio_code/excel"] = m["D_sqrt_t_cm2_s"] / m["excel_D_cm2_s"]
                            checks.append(m)
                if checks:
                    pd.concat(checks, ignore_index=True).to_excel(xw, sheet_name="Check vs workbook", index=False)
            if verbose:
                print(f"✅  {xlsx.name} -> {xlsx.parent}")
        return out

    def gitt_results(self, batch: Optional[str] = None) -> list:
        name, _, _ = self._pulse_batch("gitt", batch)
        if ("gitt", name) not in self._pulse_results:
            self.run_gitt(batch=name, verbose=False)
        return self._pulse_results[("gitt", name)]

    def plot_gitt(self, batch: Optional[str] = None, show: bool = False) -> list:
        from battery_analysis.analysis.gitt.plot import export_figures
        name, _, cfg = self._pulse_batch("gitt", batch)
        out = export_figures(self.gitt_results(name), cfg.get("figures") or {}, self._pulse_out("GITT", name)[1], name, show)
        print(f"✅  {len(out)} GITT figures -> {self._pulse_out('GITT', name)[1] / name}")
        return out

    def analyze_gitt(self, batch: Optional[str] = None, verbose: bool = True) -> dict:
        out = {}
        for s in self.gitt_results(batch):
            t = s.table
            d = {f"{r.state}.D_gitt_cm2_s": getattr(r, "D_gitt_cm2_s", float("nan")) for r in t.itertuples()}
            d.update({f"{r.state}.D_sqrt_t_cm2_s": getattr(r, "D_sqrt_t_cm2_s", float("nan")) for r in t.itertuples()})
            d["narrative"] = f"GITT — {s.sample}: {len(t)} states"
            out[s.sample] = d
            if verbose:
                print(d["narrative"])
        return out

    # =====================================================
    # Curated workbooks (sheets.yaml) — figures from already-processed Excel
    # =====================================================

    def plot_sheets(self, batch: Optional[str] = None, show: bool = False) -> list:
        """
        Figures from a curated workbook (sheets.yaml): no import or analysis, the numbers
        are used as they are. Figures -> Figures/Sheets/<batch>/ (data -> Exports/PlotData/).
        """
        from battery_analysis.analysis.pulse.config import default_batch
        from battery_analysis.analysis.sheets import plot_batch
        tech = self._get("sheets")
        if batch is not None:
            self._batch_keys["sheets"] = batch
        name = self._batch_keys.get("sheets") or default_batch(tech.cfg)
        b = tech.cfg["batches"][name]
        wb = Path(b["workbook"])
        wb = wb if wb.is_absolute() else tech.data_root / wb
        if not wb.exists():
            raise FileNotFoundError(f"sheets/{name}: workbook not found: {wb}")
        colours = {s.name: s.color for s in self.config.samples if s.color}
        figs = {**(tech.cfg.get("figures") or {}), **(b.get("figure_settings") or {})}
        out_dir = self.config.resolve(self.config.paths.figures_root) / figs.get("folder", "Sheets")
        out = plot_batch(b, wb, colours, out_dir, name, figs.get("formats", ["svg"]), show)
        print(f"✅  {len(out)} figure(s) from {wb.name} -> {out_dir / name}")
        return out

    # =====================================================
    # dQ/dV — reuses Cycle's raw *_Cycle.csv profile files
    # =====================================================

    def import_dqdv(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.dqdv.importer import import_dqdv
        return import_dqdv(self.config, self._batch("dqdv", batch), self.repo, self.project_id)

    def analyze_dqdv(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.dqdv.report import run_analysis
        dqdv_cfg = self._batch("dqdv", batch)
        return run_analysis(self.config, dqdv_cfg, self.repo, self.project_id,
                             verbose=verbose, batch_name=self.batch_name("dqdv"))

    def plot_dqdv(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.dqdv.plot import run_plot
        dqdv_cfg = self._batch("dqdv", batch)
        run_plot(self.config, dqdv_cfg, self.repo, self.project_id,
                  batch_name=self.batch_name("dqdv"))

    # =====================================================
    # RIC — reuses Cycle's raw *_Capacity.csv files
    # =====================================================

    def import_ric(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.ric.importer import import_ric
        return import_ric(self.config, self._batch("ric", batch), self.repo, self.project_id)

    def analyze_ric(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.ric.report import run_analysis
        ric_cfg = self._batch("ric", batch)
        return run_analysis(self.config, ric_cfg, self.repo, self.project_id,
                             verbose=verbose, batch_name=self.batch_name("ric"))

    def plot_ric(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.ric.plot import run_plot
        ric_cfg = self._batch("ric", batch)
        run_plot(self.config, ric_cfg, self.repo, self.project_id, batch_name=self.batch_name("ric"))

    # =====================================================
    # SEM — cross-section / surface / powder image quantification
    # =====================================================

    def import_sem(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.sem.importer import import_sem
        return import_sem(self.config, self._batch("sem", batch), self.repo, self.project_id)

    def analyze_sem(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.sem.report import run_analysis
        sem_cfg = self._batch("sem", batch)
        return run_analysis(self.config, sem_cfg, self.repo, self.project_id,
                             verbose=verbose, batch_name=self.batch_name("sem"))

    def plot_sem(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.sem.plot import run_plot
        sem_cfg = self._batch("sem", batch)
        run_plot(self.config, sem_cfg, self.repo, self.project_id, batch_name=self.batch_name("sem"))

    # =====================================================
    # FTIR — curated-Excel spectra, no raw-import step
    # =====================================================

    def import_ftir(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.ftir.importer import import_ftir
        ftir_cfg = self._batch("ftir", batch)
        return import_ftir(self.config, ftir_cfg, self.repo, self.project_id, batch_name=self.batch_name("ftir"))

    def analyze_ftir(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.ftir.report import run_analysis
        ftir_cfg = self._batch("ftir", batch)
        return run_analysis(self.config, ftir_cfg, self.repo, self.project_id,
                             verbose=verbose, batch_name=self.batch_name("ftir"))

    def plot_ftir(self, batch: Optional[str] = None) -> None:
        from battery_analysis.analysis.ftir.plot import run_plot
        ftir_cfg = self._batch("ftir", batch)
        run_plot(self.config, ftir_cfg, self.repo, self.project_id, batch_name=self.batch_name("ftir"))

    # =====================================================
    # CV — curated-Excel scan-rate sweeps, no raw-import step
    # =====================================================

    def import_cv(self, batch: Optional[str] = None) -> int:
        from battery_analysis.analysis.cv.importer import import_cv
        cv_cfg = self._batch("cv", batch)
        return import_cv(self.config, cv_cfg, self.repo, self.project_id, batch_name=self.batch_name("cv"))

    def analyze_cv(self, verbose: bool = True, batch: Optional[str] = None) -> dict:
        from battery_analysis.analysis.cv.report import run_analysis
        cv_cfg = self._batch("cv", batch)
        return run_analysis(self.config, cv_cfg, self.repo, self.project_id,
                             verbose=verbose, batch_name=self.batch_name("cv"))

    def plot_cv(self, batch: Optional[str] = None, kind: str = "all") -> None:
        """CV by composition (one figure per cell, scan rates overlaid) and by scan rate
        (one figure per rate, samples overlaid). kind: "all" | "composition" | "scan_rate"."""
        from battery_analysis.analysis.cv.plot import run_plot
        cv_cfg = self._batch("cv", batch)
        run_plot(self.config, cv_cfg, self.repo, self.project_id, batch_name=self.batch_name("cv"), kind=kind)

    # =====================================================
    # "Which experiment was this" lookups
    # =====================================================

    # =====================================================
    # Lab calculators — electrode / slurry / N-P (no raw cycler data,
    # no database — folded in from the battery_lab_toolkit as the
    # battery_analysis/lab/ subpackage)
    # =====================================================

    def run_lab_batch(self, config_path: str, out_dir: Optional[str] = None) -> dict:
        """
        Run an electrode/slurry/N-P batch YAML (see lab_batch_example.yaml
        at the project root) through the lab calculators and write the
        result CSVs. config_path is resolved relative to this project's
        folder if not already absolute. out_dir defaults to this
        project's exports_root (paths.exports_root in config.yaml); pass
        a different folder to write elsewhere. For one-off calculations
        instead of a batch, use lab.lab_electrode.ElectrodeMeasurement /
        lab.lab_slurry.SlurryFormulation / lab.lab_np_ratio.np_ratio_from_electrodes
        directly, or run `python -m battery_analysis.lab.lab_cli` for
        interactive prompts.
        """
        from battery_analysis.lab.lab_batch import run_batch
        path = Path(config_path)
        if not path.is_absolute():
            path = self.yaml_dir / path
        resolved_out = self.config.resolve(self.config.paths.exports_root) if out_dir is None else out_dir
        return run_batch(path, resolved_out)

    def describe_experiment(self, experiment_id: int) -> dict:
        """Every recorded detail (chemistry, electrolyte, replicate, date, raw files) for one physical cell."""
        return self.repo.describe_experiment(experiment_id)

    def find_experiments(self, **filters):
        """e.g. project.find_experiments(sample_name='Bare', technique='eis')"""
        return self.repo.find_experiments(self.project_id, **filters)

    # =====================================================
    # Run everything at once
    # =====================================================

    def run_all(self, techniques: Optional[list[str]] = None, *,
                dry_run: bool = False, stop_on_error: bool = False) -> dict:
        """
        Run every batch of every technique you've project.use()'d, in one
        call: import -> analyze/fit -> plot(-> export for EIS), exactly
        the same steps and order you'd otherwise type out one notebook
        cell per batch for. Nothing needs to be picked by hand — every
        batch already declared in that technique's YAML runs, INCLUDING
        any batch an `auto_discover:` block picked up automatically from
        a new folder under Data/ (see eis.yaml/cycle.yaml/rate.yaml).

        Only techniques you've already called project.use(technique, "...")
        for are run — this never reads a YAML file on its own. Pass
        techniques=["eis", "drt"] to run a subset instead of everything
        loaded.

        dry_run=True prints what WOULD run (technique/batch pairs) without
        touching the database or writing any figures — a quick way to
        check auto-discovery found the folders you expect before
        committing to a real run.

        By default (stop_on_error=False) one batch's failure is caught,
        printed, and skipped — same self-healing spirit as the rest of
        this project: one bad file/folder shouldn't stop every other
        batch from processing. Pass stop_on_error=True while debugging a
        specific batch to let the real traceback stop the run immediately
        instead of being summarized away.

        Returns {technique: {batch_name: "ok" or "error: <message>"}}.
        """
        order = [t for t in _RUN_ALL_ORDER if t in self._techniques]
        if techniques is not None:
            order = [t for t in order if t in techniques]

        plan = {t: list(self._techniques[t].batches.keys()) for t in order}
        plan = {t: batches for t, batches in plan.items() if batches}

        if dry_run:
            print("run_all() would run:")
            for technique, batches in plan.items():
                print(f"  {technique}: {', '.join(batches)}")
            return plan

        results: dict = {}
        for technique, batches in plan.items():
            results[technique] = {}
            for batch_name in batches:
                print(f"\n=== {technique} / {batch_name} ===")
                try:
                    for step_name, kwargs_fn in _RUN_ALL_STEPS[technique]:
                        getattr(self, step_name)(batch=batch_name, **kwargs_fn(batch_name))
                    results[technique][batch_name] = "ok"
                except Exception as e:
                    message = f"{type(e).__name__}: {e}"
                    results[technique][batch_name] = f"error: {message}"
                    print(f"⚠️  {technique}/{batch_name} failed — {message}")
                    if stop_on_error:
                        raise

        print("\n=== run_all summary ===")
        for technique, batches in results.items():
            for batch_name, status in batches.items():
                mark = "✅" if status == "ok" else "❌"
                print(f"{mark} {technique}/{batch_name}: {status}")
        return results

    # =====================================================
    # Engineered feature export — the ML-ready foundation
    # =====================================================
    # NOT a trained model — with a handful of cells per batch, "train a
    # model that predicts cycle life" would just be fitting noise.
    # What's genuinely useful today is this: every numeric numbers each
    # analyze_x() already computes (EIS's resistance-origin trends,
    # cycle's fade rate + early-cycle EOL forecast, dQ/dV's LLI/LAM-like
    # peak tracking, ...) pulled into one flat table, one row per sample.
    # It's immediately useful for comparing/correlating samples by hand
    # even with few cells, AND it's exactly the feature matrix a real ML
    # model needs once there are enough cells to train one — nothing
    # about this table changes when that day comes, there's just more
    # rows in it.

    def export_features(self, filename: str = "Features.csv", out_dir: Optional[str] = None):
        """
        One row per sample, one column per numeric metric from every
        technique you've already project.use()'d — every batch already
        IMPORTED for that technique gets checked (not just whichever
        batch happens to be "currently selected"; after run_all() that's
        simply whatever ran last, which could be an empty placeholder
        batch), so a sample only shows up missing here if it genuinely
        has no data yet, not because of which batch some other call left
        selected. Columns are named "<technique>.<...>" after whatever
        key path analyze_x() returned that number under (e.g. "eis.
        charge_transfer.change_pct", "cycle.early_eol.predicted_eol_
        cycle", "dqdv.degradation.charge.tracks.count") so a new number
        analyze_x() starts computing shows up here automatically, with
        no separate field list to maintain — plus one "<technique>.batch"
        column per technique recording which batch that row's numbers
        actually came from. Techniques with no numeric output (CV/FTIR
        currently only return a narrative string) simply don't
        contribute columns.

        Call this after whatever import_x() calls you want reflected in
        the table — typically right after project.run_all(). Writes a
        CSV to paths.exports_root (or out_dir) and also returns the
        pandas DataFrame directly, so you can inspect/filter it in the
        notebook without re-reading the file.
        """
        import pandas as pd

        analyze_methods = {
            "eis": self.analyze_eis, "cycle": self.analyze_cycle, "gcd": self.analyze_gcd,
            "xps": self.analyze_xps, "dqdv": self.analyze_dqdv, "ric": self.analyze_ric,
            "sem": self.analyze_sem, "ftir": self.analyze_ftir, "cv": self.analyze_cv,
            "tafel": self.analyze_tafel, "gitt": self.analyze_gitt,
            # "rate" has no analyze_rate() (no degradation-analysis step exists for
            # it yet) and "drt" has no per-sample analyze step of its own — both
            # skipped rather than guessed at.
        }

        rows: dict[str, dict] = {}
        for technique, method in analyze_methods.items():
            if technique not in self._techniques:
                continue
            # Every batch declared for this technique, not just whichever
            # one happens to be "currently selected" — after run_all(),
            # that's simply whatever batch ran LAST, which can easily be
            # an empty placeholder batch (no raw data yet) rather than
            # the one with real results. Sweeping every batch means a
            # technique with several real batches of different samples
            # (or one real batch buried after an empty one) still shows
            # up here in full, and each row records which batch it came
            # from (<technique>.batch) so it's traceable.
            for batch_name in self._techniques[technique].batches.keys():
                try:
                    results = method(batch=batch_name, verbose=False)
                except Exception as e:
                    print(f"⚠️  Skipping {technique}/{batch_name} features — {type(e).__name__}: {e}")
                    continue
                for sample, r in (results or {}).items():
                    if sample == "_comparison":
                        continue
                    features = _flatten_numeric(r, prefix=technique)
                    if not features:
                        continue  # this batch had nothing for this sample — don't overwrite a real batch's row with an empty one
                    row = rows.setdefault(sample, {"sample": sample})
                    row[f"{technique}.batch"] = batch_name
                    row.update(features)

        df = pd.DataFrame(rows.values())
        if df.empty:
            print("⚠️  No features to export yet — project.use()/import_x() at least one technique first.")
            return df

        out_path = Path(out_dir) if out_dir else self.config.resolve(self.config.paths.exports_root)
        out_path.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path / filename, index=False)
        print(f"✅  {len(df)} sample(s) × {len(df.columns) - 1} feature(s) written to {out_path / filename}")
        return df

    # =====================================================
    # Cross-technique story report
    # =====================================================

    def story(self, techniques: Optional[list[str]] = None) -> str:
        """
        Pull each migrated technique's narrative and stitch them into one
        per-sample story. As cycle/rate/dqdv/xps/etc. get migrated, add a
        section here the same way eis/drt are called below, and the story
        grows automatically without changing how you call project.story().

        Each technique's *currently selected* batch is used (whatever was
        last passed to that technique's import_x()/default_batch in its
        YAML) — call e.g. project.import_cycle(batch="60DEG") beforehand
        if you want the story to reflect a specific batch.
        """
        techniques = techniques or [
            t for t in ("eis", "drt", "cycle", "gcd", "xps", "dqdv", "ric", "sem", "ftir", "cv")
            if t in self._techniques
        ]
        sections = [f"# Story — {self.config.project.name}\n"]

        if "eis" in techniques:
            results = self.analyze_eis(verbose=False)
            if results:
                sections.append("## EIS\n")
                for sample_name, r in results.items():
                    if sample_name == "_comparison":
                        continue
                    lines = [f"**{sample_name}**"]
                    if r.get("ohmic"):
                        o = r["ohmic"]
                        lines.append(f"- Ohmic resistance (Rs) {o['change_pct']:+.1f}% ({o['status']}): {o['interpretation']}")
                    if r.get("sei"):
                        s = r["sei"]
                        lines.append(f"- SEI resistance {s['change_pct']:+.1f}% ({s['status']}, {s['mechanism']}): {s['interpretation']}")
                    if r.get("charge_transfer"):
                        c = r["charge_transfer"]
                        lines.append(f"- Charge-transfer resistance {c['change_pct']:+.1f}% ({c['status']}, {c['mechanism']}): {c['interpretation']}")
                    if r.get("diffusion"):
                        d = r["diffusion"]
                        lines.append(f"- Diffusion resistance (Warburg) {d['change_pct']:+.1f}% ({d['status']}, {d['mechanism']}): {d['interpretation']}")
                    if r.get("degradation_mode"):
                        m = r["degradation_mode"]
                        lines.append(f"- Dominant degradation mode: {m['dominant']} → {m['recommendation']}")
                    sections.append("\n".join(lines) + "\n")

        if "drt" in techniques:
            results_df = self.repo.list_drt_results(self.project_id, batch_name=self.batch_name("eis"))
            if not results_df.empty:
                sections.append("## DRT\n")
                for sample_name in sorted(results_df["sample"].unique()):
                    sample_rows = results_df[results_df["sample"] == sample_name]
                    first_row = sample_rows.iloc[0]
                    last_row = sample_rows.iloc[-1]
                    first_peaks = self.repo.load_drt_peaks(int(first_row["result_id"]))
                    last_peaks = self.repo.load_drt_peaks(int(last_row["result_id"]))
                    lines = [f"**{sample_name}**"]
                    lines.append(
                        f"- {first_row['sheet_name']}: {len(first_peaks)} resolved process(es) "
                        f"(R_inf={first_row['r_inf']:.2f} Ω)"
                    )
                    lines.append(
                        f"- {last_row['sheet_name']}: {len(last_peaks)} resolved process(es) "
                        f"(R_inf={last_row['r_inf']:.2f} Ω)"
                    )
                    sections.append("\n".join(lines) + "\n")

        if "cycle" in techniques:
            results = self.analyze_cycle(verbose=False)
            if results:
                sections.append("## Cycling\n")
                for sample_name, r in results.items():
                    lines = [f"**{sample_name}**"]
                    if r.get("retention"):
                        ret = r["retention"]
                        lines.append(
                            f"- Capacity retention: {ret['final_pct']:.1f}% after {ret['n_cycles']} "
                            f"cycles ({ret['status']}) — {ret['interpretation']}"
                        )
                    if r.get("ce"):
                        ce = r["ce"]
                        lines.append(f"- Coulombic efficiency: mean {ce['mean']:.2f}%, {ce['status']} — {ce['interpretation']}")
                    if r.get("eol"):
                        e = r["eol"]
                        lines.append(f"- Predicted 80% EOL at cycle {e['predicted_eol_cycle']} ({e['cycles_remaining']} cycles remaining)")
                    if r.get("early_eol"):
                        e = r["early_eol"]
                        lines.append(
                            f"- Early-cycle forecast ({e['basis']}): 80% EOL around cycle "
                            f"{e['predicted_eol_cycle']} (R²={e['r_squared']:.2f}) — {e['caveat']}"
                        )
                    sections.append("\n".join(lines) + "\n")

        if "gcd" in techniques:
            results = self.analyze_gcd(verbose=False)
            if results:
                sections.append("## GCD (Li-Li plating/stripping)\n")
                for sample_name, r in results.items():
                    sections.append(r["narrative"] + "\n")

        if "xps" in techniques:
            results = self.analyze_xps(verbose=False)
            if results:
                sections.append("## XPS\n")
                for key, r in results.items():
                    sections.append(r["narrative"] + "\n")

        if "dqdv" in techniques:
            results = self.analyze_dqdv(verbose=False)
            if results:
                sections.append("## dQ/dV\n")
                for sample_name, r in results.items():
                    sections.append(r["narrative"] + "\n")

        if "ric" in techniques:
            results = self.analyze_ric(verbose=False)
            if results:
                sections.append("## RIC (Reversible/Irreversible Capacity)\n")
                for sample_name, r in results.items():
                    sections.append(r["narrative"] + "\n")

        if "sem" in techniques:
            results = self.analyze_sem(verbose=False)
            if results:
                sections.append("## SEM\n")
                for sample_name, r in results.items():
                    sections.append(r["narrative"] + "\n")

        if "ftir" in techniques:
            results = self.analyze_ftir(verbose=False)
            if results:
                sections.append("## FTIR\n")
                for sample_name, r in results.items():
                    sections.append(r["narrative"] + "\n")

        if "cv" in techniques:
            results = self.analyze_cv(verbose=False)
            if results:
                sections.append("## CV\n")
                for sample_name, r in results.items():
                    sections.append(r["narrative"] + "\n")

        # --- placeholders for techniques not yet migrated ---
        not_migrated = [
            t for t in techniques
            if t not in ("eis", "drt", "cycle", "gcd", "xps", "dqdv", "ric", "sem", "ftir", "cv")
            and t in self._techniques
        ]
        if not_migrated:
            sections.append(
                "## Not yet included\n"
                f"{', '.join(not_migrated)} — migrate these techniques to add their sections here.\n"
            )

        return "\n".join(sections)
