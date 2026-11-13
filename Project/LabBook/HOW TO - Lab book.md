# HOW TO — Lab book

Your electronic lab notebook. Each experiment is one YAML file. The
computer does the weighing maths, and one click prints a PDF of
everything: conditions, materials, masses, discs, cells, notes and photos.

```
Results/
├── LabBook/
│   ├── entries/          ← one YAML per experiment   (YOU write these)
│   ├── templates/        ← starting points for new entries
│   ├── materials.yaml    ← chemicals + stock solutions, written once
│   ├── settings.yaml     ← name / affiliation / project for the PDF header
│   └── reports/          ← PDFs appear here
├── Figures/Photos/<id>/  ← photos for that entry
├── notebooks/            ← all notebooks AND all YAMLs (config.yaml, cycle.yaml …)
└── battery_analysis/labbook/   ← the code (recipes.py = the maths, templates/report.typ = the look)
```

---

## 1. The everyday loop

| What | PyCharm (Run menu → **Lab book** folder) | Terminal (in `Results/`) |
|---|---|---|
| Start an experiment | *Lab book: new entry (asks)* | `python -m battery_analysis.labbook new "2.5 wt% AdditiveA in 1 M NaPF6/G2" --template electrolyte_additive` |
| See what to weigh | open the entry, *Lab book: masses for the open entry* | `python -m battery_analysis.labbook calc 2026-10-22a` |
| Print the PDF | open the entry, *Lab book: PDF of the open entry* | `python -m battery_analysis.labbook report 2026-10-22a` |
| Print everything | *Lab book: PDF of whole book* | `python -m battery_analysis.labbook report all` |
| One month only | — | `… report all --since 2026-10-01 --until 2026-10-31` |
| List entries | *Lab book: list entries* | `python -m battery_analysis.labbook list` |

"Open entry" means the YAML file is the active editor tab. PyCharm passes
its file name to the command.

If you prefer cells, `notebooks/labbook.ipynb` does the same things.

**Entry ids** are the start date plus a letter: `2026-10-22a`, `2026-10-22b`
for a second experiment that day. `new` picks the next free letter for you
and makes the photo folder.

---

## 2. Recipes: what you write, what you get

All percentages are **wt% of the final total mass**, the same as in the paper
("2.5 wt% AdditiveA dispersed in 1 M LiPF6 …": AdditiveA ÷ (AdditiveA + electrolyte)).

Each line in `components:` is **one thing you put on the balance**. It needs
either `wt_pct: n` or `balance: true`. Exactly one line is the balance.

### a) Additive in an electrolyte (today's experiment)

```yaml
recipes:
  - id: E-0                       # the label on the bottle
    name: 1 M NaPF6 in G2
    type: molar_solution
    salt: NaPF6
    solvent: G2
    molarity_M: 1.0
    volume_mL: 10                 # -> NaPF6 = 1 × 0.010 × 167.95 = 1.6795 g

  - id: L-1
    name: 1 wt% AdditiveA in 1 M NaPF6/G2
    total_g: 5.0
    components:
      - {name: AdditiveA, wt_pct: 1.0} # -> 0.0500 g
      - {name: E-0, balance: true}  # -> 4.9500 g of the electrolyte above
```

**Next time, 2.5 %:** start a new entry and change `1.0` to `2.5`. That's all.

**A whole series at once** (like L-0, L-2.5, L-5, L-7.5 in the paper):

```yaml
    vary: {component: AdditiveA, wt_pct: [0, 2.5, 5, 7.5]}
```

This gives recipes `L-0`, `L-2.5`, `L-5`, `L-7.5`, each with its own table.

**Why molar solutions say "top up to the mark":** 1 M means per litre of
*solution*, so the honest way is a volumetric flask. If you weigh 1.000 mL of
the finished electrolyte once and add `solution_density_g_mL: 1.09`, the
calculator also gives the G2 mass. The final composition then lists
NaPF6 / diglyme / AdditiveA separately instead of "1 M NaPF6 in G2".

### b) Additive in a binder solution you already have

```yaml
      - {name: AdditiveA, wt_pct: 1.0}
      - {name: 8 wt% PVDF in NMP, balance: true}   # a stock from materials.yaml
```

Result: 0.05 g AdditiveA + 4.95 g stock. The report shows that PVDF ends at
**7.92 wt%** and NMP at 91.08 wt%.

### c) Several things from scratch

```yaml
      - {name: AdditiveA, wt_pct: 1.0}
      - {name: PVDF-HFP, wt_pct: 10}      # change to 8 for the other version
      - {name: Super P, wt_pct: 0.5}      # another additive? add a line
      - {name: NMP, balance: true}        # NMP adjusts itself
```

