# LIBs: Battery Analysis Software (version 1.0)

LIBs is free, open-source software for analysing battery data. Put the
raw files from your cycler or potentiostat in a folder, describe what you
want in a short YAML file, and run one notebook. LIBs reads the files and
calculates capacity, Coulombic efficiency, impedance, nucleation
overpotential and the other numbers. It saves everything in one database
and draws figures ready for publication.

It runs on **Windows and macOS** (Linux works too).

Version 1.0 replaces the earlier *LIBs: Battery & Materials Analysis
Toolkit* on this GitHub page. In the old version you called functions one
at a time. Version 1.0 is a complete workflow: you supply YAML, and you get
figures, a database and written findings.

---

## Contents

1. [What you need (requirements)](#1-what-you-need-requirements)
2. [Install](#2-install)
3. [Folder layout](#3-folder-layout)
4. [Your first analysis, step by step](#4-your-first-analysis-step-by-step)
5. [Supported instruments](#5-supported-instruments)
6. [What each technique gives you](#6-what-each-technique-gives-you)
7. [The YAML files](#7-the-yaml-files)
8. [The lab book](#8-the-lab-book)
9. [The database](#9-the-database)
10. [Troubleshooting](#10-troubleshooting)
11. [Licence and citing](#11-licence-and-citing)

---

## 1. What you need (requirements)

### Computer

| | Minimum |
|---|---|
| Operating system | Windows 10 or 11 (64-bit), or macOS 12 Monterey or newer (Intel or Apple Silicon) |
| Memory | 4 GB RAM (8 GB recommended for long cycling files) |
| Disk space | about 1.5 GB for Python and the packages, plus room for your data |

### Software

| Software | Required? | What it's for |
|---|---|---|
| **Python 3.10 or newer** (tested on 3.13) | Yes | runs LIBs |
| **pip** (comes with Python) | Yes | installs LIBs and its packages |
| **Jupyter** (JupyterLab, VS Code or PyCharm) | Yes, to use the notebooks | where you run the analysis |
| **Typst** | Only for lab-book PDFs | typesets the lab-book report |

### Python packages

[Step 2](#2-install) installs these for you, so you don't need to install
them one at a time.

| Package | Used for |
|---|---|
| numpy, pandas, scipy | reading data and doing the maths |
| matplotlib | figures |
| pyyaml, pydantic (v2) | reading and checking your YAML files |
| openpyxl | reading Excel files (NEWARE exports, curated CV/FTIR workbooks) |
| opencv-python, scikit-image | SEM image measurements |
| impedance *(optional)* | EIS equivalent-circuit fitting |
| NewareNDA *(optional)* | reading NEWARE `.ndax`/`.nda` files directly |
| jupyterlab *(optional)* | running the notebooks without another editor |

---

## 2. Install

You only need to do this once on each computer.

### Step 1: Install Python

- **Windows:** download Python from <https://www.python.org/downloads/>.
  In the first window of the installer, **tick "Add python.exe to PATH"**,
  then click *Install Now*.
- **macOS:** download Python from <https://www.python.org/downloads/>
  and run the installer. If you use Homebrew, you can run
  `brew install python` instead.

To check that it worked, open **Terminal** (macOS) or **PowerShell**
(Windows) and type:

```bash
python --version
```

You should see `Python 3.10` or higher. On macOS, if `python` is not
found, type `python3` wherever this guide says `python`.

### Step 2: Download LIBs

On GitHub, click the green **Code → Download ZIP** button and unzip the
file. If you use git, run this instead:

```bash
git clone https://github.com/carlostafarampupuni/LIBs.git
```

Put the `LIBs` folder wherever you keep your research, for example in
Documents. It holds the engine (`battery_analysis/`) and an example project
(`Project/`); you add your own projects next to it (see section 3).

### Step 3: Create a virtual environment

A virtual environment keeps the packages LIBs needs separate from
everything else on your computer. In Terminal or PowerShell, go into the
folder:

```bash
cd path/to/LIBs
```

Then create the environment and turn it on.

**macOS:**

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

**Windows (PowerShell):**

```bash
python -m venv .venv
```

```bash
.venv\Scripts\Activate.ps1
```

> On Windows, PowerShell may say that running scripts is disabled. If it
> does, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once,
> then try again.

Once the environment is on, `(.venv)` appears at the start of the line.
Turn it on again every time you open a new terminal to work with LIBs.

### Step 4: Install LIBs

```bash
pip install -e ".[all]"
```

This installs LIBs together with all the optional extras: EIS fitting,
native NEWARE files and JupyterLab. For the basics only, run
`pip install -e .` instead.

The `-e` stands for "editable". LIBs runs straight from this folder, so
the notebooks, YAML files and lab book stay together. Any change you make
to the code takes effect at once.

### Step 5: Install Typst (only for lab-book PDFs)

- **macOS:** `brew install typst`
- **Windows:** `winget install --id Typst.Typst`

You can also download Typst from <https://github.com/typst/typst/releases>
and add it to your PATH.

### Step 6: Check the installation

```bash
python -c "from battery_analysis.project import Project; Project('Project'); print('LIBs is ready')"
```

---

## 3. Folder layout

LIBs is **one engine and any number of projects**. The engine is installed
once (Step 4); every project folder uses it.

```
LIBs/
├── battery_analysis/     ← the engine: parsing, analysis, database, plotting
│                           (you don't need to open this)
├── Project/              ← an example project — copy it to start a new one
│   ├── Data/               your raw instrument files, one folder per technique
│   │   ├── EIS/  Cycle/  Rate/  GCD/  XPS/  CV/  FTIR/  SEM/
│   ├── notebooks/          every notebook AND every YAML, side by side
│   │   ├── config.yaml       shared: project name, samples, colours, cell chemistry
│   │   ├── gcd.yaml  eis.yaml  cycle.yaml  rate.yaml  dqdv.yaml  drt.yaml ...
│   │   ├── gcd.ipynb eis.ipynb cycle.ipynb rate.ipynb dqdv.ipynb drt.ipynb ...
│   │   ├── run_all.ipynb     one click: every technique, every batch
│   │   ├── story.ipynb       every technique's findings in one report
│   │   ├── labbook.ipynb     lab book: masses to weigh, PDF reports
│   │   └── lab_calculator.ipynb  N/P ratio and electrode calculator
│   ├── Figures/            figures are saved here automatically, one subfolder per technique
│   ├── database/           project.db is created here the first time you import
│   ├── exports/            optional Excel/CSV snapshots
│   └── LabBook/            electronic lab notebook (see section 8)
├── NewStudy/             ← your next study: a copy of Project with its own data
└── pyproject.toml        ← install information
```

**Each project folder holds one study.** To start a new one, copy
`Project/` (rename it, e.g. `LMR_cathode/`), empty its `Data/`, and edit
its `notebooks/config.yaml`. Projects never contain a copy of the engine, so
a fix to `battery_analysis/` reaches every project at once. A project can
also live anywhere else on your computer: it only needs the environment
where LIBs is installed.

---

## 4. Your first analysis, step by step

All paths in this section and the next are inside your project folder
(for example `Project/`).

The example below uses GCD (plating/stripping), and every other technique
works the same way.

### 1. Put your raw files in `Data/`

Make a folder for the batch and copy the instrument files into it:

```
Data/GCD/Batch1/
    SampleA-1.xlsx
    SampleA-2.xlsx
    SampleB-1.xlsx
```

LIBs only reads your raw files, so never edit them.

### 2. Name your samples in `notebooks/config.yaml`

Give each sample one name and one colour, which every figure uses. Under
`aliases`, list the file-name prefixes that belong to that sample:

```yaml
samples:
  - name: SampleA
    color: "#6B7280"
    aliases: [SampleA]
  - name: SampleB
    color: "#000000"
    aliases: [SampleB]
```

At the top of the same file, set `project: name:` and `cell_defaults:`
(electrolyte, temperature and so on). The `config.yaml` that comes with
LIBs has six example samples (SampleA to SampleF), so replace them with your own.

### 3. Describe the batch in the technique's YAML

Open `notebooks/gcd.yaml` and point a batch at your folder:

```yaml
default_batch: Batch1
batches:
  Batch1:
    data_subfolder: GCD/Batch1
    current_density_mA_cm2: 1.0
    cutoff_v: 1.0
    cells:
      - SampleA-1
      #- SampleA-2        # commented out = not analysed, not drawn
      - SampleB-1
```

**To hide something, comment its line out with `#`.** This is the only
way to hide anything. LIBs analyses and draws every cell in the list, and
draws every figure block that isn't commented out.

### 4. Run the notebook

Turn on the environment, then start Jupyter from the LIBs folder:

```bash
jupyter lab
```

Open `notebooks/gcd.ipynb` and run the cells from the top. The first cell
sets the project up, and each cell after it handles one batch:

```python
project.import_gcd(batch="Batch1")   # read the raw files into the database
project.analyze_gcd()                # calculate the numbers
project.plot_gcd()                   # draw and save the figures
```

You can also open the notebooks in **VS Code** or **PyCharm**. Select the
`.venv` you created as the Python interpreter (kernel).

### 5. Look at the results

- **Figures:** `Figures/GCD/` (SVG files, ready for a paper or slides).
  Each figure's exact data and style go to `exports/PlotData/` (same
  sub-folders as the figure), so it can be redrawn in Origin, SigmaPlot or
  Excel. `Figures/` holds only figures.
- **Numbers:** `database/project.db`.
- **Written findings:** printed in the notebook. They compare every sample
  with the first sample in your list.

To run every technique and every batch at once, run
`notebooks/run_all.ipynb`. If one batch fails, LIBs reports it and skips
it, so one bad file never stops the others.

---

## 5. Supported instruments

Version 1.0 reads raw files from these five instruments.

| Instrument | Software / export | Techniques | Where the sample name comes from |
|---|---|---|---|
| **IVIUM** (Iviumstat) | IviumSoft text export | EIS, DRT | the sample folder name |
| **BioLogic** | EC-Lab `.mpt` ASCII export | EIS, DRT | the sample folder name |
| **NEWARE** | BTSDA `.xlsx` / `.csv` export, or native `.ndax` / `.nda` | GCD / CE (plating/stripping) | the file name |
| **WonATech** | WBCS `*_Capacity.csv` + `*_Cycle.csv`; ZIVE ASCII `.csv` | Cycle, Rate, RIC, dQ/dV; GCD / CE | the file name |
| **ESPEC** | `*_All.csv`, one file per channel | Cycle, Rate | the YAML (`espec_channels:`) |

You never need to tell LIBs which instrument a file came from, because it
recognises the format from the file's contents. Different batches can come
from different instruments, and one EIS folder can mix IVIUM and BioLogic
files.

### IVIUM: EIS

- **File:** a tab-separated `.txt` file with three columns: frequency
  (Hz), Z′ (Ω) and Z″ (Ω). The header is on the first row.
- **Where:** `Data/EIS/<batch>/<sample>/<file>.txt`, with one folder per
  sample. The file name gives the condition, for example `3.0 V.txt` or
  `defaultdata_1.txt`.
- **New batches need no YAML:** `eis.yaml` contains `auto_discover:`, so
  every new folder in `Data/EIS/` becomes a batch automatically.

### BioLogic: EIS

- **File:** the EC-Lab `.mpt` ASCII export, which starts with the line
  `EC-Lab ASCII FILE`. In EC-Lab, use *Export as Text* to create it.
- **Where:** `Data/EIS/<batch>/<sample>/<file>.mpt`, the same layout as
  IVIUM.
- **Several sweeps in one file:** if your EC-Lab protocol repeated the
  EIS sweep, LIBs uses the file's `cycle number` column to split it into
  separate spectra. The sweeps are never joined into one curve.
- LIBs ignores the channel suffix that EC-Lab adds (for example `_C02`)
  when it matches file names.

### NEWARE: GCD / CE (plating and stripping)

- **Files, best option first:**
  1. The native `.ndax` / `.nda` file. It reads about 10× faster than the
     Excel export and holds the same data. It needs the optional
     `NewareNDA` package, which `pip install -e ".[all]"` installs.
  2. The BTSDA **Export → Excel** file (`.xlsx`). LIBs reads the `record`
     sheet. When a long test is split over several sheets, LIBs joins the
     sheets back together. Excel and CSV files are parsed once and then
     loaded from a cache (`~/.cache/battery_analysis/neware`).
  3. The BTSDA CSV export of the record data (`.csv`).
- **Both formats for the same cell** (same file name, e.g. `SampleA-1.ndax`
  and `SampleA-1.xlsx`): LIBs uses the `.ndax` and ignores the Excel copy.
- **Where:** `Data/GCD/<batch>/`. Name each file
  `<sample>-<replicate>.xlsx`, for example `SampleA-1.xlsx`.
- **Columns:** LIBs finds the columns by meaning rather than exact
  spelling, so exports from different BTSDA versions and languages work
  (`Voltage(V)`, `电压(V)`, `Current(A)` or `Current(mA)`, and so on).
  Units are converted to V, mA and mAh.
- **In `gcd.yaml`:** set `cutoff_v` (the stripping cut-off in your step
  plan), `plating_sign: -1` and `current_density_mA_cm2`.
- **Capacity:** NEWARE resets the capacity at the start of every step, so
  LIBs adds up the steps to get the capacity of each half-cycle.
- **Limit in version 1.0:** only GCD/CE reads NEWARE files. NEWARE support
  for Cycle and Rate is planned for a later version.

### WonATech: Cycle, Rate, RIC, dQ/dV and GCD / CE

LIBs reads two kinds of WonATech export.

**WBCS cycling export (Cycle, Rate, RIC, dQ/dV)**

- **Files:** a pair of files for each cell. `<sample>_<n>_Capacity.csv`
  holds the charge and discharge capacity of each cycle, already in
  mAh/g. `<sample>_<n>_Cycle.csv` holds the full voltage–capacity profile.
- **Where:** `Data/Cycle/<batch>/` or `Data/Rate/<batch>/`. LIBs reads the
  sample name and replicate number from the file name.
- **New batches need no YAML:** `cycle.yaml` and `rate.yaml` contain
  `auto_discover:`.
- RIC and dQ/dV read the same files, so you don't need to export anything
  extra.
- LIBs tolerates small differences in the header, such as extra spaces or
  different capitals. If a column is missing, LIBs skips that one file
  and prints a warning listing the columns it did find. The rest of the
  batch still imports.

**ZIVE ASCII export (GCD / CE, symmetric-cell plating/stripping)**

- **File:** the ZIVE `.csv` ASCII export, with time written as
  `d:hh:mm:ss.ms`.
- **Where:** `Data/GCD/<batch>/`.
- **In `gcd.yaml`:** set `cutoff_v` and `rest_hours` (the length of the
  OCV rest at the start, which LIBs skips).

### ESPEC: Cycle and Rate

- **File:** one `*_All.csv` for each **channel**, for example
  `C1S1CH10_LFP-CYC_All.csv`.
- **Where:** `Data/Cycle/<batch>/` or `Data/Rate/<batch>/`.
- **The YAML must give the sample and active mass for each channel.** An
  ESPEC file records capacity in mAh and contains neither the sample name
  nor the electrode mass, so list each channel yourself:

  ```yaml
  batches:
    Batch3:
      data_subfolder: Cycle/Batch3
      espec_channels:
        - channel: C1S1CH10
          sample: Bare
          replicate: 1
          active_mass_g: 0.00412     # required; the channel is skipped without it
        - channel: C1S1CH11
          sample: Bare
          replicate: 2
          active_mass_g: 0.00398
  ```

- If a channel has no `active_mass_g`, LIBs **skips it and prints a
  warning** rather than guessing. A guessed mass would silently put the
  wrong mAh/g into every figure.
- Setting `espec_channels:` makes that batch an ESPEC batch. Other batches
  in the same YAML can still be WonATech.
- **Limit in version 1.0:** for ESPEC Rate data, LIBs imports the capacity
  and CE summary. Voltage-profile figures for ESPEC Rate data are planned
  for a later version.

---

## 6. What each technique gives you

| Technique | Notebook | Results |
|---|---|---|
| **GCD / CE** | `gcd.ipynb` | voltage vs time, voltage vs capacity, Coulombic efficiency, capacity, nucleation overpotential, hysteresis, soft-short detection, written findings |
| **Cycle** | `cycle.ipynb` | capacity retention, CE, capacity fade, end-of-life (80 %) forecast, plus an early forecast from the first 10 cycles |
| **Rate** | `rate.ipynb` | rate capability with lines marking each C-rate change |
| **RIC** | `ric.ipynb` | reversible and irreversible capacity trends |
| **dQ/dV** | `dqdv.ipynb` | differential capacity, peaks tracked across cycles (signals resembling LAM and LLI) |
| **EIS** | `eis.ipynb` | Nyquist plots, equivalent-circuit fitting (Rs, Rsei, Rct, Warburg), and which resistance changes most |
| **DRT** | `drt.ipynb` | distribution of relaxation times, peaks, heatmaps (built from the EIS data, so run EIS first) |
| **XPS** | `xps.ipynb` | automatic peak fitting of raw spectra (Avantage workbooks or 2-column files) or plotting of already-fitted exports (XPSPeak layout), per batch: charge correction, Shirley/Tougaard backgrounds, constrained peaks and doublets, Monte Carlo errors, atomic %, survey and stacked survey; peak models in `xps.yaml` |
| **CV** | `cv.ipynb` | overlays at each scan rate (from a curated Excel workbook) |
| **FTIR** | `ftir.ipynb` | spectra (from a curated Excel workbook) |
| **SEM** | `sem.ipynb` | thickness, gap, porosity and particle-size measurements from images |
| **Tafel** | `tafel.ipynb` | i0 and α from galvanostatic pulses by four methods (workbook-style, Butler–Volmer, Tafel region, linear), optional IR correction, check against a hand-built workbook |
| **GITT** | `gitt.ipynb` | diffusion coefficients (classic Weppner–Huggins with validity check, and the √t pulse method), OCV vs state |
| **Curated workbook** | `sheets.ipynb` | house-style figures straight from a hand-built workbook (`sheets.yaml`): cycling, CE, rate, profiles, GCD, EIS, Tafel, FTIR |
| **Story** | `story.ipynb` | the findings of every technique in one report |
| **Features** | `project.export_features()` | a CSV with one row per sample and every calculated number |

---

## 7. The YAML files

- **`config.yaml`** holds what is true for the whole project: the project
  name, the folders, sample names and colours, and the cell chemistry.
- **`<technique>.yaml`** holds the settings you adjust for that technique:
  the raw folder, the cells, axis limits, tick spacing, figure size and
  fitting settings.

Every technique YAML has the same structure:

```yaml
default_batch: Batch1
batches:
  Batch1: {data_subfolder: GCD/Batch1, ...}
  Batch2: {data_subfolder: GCD/Batch2, ...}
```

Choose a batch once in a notebook, for example with
`project.import_gcd(batch="Batch2")`. Every later call for that technique
uses the same batch until you choose another.

**Figures.** Each figure has its own block in the YAML. You set the axis
limits (`xlim: [0, 100]`, `ylim: [-0.2, 1.0]`), the tick spacing
(`xtick_step`, `ytick_step`) and the size (`figsize: small | medium |
large`). Many figures have two blocks: `<name>_plot` puts all samples on
one axis, and `<name>_separate_plot` draws one figure per sample. If you
comment a block out, that figure isn't drawn.

**Lab-book links.** Instead of typing a value twice, any YAML value can
point to the lab book, for example `active_mass_g: labbook:2026-10-24a/C1`.

Every YAML file that comes with LIBs has a comment on each setting.
Before you change a setting, read the comments in that file.

---

## 8. The lab book

`LabBook/` is an electronic lab notebook. You write one YAML file per
experiment, and LIBs turns it into a PDF report.

- LIBs works out recipes and weighing maths for you: mixtures as wt% of
  the final total, molar solutions, dose series, and the actual wt% from
  what you really weighed.
- Chemicals and stock solutions go in `LabBook/materials.yaml`.
- Photos go in `Figures/Photos/<entry id>/`.

Use these commands from the terminal, with the environment turned on:

```bash
labbook new "AdditiveA in NaPF6 diglyme" --template electrolyte_additive
```

```bash
labbook calc latest
```

```bash
labbook report latest
```

`labbook templates` lists the templates you can start from, and
`labbook list` shows every entry. You can do the same things in
`notebooks/labbook.ipynb`. The full guide is
`LabBook/HOW TO - Lab book.md`. PDF reports need Typst
([install step 5](#step-5-install-typst-only-for-lab-book-pdfs)).

---

## 9. The database

`database/project.db` is a single SQLite file. LIBs creates it the first
time you import anything. Each imported cell becomes an `experiment`
record with its sample, raw file, replicate, electrodes, electrolyte,
temperature and date. That means you can search across all techniques
later.

To look inside without writing code, open the file with the free
[DB Browser for SQLite](https://sqlitebrowser.org/). From a notebook:

```python
project.find_experiments()                     # every cell ever imported
project.repo.list_samples(project.project_id)  # every sample
```

When a newer version of LIBs adds columns, an older `project.db` updates
itself automatically, so you never need to delete it.

---

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| Windows says `python` is not recognised | Reinstall Python and tick **Add python.exe to PATH**. |
| macOS can't find `python` | Use `python3`. |
| `ModuleNotFoundError: battery_analysis` | Turn the environment on (`source .venv/bin/activate` or `.venv\Scripts\Activate.ps1`) and select `.venv` as the notebook kernel. |
| A cell or channel is "skipped" | Read the warning. It names the file and what was missing (a column, `active_mass_g`, ...). |
| A legend shows the file name instead of the sample name | Add the file prefix to that sample's `aliases:` in `config.yaml`. |
| A figure is empty or cut off | Widen `xlim` / `ylim` in that figure's YAML block. |
| A script run outside Jupyter stops at a figure | Set the backend first: `MPLBACKEND=Agg` (macOS) or `$env:MPLBACKEND="Agg"` (Windows PowerShell). |
| A NEWARE `.ndax` won't open | Run `pip install NewareNDA`, or use the BTSDA Excel export. |
| The lab book says "Typst isn't installed" | See [install step 5](#step-5-install-typst-only-for-lab-book-pdfs). |
| EIS fitting is not available | Run `pip install impedance`. |

---

## 11. Licence and citing

LIBs is free software under the MIT licence (see `LICENSE`). You may use,
copy, change and share it, including in commercial work.

If LIBs helps your research, please cite it:

> C. Tafara Mpupuni, *LIBs: battery analysis software*, version 1.0 (2026).
> https://github.com/carlostafarampupuni/LIBs

To ask a question or report a bug, open an issue on the GitHub page and
attach a small example of the raw file.
