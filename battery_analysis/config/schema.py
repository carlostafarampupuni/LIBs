"""
Dataclasses (pydantic models) mirroring config.yaml.

Design note
-----------
Two kinds of sections live in a project's config.yaml:

1. Sections we validate strictly, because the technique has been migrated
   to battery_analysis (currently just `techniques.eis`). Typos or missing
   fields here fail fast with a clear message, instead of a KeyError three
   functions deep into a plotting call.

2. Sections for techniques *not yet migrated* (drt, cycle, rate, ce, gcd,
   ric, dqdv, profiles, cycleivium, sem, xps). These are accepted as-is
   (untyped dict) so you can start filling in a project's full config today
   without waiting for every technique's schema to be written. As each
   technique gets migrated, add a strict model for it here (copy the
   EISTechniqueConfig pattern) and it starts getting validated too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

Number = float
XYLim = Tuple[Number, Number]


# =========================================================
# SHARED BATCH RESOLUTION
# =========================================================
# Every technique's own YAML holds `batches: {name: {...}, ...}` plus an
# optional `default_batch:` — one file, several fully self-contained
# batches (which raw folder, and everything about it worth tweaking).
# All technique configs below share this exact same resolution rule via
# their own get_batch(); this is the one place it's implemented.

def _resolve_batch(batches: Dict[str, Any], default_batch: Optional[str],
                    name: Optional[str], label: str):
    if name is None:
        if default_batch:
            name = default_batch
        elif len(batches) == 1:
            name = next(iter(batches))
        else:
            raise ValueError(
                f"{label} defines multiple batches ({list(batches)}) — "
                "pass batch=\"...\" or set default_batch in the YAML."
            )
    if name not in batches:
        raise ValueError(f"Unknown batch {name!r} in {label}. Known: {list(batches)}")
    return batches[name]


# =========================================================
# SHARED PLOT-STYLE BASE CLASSES
# =========================================================
# Every technique repeated the same handful of plot-styling fields in its
# own config class (figsize/legend_loc/xlim/ylim/xtick_step/ytick_step/
# xticks_n/yticks_n) — harmless on its own, but it meant a new default or
# a new shared field had to be hand-copied into a dozen classes, and
# nothing enforced they stayed in sync. Two shared bases instead:
#
#   AxisStyleConfig  — one whole plot's styling: figsize + legend_loc +
#                       the 6 axis/tick fields. Used by every "one figure"
#                       config (capacity/CE/voltage-trace/RIC/CV/FTIR
#                       plots, voltage-vs-capacity profile plots, ...).
#
#   AxisTicksConfig  — just the 6 axis/tick fields, no figsize/legend_loc
#                       — used by per-ENTRY configs nested inside a parent
#                       that already carries the batch's figsize/legend_loc
#                       (EIS sheet/evolution entries, dQ/dV plot entries,
#                       DRT sheet-comparison entries).
#
# A technique-specific class still subclasses one of these and adds only
# what's genuinely its own (e.g. RICPlotDefaults adds `save`, FTIRPlotDefaults
# adds `xlabel`/`invert_x`/`hide_yaxis`) — field names/defaults for the
# shared part are unchanged, so no existing YAML needs to change.

class AxisStyleConfig(BaseModel):
    figsize: str = "medium"
    legend_loc: str = "best"
    xlim: Optional[XYLim] = None
    ylim: Optional[XYLim] = None
    # Tick control — see the "TICK CONTROL" note in plotting/style.py.
    # xtick_step/ytick_step: exact spacing, always lands on both ends of
    # xlim/ylim. xticks_n: exact tick count instead, evenly spaced end to
    # end. Leave all four unset for a clean default (5 ticks) instead of
    # matplotlib's own guess.
    xtick_step: Optional[Number] = None
    ytick_step: Optional[Number] = None
    xticks_n: Optional[int] = None
    yticks_n: Optional[int] = None
    # Tick labels without needless decimals: 0, 0.5, 1 instead of 0.0, 0.5, 1.0.
    compact_ticks: bool = False


class AxisTicksConfig(BaseModel):
    xlim: Optional[XYLim] = None
    ylim: Optional[XYLim] = None
    xtick_step: Optional[Number] = None
    ytick_step: Optional[Number] = None
    xticks_n: Optional[int] = None
    yticks_n: Optional[int] = None


# =========================================================
# SHARED (used by every technique)
# =========================================================

class ProjectMeta(BaseModel):
    name: str
    root: Optional[str] = None


class PathsConfig(BaseModel):
    data_root: str = "../../Data"
    database: str = "database/project.db"
    figures_root: str = "../../Figures"
    exports_root: str = "exports/"


# =========================================================
# CELL / EXPERIMENT METADATA
# =========================================================
# This is the "which experiment was this" information: chemistry,
# electrolyte, cell format, temperature. It can't be read out of a raw
# instrument file, so it's declared once here — project-wide via
# cell_defaults, optionally overridden per sample via SampleConfig.cell —
# and the importer stamps it onto every experiment record automatically.

class CellMetadata(BaseModel):
    cathode: Optional[str] = None
    anode: Optional[str] = None
    cell_type: Optional[str] = None          # "full" | "half"
    electrolyte: Optional[str] = None
    additives: List[str] = Field(default_factory=list)
    temperature_c: Optional[Number] = None
    notes: Optional[str] = None

    def merged_with(self, override: Optional["CellMetadata"]) -> "CellMetadata":
        """Return a copy with any field override sets applied on top of self."""
        if override is None:
            return self
        base = self.model_dump()
        for k, v in override.model_dump(exclude_unset=True).items():
            if v not in (None, [], ""):
                base[k] = v
        return CellMetadata(**base)


class SampleConfig(BaseModel):
    """
    A GLOBAL override for one auto-discovered sample — identity that's
    the same across every batch/technique: a forced color, or chemistry
    that differs from cell_defaults. (Which replicate folder represents
    this sample is a per-batch choice, not a global one — set `plot:`
    inside that technique's own YAML file instead, e.g. eis.yaml.)
    """
    name: str
    color: Optional[str] = None
    # Raw file-name prefixes that belong to this sample, e.g. a cell file
    # "SampleA-1.xlsx" has prefix "SampleA". Every technique that
    # reads cell files renames them to `name` through this list.
    aliases: List[str] = Field(default_factory=list)
    cell: Optional[CellMetadata] = None


# =========================================================
# EIS (migrated — validated strictly)
# =========================================================

class EISFittingConfig(BaseModel):
    use_model: bool = False
    circuit_key: str = "SEI_CT_W"     # a key of analysis/eis/fitting.py CIRCUITS, any impedance.py circuit string, or "auto"
    extract_at_freq: bool = True
    freq_extraction: Dict[str, Number] = Field(default_factory=dict)
    # Model-fit options — all optional; leave them out and everything is read off the data.
    candidates: List[str] = Field(default_factory=list)      # circuits "auto" chooses from; empty = all of CIRCUITS
    inductance: Union[bool, str] = "auto"                     # "auto": add L0 only if the spectrum is inductive at HF
    f_min: Optional[Number] = None                            # Hz; fit only this window (None = all points)
    f_max: Optional[Number] = None
    initial_guess: Dict[str, Number] = Field(default_factory=dict)   # impedance.py names, e.g. {R1: 20, CPE1_1: 0.9}


class EISAxisDefaults(BaseModel):
    xlim: Optional[XYLim] = None
    ylim: Optional[XYLim] = None
    xtick_step: Optional[Number] = None
    xticks_n: Optional[int] = None


class EISEvolutionEntry(AxisTicksConfig):
    name: str
    gradient_start: str = "#C0C0C0"
    gradient_end: Optional[str] = None
    save: Optional[str] = None


class EISParamPlot(BaseModel):
    param: str
    ylabel: str
    ylim: Optional[XYLim] = None
    yticks_n: int = 5
    ytick_step: Optional[Number] = None
    yscale: Optional[str] = None
    save: Optional[str] = None


class EISPlottingConfig(BaseModel):
    axis_defaults: EISAxisDefaults = Field(default_factory=EISAxisDefaults)
    evolution: List[EISEvolutionEntry] = Field(default_factory=list)
    param_plots: List[EISParamPlot] = Field(default_factory=list)
    figsize: str = "small"
    legend_loc: str = "best"


class EISSheetDefinition(AxisTicksConfig):
    sheet: str


class EISSampleOverride(BaseModel):
    """
    Per-BATCH override: which of this sample's folders to use, and which
    one is representative (for parameter-evolution plots), in THIS
    data_subfolder specifically. A tie in file count is the usual reason
    you'd need this — see discovery.py.
    """
    name: str
    folders: Optional[List[str]] = None
    plot: Optional[List[str]] = None


class EISBatchConfig(BaseModel):
    # Which raw folder this batch targets, e.g. "EIS/Batch1" — the one
    # thing about a batch that genuinely has to be stated (which folder
    # to read), not boilerplate. Sample folders inside it are discovered
    # automatically; you never type them.
    data_subfolder: str
    cycle_mapping: Dict[int, str] = Field(default_factory=dict)
    # Case-insensitive SUBSTRING of a raw file's own filename -> sheet
    # key, checked before cycle_mapping. Use this whenever a batch's
    # files don't follow any consistent embedded-digit convention —
    # cycle_mapping only works when every file has a distinct integer
    # baked into its name; filename_mapping works for ANY naming scheme
    # (a mix of hours/precycle/cycle-counts, voltage steps, current
    # densities, or genuinely arbitrary names) because you name the
    # actual file, not a pattern in it. e.g.:
    #   filename_mapping: {"0hrs": "1", "6hrs": "2", "precycle": "4"}
    # matches "ADD at 0hrs.mpt", "DEE at 6hrs.mpt",
    # "Li_LFP_ADD EIS after Precycle.mpt", etc. — whichever substring is
    # found in a file's stem (case-insensitively) wins; only applied to
    # files that resolve to a single EIS run (a multi-run file — several
    # concatenated cycle sweeps in one BioLogic export — still goes
    # through the existing cycle_mapping/positional logic, since one
    # filename can't unambiguously label several runs).
    filename_mapping: Dict[str, str] = Field(default_factory=dict)
    sheet_names: Dict[str, str] = Field(default_factory=dict)
    sheet_order: List[str] = Field(default_factory=list)
    sheets: List[EISSheetDefinition] = Field(default_factory=list)
    fitting: EISFittingConfig = Field(default_factory=EISFittingConfig)
    plotting: EISPlottingConfig = Field(default_factory=EISPlottingConfig)
    sample_overrides: List[EISSampleOverride] = Field(default_factory=list)
    # Leave samples or single cells out of this batch entirely (import, fit,
    # plots, export, DRT): a sample name hides every cell of it ("0%_SiO2"),
    # a folder name hides one cell ("0%_SiO24"). Delete the entry to bring it back.
    exclude: List[str] = Field(default_factory=list)
    # Which samples appear in this batch's plots, one per line — comment a
    # line out (#) to hide that sample. Empty / left out = every sample.
    samples: List[str] = Field(default_factory=list)
    # Which trial (cell) of each sample is plotted and carried into analysis
    # when a sample has several cells in one batch:
    #   first      — the old rule: the folder with most files, else the lowest cell number
    #   best_fit   — every cell is fitted; the one the circuit describes best (lowest fit_err_pct)
    #   lowest_Rct — every cell is fitted; the one with the smallest Rct (best-performing cell)
    # A sample_overrides `plot:` entry always wins over this.
    representative: str = "first"

    def sample_override(self, name: str) -> Optional[EISSampleOverride]:
        for s in self.sample_overrides:
            if s.name == name:
                return s
        return None


class EISTechniqueConfig(BaseModel):
    """
    One eis.yaml, several batches: `batches: {batch1: {...}, batch2: {...}}`.
    Each batch is fully self-contained (its own data_subfolder, cycle
    mapping, fitting, plot styling) — switching batches in a notebook is
    `project.import_eis(batch="batch2")`, nothing else changes.
    """
    batches: Dict[str, EISBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> EISBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "eis.yaml")


class DRTSheetPlot(AxisTicksConfig):
    sheet: str                       # matches an EIS sheet_name, e.g. "EIS1"
    # None = inherit the batch-level plotting.figsize (unchanged default
    # behaviour). Set per-entry to override just this one sheet's
    # comparison plot, e.g. figsize: small for 0h/6h and figsize: medium
    # for Precyc/50cyc within the same batch. Deliberately its own field
    # (not AxisStyleConfig) — that base's figsize defaults to "medium"
    # unconditionally, which would break the "inherit the batch" contract.
    figsize: Optional[str] = None
    peak_labels: List[str] = Field(default_factory=list)
    save: Optional[str] = None


class DRTContourPerSample(BaseModel):
    sample: str
    sheet_order: List[str] = Field(default_factory=list)  # order of cycle rows, top to bottom; empty = whatever's in the DB
    figsize: str = "medium"
    xlim: Optional[XYLim] = None
    xtick_step: Optional[Number] = None
    xticks_n: Optional[int] = None
    clim: Optional[XYLim] = None
    save: Optional[str] = None


class DRTContourPerCycle(BaseModel):
    sheet: str
    figsize: str = "medium"
    xlim: Optional[XYLim] = None
    xtick_step: Optional[Number] = None
    xticks_n: Optional[int] = None
    clim: Optional[XYLim] = None
    save: Optional[str] = None


class DRTPlottingConfig(BaseModel):
    figsize: str = "medium"
    legend_loc: str = "best"
    sheets: List[DRTSheetPlot] = Field(default_factory=list)
    contour_per_sample: List[DRTContourPerSample] = Field(default_factory=list)
    contour_per_cycle: List[DRTContourPerCycle] = Field(default_factory=list)

class DRTBatchConfig(BaseModel):
    plotting: DRTPlottingConfig = Field(default_factory=DRTPlottingConfig)


class DRTTechniqueConfig(BaseModel):
    # engine: "pydrttools" = Ciucci lab's pyDRTtools (the published DRTtools code; default)
    #         "own"        = this package's own RBF-Tikhonov solver (kept for cross-checks)
    engine: str = "pydrttools"
    # lambda_selection (pydrttools only): "fixed" uses lambda_reg as is (DRTtools "custom");
    # "GCV" | "mGCV" | "rGCV" | "LC" | "re-im" | "kf" let pyDRTtools choose lambda itself.
    lambda_selection: str = "fixed"
    n_tau: int = 100            # engine "own" only -- pyDRTtools uses 10 points per measured frequency
    lambda_reg: float = 1e-3
    rbf_type: str = "gaussian"
    data_use: str = "combined"
    reg_order: int = 2
    fwhm_coeff: float = 0.5
    inductance: bool = False

    batches: Dict[str, DRTBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> DRTBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "drt.yaml")


# =========================================================
# CYCLE / RATE (share the same raw-file format: paired
# *_Capacity.csv + *_Cycle.csv, sample name embedded in the filename)
# =========================================================

class CyclingPlotDefaults(AxisStyleConfig):
    """Styling for one Cycle/Rate capacity or CE figure."""


class ProfilePickConfig(BaseModel):
    """
    Which cycles to pull voltage-vs-capacity profiles for out of this
    batch's *_Cycle.csv file. Two independent lists, since "formation"
    and "any cycle you like" are picked for different reasons:

      formation_cycles: the first few cycles run before real cycling/rate
      testing starts (usually the instrument's own ordinals 1, 2, ...).
      Choose 1, 2, or however many you actually ran — e.g. [1, 2] to plot
      both, or [2] for just the second. plot_..._formation() draws these.

      custom_cycles: any other ordinal cycle numbers you want a profile
      for, called out explicitly — e.g. [2, 4, 50, 100]. plot_..._profiles()
      draws these (plus, for Rate, the auto rate-capability sequence below).

    Cycle numbers are the instrument's own 1st/2nd/3rd/... ordinals in the
    *_Cycle.csv file — the same numbering dQ/dV already uses.
    """
    formation_cycles: List[int] = Field(default_factory=list)
    # Which ONE of formation_cycles actually gets drawn in the all-samples
    # formation comparison plot (plot_cycle_formation()/plot_rate_formation())
    # — battery data on cycle 1 vs cycle 2 is sometimes messy, so import
    # both, look at the console CE readout for each, then set this to
    # whichever looked cleanest. None (default) -> the first entry in
    # formation_cycles.
    formation_plot_cycle: Optional[int] = None
    custom_cycles: List[int] = Field(default_factory=list)
    modes: List[str] = Field(default_factory=lambda: ["charge", "discharge"])


class ProfilePlotConfig(AxisStyleConfig):
    """Styling for one voltage-vs-capacity profile figure (see ProfilePickConfig).
    xlim = capacity axis (mAh/g), ylim = voltage axis (V)."""
    save: Optional[str] = None


class ESPECChannelConfig(BaseModel):
    """
    One ESPEC-format channel to import for this Cycle batch, e.g. a raw
    file at data_subfolder/<run>/C1S1CH10/C1S1CH10_LFP-CYC_All.csv.

    Unlike Wonatech's *_Capacity.csv (sample name + replicate embedded in
    the filename, auto-discovered — see cycling_common/discovery.py), an
    ESPEC raw file carries none of that: which sample this channel is,
    and the active electrode mass needed to convert raw mAh into mAh/g,
    are genuine external metadata that has to be declared here, once per
    channel. See cycling_common/espec_io.py's module docstring for why
    guessing either would silently corrupt every downstream number.

    Several channels can share one `sample` (replicate cells of the same
    material) — give each a distinct `replicate`; the lowest becomes that
    sample's representative, same convention as Wonatech's discovery.py.
    """
    channel: str                              # e.g. "C1S1CH10" — matched against "<channel>*_All.csv"
    sample: str                               # sample name this channel is imported as
    active_mass_g: Optional[Number] = None    # REQUIRED — None means "not supplied yet"; importer skips this channel with a loud warning rather than guessing
    formation_ptn: int = 2                    # ESPEC pattern number for the formation/0.1C cycles
    cycle_ptn: int = 3                        # ESPEC pattern number for the main cycling data
    replicate: int = 1


class CycleBatchConfig(BaseModel):
    # Which raw folder THIS batch targets, e.g. "Cycle/25DEG/20260717".
    data_subfolder: str
    capacity_plot: CyclingPlotDefaults = Field(default_factory=CyclingPlotDefaults)
    ce_plot: CyclingPlotDefaults = Field(default_factory=CyclingPlotDefaults)

    # Voltage-vs-capacity profiles — see ProfilePickConfig/ProfilePlotConfig
    # above. import_cycle_profiles() reads formation_cycles + custom_cycles
    # out of profiles: below; plot_cycle_formation()/plot_cycle_profiles()
    # style themselves from formation_plot:/profiles_plot: respectively.
    profiles: ProfilePickConfig = Field(default_factory=ProfilePickConfig)
    formation_plot: ProfilePlotConfig = Field(default_factory=ProfilePlotConfig)
    profiles_plot: ProfilePlotConfig = Field(default_factory=ProfilePlotConfig)

    # Set this to import from ESPEC-format raw files instead of Wonatech
    # *_Capacity.csv/*_Cycle.csv auto-discovery — one entry per channel.
    # Leave empty (the default) for a normal Wonatech-format batch; a
    # batch with any espec_channels: entries is imported entirely via the
    # ESPEC path (cycling_common/espec_io.py), auto_discover: is ignored
    # for it since ESPEC channels can't be auto-named from the filename.
    espec_channels: List[ESPECChannelConfig] = Field(default_factory=list)


class CycleTechniqueConfig(BaseModel):
    """
    One cycle.yaml, several batches: `batches: {25DEG: {...}, 60DEG: {...}}`
    — same pattern as eis.yaml. Pick a batch once per notebook run —
    `project.import_cycle(batch="25DEG")` — and later calls in that same
    notebook remember it automatically until you pass batch= again.
    `default_batch` is used if you never pass batch= at all.
    """
    batches: Dict[str, CycleBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> CycleBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "cycle.yaml")


class RateCapabilityConfig(BaseModel):
    """
    Auto-numbered rate-capability profile sequence: the first cycle of
    each C-rate step, renumbered so the first one after formation reads
    as "cycle 1". With formation_cycles: [1, 2] (2 formation cycles) and
    interval: 5 (defaults to this batch's c_rate_step), the actual
    ordinals pulled are 3, 8, 13, 18, ... — i.e. cycle 3 becomes "cycle
    1", then every 5 cycles after that — stopping automatically at the
    first cycle number the *_Cycle.csv file has no data for.
    """
    interval: Optional[int] = None   # cycles between steps; None -> this batch's c_rate_step
    modes: List[str] = Field(default_factory=lambda: ["discharge"])


class ESPECRateChannelConfig(BaseModel):
    """
    Rate's counterpart to ESPECChannelConfig (see that docstring for the
    active_mass_g "ask, don't guess" reasoning) — rate_ptns replaces
    cycle_ptn since a rate-capability file has one ESPEC pattern per
    C-rate step rather than one continuous cycling pattern.
    """
    channel: str
    sample: str
    active_mass_g: Optional[Number] = None
    formation_ptn: int = 2
    rate_ptns: Dict[int, str] = Field(default_factory=dict)   # {ptn_no: "0.5C", ...}
    replicate: int = 1


class RateBatchConfig(BaseModel):
    data_subfolder: str
    electrode_type: str = "cathode"   # "cathode" | "anode" — anode swaps Q_Ch/Q_Dis (see rate.py)
    # Vertical dotted division lines marking C-rate steps, e.g. every 5
    # cycles: c_rate_step=5 draws lines at 5,10,15,... up to the data's
    # max cycle. Use c_rate_lines instead for irregular step points.
    c_rate_step: Optional[int] = None
    c_rate_lines: List[float] = Field(default_factory=list)
    capacity_plot: CyclingPlotDefaults = Field(default_factory=CyclingPlotDefaults)
    ce_plot: CyclingPlotDefaults = Field(default_factory=CyclingPlotDefaults)

    # Voltage-vs-capacity profiles — see ProfilePickConfig/ProfilePlotConfig
    # above CycleBatchConfig. `profiles.formation_cycles`/`custom_cycles`
    # feed import_rate_profiles(); rate_capability: additionally controls
    # the auto renumbered rate-step sequence (see RateCapabilityConfig).
    # plot_rate_formation() draws formation_plot:; plot_rate_profiles()
    # draws BOTH the rate-capability sequence and custom_cycles together,
    # styled from profiles_plot:.
    profiles: ProfilePickConfig = Field(default_factory=ProfilePickConfig)
    rate_capability: RateCapabilityConfig = Field(default_factory=RateCapabilityConfig)
    formation_plot: ProfilePlotConfig = Field(default_factory=ProfilePlotConfig)
    profiles_plot: ProfilePlotConfig = Field(default_factory=ProfilePlotConfig)

    # ESPEC-format import — see CycleBatchConfig.espec_channels above and
    # ESPECRateChannelConfig; same all-or-nothing per-batch rule.
    espec_channels: List[ESPECRateChannelConfig] = Field(default_factory=list)

    # Which samples appear in this batch's plots, one per line — comment a
    # line out (#) to hide that sample. Empty / left out = every sample.
    samples: List[str] = Field(default_factory=list)
    # Several cells (trials) of one sample: which one every Rate plot uses —
    #   first   the lowest cell number (the old rule)
    #   best    highest discharge capacity at the lowest current density, among
    #           cells whose mean coulombic efficiency is at least 75 %
    #   median  the most typical cell: capacity closest to the sample's median
    # A sample_overrides plot: entry ("<sample><cell>", e.g. 5%_SiO24) always wins.
    representative: str = "first"
    sample_overrides: List[EISSampleOverride] = Field(default_factory=list)

    def sample_override(self, name: str) -> Optional[EISSampleOverride]:
        for s in self.sample_overrides:
            if s.name == name:
                return s
        return None


class RateTechniqueConfig(BaseModel):
    """One rate.yaml, several batches — same pattern as eis.yaml/cycle.yaml."""
    batches: Dict[str, RateBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> RateBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "rate.yaml")


# =========================================================
# dQ/dV — reuses Cycle's raw *_Cycle.csv profile files, no new raw format
# =========================================================

class DQDVPlotEntry(AxisTicksConfig):
    sample: str
    cycles: List[int] = Field(default_factory=list)   # which ordinal cycles to overlay; empty = all imported
    modes: List[str] = Field(default_factory=lambda: ["charge", "discharge"])
    save: Optional[str] = None


class DQDVPlottingConfig(BaseModel):
    figsize: str = "medium"
    legend_loc: str = "best"
    entries: List[DQDVPlotEntry] = Field(default_factory=list)


class DQDVBatchConfig(BaseModel):
    # Same raw folder Cycle reads for this batch — e.g. "Cycle/25DEG/20260717".
    data_subfolder: str
    cycles: List[int] = Field(default_factory=lambda: [1, 2, 5, 10])
    modes: List[str] = Field(default_factory=lambda: ["charge", "discharge"])
    smoothing_window: int = 51
    peak_prominence: Optional[float] = None   # None -> auto (10% of max |dQ/dV|), matching the original
    plotting: DQDVPlottingConfig = Field(default_factory=DQDVPlottingConfig)


class DQDVTechniqueConfig(BaseModel):
    """
    One dqdv.yaml, several batches — mirror whatever batches cycle.yaml
    defines, since dQ/dV reads the exact same *_Cycle.csv files.
    """
    batches: Dict[str, DQDVBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> DQDVBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "dqdv.yaml")


# =========================================================
# GCD / CE (Li-Li symmetric-cell plating/stripping — same
# technique/raw-format; ce.py turned out to be gcd.py plus one extra
# calculation, ce_method3, so both are covered by one schema)
# =========================================================

class GCDPlotDefaults(AxisStyleConfig):
    """
    Styling for one GCD figure. Each figure has its own block — the combined
    one (all samples) and the separate one (one figure per sample) are set
    independently. A block that is commented out is not drawn.
    """
    # A list gives each separate figure its own size, in `cells:` order; the
    # last size repeats, e.g. [medium, small] = first medium, the rest small.
    figsize: Union[str, List[str]] = "medium"
    # profile_plot only: the cycle drawn in the combined voltage-capacity figure
    combined_cycle: int = 1


class GCDMethod3Config(BaseModel):
    enabled: bool = False
    n_cycles: int = 10
    stabilisation_hours: float = 10.0
    qt_hours: float = 10.0
    cycle_hours: float = 1.0


class GCDBatchConfig(BaseModel):
    data_subfolder: str
    # Current density in mA/cm^2 — auto-detected from the folder name
    # (e.g. "GCD1" -> 1.0) unless overridden here.
    current_density_mA_cm2: Optional[float] = None
    cutoff_v: float = 1.0
    rest_hours: float = 10.0
    plating_sign: int = -1
    nuc_frac: float = 0.15
    plateau_frac: float = 0.25
    spike_z_thresh: float = 3.5
    # Where the stripping plateau is read: "end" = last plateau_frac of
    # stripping (fixed-time stripping, e.g. Li-Li cells); "middle" = median
    # of 20-80 % of stripping (stripping to a cut-off voltage, e.g. Na‖Al/Cu,
    # where the end of stripping is the jump to the cut-off).
    strip_plateau: str = "end"
    min_halfcycle_points: int = 10
    method3: GCDMethod3Config = Field(default_factory=GCDMethod3Config)
    # Which cells (file names without extension) the analysis and plots use,
    # in order — one per line, comment a line out (#) to leave that cell out
    # of figures/analysis. Every file is still imported, so the database
    # always holds all the data. Empty (the default) = every file found.
    cells: List[str] = Field(default_factory=list)
    # Optional per-batch rename, file-name sample -> sample name. Normally
    # empty: names and colours live in config.yaml (samples: aliases:).
    sample_names: Dict[str, str] = Field(default_factory=dict)
    # Voltage-vs-capacity profiles: which cycles (plot_gcd draws one figure per sample).
    profile_cycles: List[int] = Field(default_factory=lambda: [1, 2, 3])
    # One block per figure; a figure is drawn only if its block is in gcd.yaml.
    # *_plot = all samples on one axis, *_separate_plot = one figure per sample.
    voltage_plot: Optional[GCDPlotDefaults] = None               # voltage vs time
    voltage_separate_plot: Optional[GCDPlotDefaults] = None
    profile_plot: Optional[GCDPlotDefaults] = None               # voltage vs capacity
    profile_separate_plot: Optional[GCDPlotDefaults] = None
    ce_plot: Optional[GCDPlotDefaults] = None                    # CE vs cycle
    ce_separate_plot: Optional[GCDPlotDefaults] = None
    nucleation_plot: Optional[GCDPlotDefaults] = None            # cycle-1 plating dip
    nucleation_separate_plot: Optional[GCDPlotDefaults] = None
    capacity_plot: Optional[GCDPlotDefaults] = None              # extra per-cycle figures
    lli_plot: Optional[GCDPlotDefaults] = None
    overpotential_plot: Optional[GCDPlotDefaults] = None
    hysteresis_plot: Optional[GCDPlotDefaults] = None

    # "plating" (default): Li-Li plating/stripping, everything above.
    # "supercap": supercapacitor charge-discharge at several current densities
    #   (analysis/gcd/supercap.py) — uses only the fields below.
    mode: str = "plating"
    # Which samples are drawn, one per line — comment a line out (#) to hide it.
    samples: List[str] = Field(default_factory=list)
    gcd_cycle: int = 2                      # which cycle WITHIN each current-density step
    plot: ProfilePlotConfig = Field(default_factory=ProfilePlotConfig)   # xlim = capacity, ylim = voltage
    current_palette: Dict[str, str] = Field(default_factory=dict)       # {"0.5": "#7B1E1E", ...}
    # Several cells of one sample: first | best | median — same rules as rate.yaml;
    # a sample_overrides plot: entry ("<sample><cell>") always wins.
    representative: str = "first"
    sample_overrides: List[EISSampleOverride] = Field(default_factory=list)

    def sample_override(self, name: str) -> Optional[EISSampleOverride]:
        for s in self.sample_overrides:
            if s.name == name:
                return s
        return None


class GCDTechniqueConfig(BaseModel):
    """One gcd.yaml, several batches — e.g. by current density: GCD1, GCD0.5."""
    batches: Dict[str, GCDBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> GCDBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "gcd.yaml")


# XPS is configured by xps.yaml and read by analysis/xps (batches.py, fitting.py),
# not by a schema class here.


# =========================================================
# RIC (Reversible/Irreversible Capacity) — reuses Cycle's raw
# *_Capacity.csv files (same discovery, same file format), no new raw
# format. RIC/RIC_disconn/RIC_sei are DERIVED from Q_Ch/M and Q_Dis/M,
# the same columns Cycle already reads.
# =========================================================

class RICPlotDefaults(AxisStyleConfig):
    """Styling for one RIC/RIC_disconn/RIC_sei figure."""
    save: Optional[str] = None


class RICBatchConfig(BaseModel):
    # Same raw folder Cycle reads for this batch, e.g. "Cycle/Batch1" —
    # RIC is a derived analysis of the exact same *_Capacity.csv files,
    # so pointing at the same data_subfolder as the matching cycle.yaml
    # batch keeps batch_name() (and therefore the database's batch_name
    # column) identical between the two techniques.
    data_subfolder: str
    # Rolling window (in cycles) used to judge trend direction/severity
    # for SEI growth and disconnection (RICAnalyzer) — first N cycles by
    # default, matching the original script.
    trend_window: int = 30
    ric_plot: RICPlotDefaults = Field(default_factory=RICPlotDefaults)
    disconn_plot: RICPlotDefaults = Field(default_factory=RICPlotDefaults)
    sei_plot: RICPlotDefaults = Field(default_factory=RICPlotDefaults)


class RICTechniqueConfig(BaseModel):
    """One ric.yaml, several batches — same pattern as cycle.yaml/eis.yaml."""
    batches: Dict[str, RICBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> RICBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "ric.yaml")


# =========================================================
# SEM — cross-section / surface / powder image quantification.
# Real images auto-discover as "#<position>-<subindex><Sample>.jpg" with
# an optional ".txt" instrument sidecar (SU8200-style) carrying an
# accurate per-image PixelSize (nm/pixel) — used as the scale calibration
# whenever present, instead of the cruder scale_um_fallback guess.
# =========================================================

class SEMGapParams(BaseModel):
    percentile_threshold: float = 5.0
    min_size: int = 100
    is_delaminated: bool = True


class SEMSampleOverride(BaseModel):
    """Per-sample tweak within one role group (cross_section/surface/powder) —
    a pixel crop (top, bottom) and/or gap-detection params, for the samples
    that need them. Matches by the auto-discovered sample name."""
    sample: str
    crop: Optional[Tuple[int, int]] = None
    gap_params: Optional[SEMGapParams] = None


class SEMManualImage(BaseModel):
    """One explicitly-listed image — for raw files that DON'T follow the
    '#<position>-<subindex><Sample>.jpg' auto-discovery convention (e.g.
    legacy loose files). file is relative to paths.data_root."""
    sample: str
    file: str
    color: Optional[str] = None
    crop: Optional[Tuple[int, int]] = None
    gap_params: Optional[SEMGapParams] = None


class SEMRoleGroup(BaseModel):
    """
    One image role within a batch: 'cross_section' (interface/gap/contact
    analysis), 'surface' (particle size + porosity), or 'powder' (particle
    size only). enabled=false (or simply omitting the role) skips it —
    e.g. most batches have no powder images.
    """
    enabled: bool = True
    # Subfolder under data_subfolder holding this role's images, e.g.
    # "Cross Section" / "Surface" — matched case-insensitively against
    # what's actually on disk, since real folders vary ("Cross Section"
    # vs "Cross section").
    folder: Optional[str] = None
    # Used only when a discovered image's filename has no trailing
    # sample-name text (e.g. "#1-1.jpg") — every image in that case
    # belongs to this one sample.
    default_sample: Optional[str] = None
    sample_overrides: List[SEMSampleOverride] = Field(default_factory=list)
    # Escape hatch for images that don't follow the auto-discovery
    # filename convention at all — listed explicitly, same shape as the
    # original per-sample notebook config.
    manual_images: List[SEMManualImage] = Field(default_factory=list)

    def override_for(self, sample: str) -> Optional[SEMSampleOverride]:
        for o in self.sample_overrides:
            if o.sample == sample:
                return o
        return None


class SEMBatchConfig(BaseModel):
    # Raw folder for this batch, e.g. "SEM/20260424_KSS_SEM".
    data_subfolder: str
    # µm-per-pixel used ONLY when an image has no ".txt" sidecar (or the
    # sidecar has no parseable PixelSize) — reproduces the original
    # script's crude estimate (scale bar assumed to span 25% of image
    # width). Prefer real per-image calibration (the sidecar) whenever
    # it's available; this is just the fallback for instruments/exports
    # that don't provide one.
    scale_um_fallback: float = 30.0
    use_sidecar_scale: bool = True

    cross_section: SEMRoleGroup = Field(default_factory=lambda: SEMRoleGroup(folder="Cross Section"))
    surface: SEMRoleGroup = Field(default_factory=lambda: SEMRoleGroup(folder="Surface"))
    powder: SEMRoleGroup = Field(default_factory=lambda: SEMRoleGroup(enabled=False, folder="Powder"))


class SEMTechniqueConfig(BaseModel):
    """One sem.yaml, several batches — one per imaging session/folder."""
    batches: Dict[str, SEMBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> SEMBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "sem.yaml")


# =========================================================
# FTIR — curated-Excel spectrum plotting. No raw-file import step (same
# as the original ftir.py): a curated workbook is the only source, so
# "importing" here means reading that Excel and storing its points, not
# discovering raw instrument files.
# =========================================================

class FTIRPlotDefaults(AxisStyleConfig):
    xlabel: str = "Wavenumber (cm$^{-1}$)"
    invert_x: bool = True
    hide_yaxis: bool = True
    save: Optional[str] = None


class FTIRCurve(BaseModel):
    """One trace: which column in the sheet, and which sample it belongs to."""
    sample: str
    column: str    # exact column header text in the Excel sheet, e.g. "bare Cu"


class FTIRSheet(BaseModel):
    """
    One sheet in the curated workbook, e.g. "R-raw" (Reflectance) or
    "A-raw-bc10" (Absorbance). header_rows=2 matches the real curated
    export (one header row naming the spectrum type, one naming each
    sample); set header_rows=1 for an already-flat sheet (a single
    header row whose column names ARE the sample names/labels).
    """
    sheet: str
    spectrum_type: str = "Reflectance"   # y-axis label / stored label, e.g. "Reflectance" | "Absorbance" | "Transmittance"
    header_rows: int = 2
    curves: List[FTIRCurve] = Field(default_factory=list)
    plot: FTIRPlotDefaults = Field(default_factory=FTIRPlotDefaults)
    # Which curves (by sample name) to draw together, one figure per
    # entry — e.g. [["SampleA", "SampleB"], ["SampleC"]]. Empty -> one figure
    # with every curve in this sheet overlaid.
    figures: List[List[str]] = Field(default_factory=list)


class FTIRBatchConfig(BaseModel):
    # Curated workbook path, relative to paths.data_root.
    excel_path: str
    sheets: List[FTIRSheet] = Field(default_factory=list)


class FTIRTechniqueConfig(BaseModel):
    """One ftir.yaml, several batches (usually one workbook = one batch)."""
    batches: Dict[str, FTIRBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> FTIRBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "ftir.yaml")


# =========================================================
# CV — curated-Excel cyclic voltammetry, one sheet per cell, scan-rate
# indexed columns (Ref1/0.1, Ref2/0.2, ...). No raw-file import step,
# same reasoning as FTIR.
# =========================================================

class CVPlotDefaults(AxisStyleConfig):
    xlabel: str = "Voltage (V)"
    ylabel: str = "Current (A)"
    save: Optional[str] = None
    # Same y-limits across figures, chosen automatically from the data (rounded
    # to nice numbers) — an explicit ylim always wins:
    #   false                         every figure scales itself
    #   true                          one y-range for every figure of this kind
    #   [[5, 20, 50], [100, 200, 500]]  (scan_rate_plot only) one y-range per group
    #                                   of scan rates, e.g. per row of a panel
    share_ylim: Union[bool, List[List[Number]]] = False


class CVCell(BaseModel):
    """
    One cell/sheet in the curated workbook, e.g. sheet "CVRef" with
    columns Ref1/0.1, Ref2/0.2, ... — voltage_prefix + scan_rates
    reconstruct those column pairs (voltage column = f"{voltage_prefix}{i}",
    current column = the scan rate itself, i.e. the literal float column
    header pandas reads, e.g. 0.1) without listing every column by hand.
    """
    sample: str
    sheet: str
    voltage_prefix: str          # e.g. "Ref" or "DS"
    scan_rates: List[float] = Field(default_factory=lambda: [0.1, 0.2, 0.3, 0.4, 0.5])
    color: Optional[str] = None  # falls back to config.yaml's scan-rate palette if unset


class CVBatchConfig(BaseModel):
    excel_path: str
    cells: List[CVCell] = Field(default_factory=list)
    # Scan rate (mV/s or V/s, whatever the raw file used) -> color, shared
    # across every cell in this batch so a reference cell and a doped
    # cell plot with the SAME colors for the SAME scan rate (direct
    # visual comparison, as in the original notebook).
    scan_rate_palette: Dict[str, str] = Field(default_factory=dict)
    plot: CVPlotDefaults = Field(default_factory=CVPlotDefaults)          # CV by composition (one figure per cell)
    scan_rate_plot: CVPlotDefaults = Field(default_factory=CVPlotDefaults)  # CV by scan rate (one figure per rate)
    # Which samples appear in this batch's plots, one per line — comment a
    # line out (#) to hide that sample. Empty / left out = every sample.
    samples: List[str] = Field(default_factory=list)
    # Unit printed after each scan rate in the legend, e.g. "mV/s" ("" = number only).
    scan_rate_unit: str = ""
    # Active mass (g) per sample: when given, current is divided by it and the
    # y label becomes current density (A/g). Samples not listed stay in A.
    active_mass_g: Dict[str, Number] = Field(default_factory=dict)


class CVTechniqueConfig(BaseModel):
    """One cv.yaml, several batches."""
    batches: Dict[str, CVBatchConfig]
    default_batch: Optional[str] = None

    def get_batch(self, name: Optional[str] = None) -> CVBatchConfig:
        return _resolve_batch(self.batches, self.default_batch, name, "cv.yaml")


# =========================================================
# TOP LEVEL
# =========================================================

class ProjectConfig(BaseModel):
    project: ProjectMeta
    paths: PathsConfig = Field(default_factory=PathsConfig)
    cell_defaults: CellMetadata = Field(default_factory=CellMetadata)
    palette: List[str] = Field(default_factory=list)  # optional custom colour order; falls back to discovery.DEFAULT_PALETTE
    # Optional override: {"Bare2": "2026-01-15"} — otherwise the importer
    # uses the earliest raw file's modified-time in that folder.
    experiment_dates: Dict[str, str] = Field(default_factory=dict)
    # Overrides only — auto-discovery fills in anything not listed here.
    samples: List[SampleConfig] = Field(default_factory=list)

    # Not part of the YAML itself — set by the loader so downstream code
    # can resolve relative paths against the config file's own folder.
    _config_dir: Optional[Path] = PrivateAttr(default=None)

    def resolve(self, relative_path: str) -> Path:
        """Resolve a path from the YAML against the config file's folder."""
        base = self._config_dir or Path(".")
        return (base / relative_path).resolve()

    def sample_aliases(self) -> Dict[str, str]:
        """{raw file-name prefix: sample name} from every sample's `aliases:`."""
        return {a: s.name for s in self.samples for a in s.aliases}

    def sample_override(self, name: str) -> Optional[SampleConfig]:
        for s in self.samples:
            if s.name == name:
                return s
        return None

    def sample_by_name(self, name: str) -> SampleConfig:
        """
        Back-compat / convenience: an override if one exists, otherwise a
        bare SampleConfig with just the name set (everything else falls
        back to discovery defaults wherever it's consumed).
        """
        return self.sample_override(name) or SampleConfig(name=name)

    def cell_metadata_for(self, sample_name: str) -> CellMetadata:
        """Project-wide cell_defaults, with that sample's `cell:` overrides applied."""
        override = self.sample_override(sample_name)
        return self.cell_defaults.merged_with(override.cell if override else None)