**A solute from a stock solution:** say which stock delivers it:

```yaml
      - {name: PVDF-HFP, wt_pct: 10, from: 15 wt% PVDF-HFP in NMP}
```

The calculator weighs 6.6667 g of the stock (per 10 g) and takes the NMP that
stock carries off the NMP line automatically.

### Batch size
- `total_g: 5` is the size of the finished mixture, **or**
- `basis: {E-0: 4.95}` means "I have 4.95 g of E-0, make the rest fit".

### After weighing: record what the balance actually read

```yaml
    weighed: {AdditiveA: 0.0503, E-0: 4.9511}
```

The PDF then shows **Target wt%** next to **Actual wt%**. Use the actual
value in the paper. For a `vary:` series, key it by recipe:
`weighed: {L-2.5: {AdditiveA: 0.1253, E-0: 4.8771}}`.

### Using a recipe from an earlier day

Use `name: 2026-10-20a/E-0` (or `from: 2026-10-20a/E-0`). This takes recipe
E-0 from that entry, so you don't have to type it again.

---

## 3. Photos

1. Put the photo in `Figures/Photos/<entry id>/`, for example
   `Figures/Photos/2026-10-22a/L-1_after-1h.jpg`. iPhone `.heic` is fine;
   it's converted for the PDF and your original is never changed.
2. Put it where you want it in any text (log, notes, results, procedure),
   much like `\includegraphics` in LaTeX:

```yaml
log:
  - time: "14:30"
    text: |
      Clear and slightly yellow after 1 h — complete dissolution.
      #photo("L-1_after-1h.jpg", caption: [L-1 after 1 h shaking], width: 50%)
```

- A photo in the folder that you never mention still appears under
  **Other photos** at the end, so nothing is lost.
- A misspelt name shows a red "Photo not found" box instead of crashing.
- Other folders work too: `#photo("Figures/SEM/cross-section.png")`.

## 4. Text formatting (Typst, same as your social posts)

`*bold*`, `_italic_`, `- bullet`, `+ numbered`, additive A or
`#chem("Li2CO3")` for subscripts. Names in the tables (Li2CO3, NaPF6, C6H14O3)
get subscripts automatically.

If the PDF fails, the error message names the line. It's almost always an
unclosed `*` or `$`, or a `#` in front of a word. Write `\#` or `\$` for the
literal symbol.

---

## 5. Electrodes, N/P, cells → straight into analysis

Template `electrodes_and_cells` has the full layout. Put the shared numbers
(foil weight, diameter, AM fraction, capacity) in `electrode_defaults:` and
only what changes on each disc:

```yaml
electrodes:
  - {name: C1, sample: Bare, disc_weight_g: 0.0205, disc_thickness_mm: 0.058}
```

You get coating mass, AM mass, thickness, density, loading, areal capacity,
1C current and C-rate currents in the calculation output and the PDF.

**Use the mass in your analysis without typing it again.** In `cycle.yaml` or
`rate.yaml`:

```yaml
        active_mass_g: labbook:2026-10-24a/C1
```

Any other value works too: `labbook:2026-10-24a/C1.loading_mg_cm2`.
If you correct the disc weight in the lab book, the analysis uses the new
number next time it runs.

---

## 6. Changing things yourself

| I want to… | Edit |
|---|---|
| add a chemical or a stock solution | `LabBook/materials.yaml` (copy a block) |
| make a new kind of entry I'll reuse | copy a file in `LabBook/templates/`, rename it |
| change colours, fonts, layout of the PDF | `battery_analysis/labbook/templates/report.typ` (colours at the top) |
| change the name/affiliation in the footer | `LabBook/settings.yaml` |
| check the maths still holds | `python -m battery_analysis.labbook.test_labbook` |

The research brand colour is blue `#0071E3`. Carltabie gold is not used here.

---

## 7. NEWARE

`battery_analysis/analysis/cycling_common/neware_io.py` reads NEWARE native
files (`.ndax`/`.nda`, needs `pip install NewareNDA` once) and BTSDA Excel/CSV
exports, in English or Chinese headers, with A/mA and Ah/mAh units.
It has only been tested on synthetic files so far. When the first real
file arrives:

```python
from battery_analysis.analysis.cycling_common.neware_io import NewareParser
p = NewareParser("../Data/Cycle/<folder>/<file>", active_mass_g=0.0032)
print(p.describe())        # which columns it found
p.cycle_summary()          # compare with BTSDA's own cycle sheet
```

When it matches BTSDA's numbers, we'll connect it to `cycle.yaml`/`rate.yaml`
the same way the ESPEC channels are.
