# XPS fit validation — this code vs KherveFitting

**Goal:** show once that `fitting.py` reproduces an established fitting program on your own data.
After it passes, you can fit everything in code and cite this check in your methods.

**Time needed:** about 1–2 hours, once.
**Files:** `xps_validate.ipynb` (notebooks folder) and `Exports/XPS_validation_<batch>/`.

---

## Before you start

- [ ] In `xps_validate.ipynb`, set `VALIDATE` to 2–3 spectra that test different things (overlapping
      singlets, a weak doublet, a many-component C 1s).
- [ ] Open `xps.ipynb`, run section 1, and look at the **Fit quality** table for those spectra.
      If a component is listed under *components at BE limit*, decide now whether to widen its window
      in `xps.yaml` (the tuner helps). Validate the model you will actually use.
- [ ] Run section 1 of `xps_validate.ipynb`. It exports to `Exports/XPS_validation_<batch>/`:
  - `<sample>_<region>_corrected.xlsx` for each spectrum in `VALIDATE`. These are already
    charge-corrected and cropped to the fit range, so **do not apply any BE shift in KherveFitting**.
  - `this_code_results.csv`: this code's answer. **Don't look at it until you have fitted in KherveFitting.**
  - `kherve_results.csv`: the empty sheet you fill in.
- [ ] Keep the settings table printed in the notebook (*Settings to use in KherveFitting*) open next to KherveFitting.

## Fit each spectrum in KherveFitting (v2.0, runs on Mac)

KherveFitting's menus may be named differently from what I describe here. What matters is matching
the settings below.

For **each** exported file:

- [ ] **Import** the `_corrected.xlsx` file (Excel import).
- [ ] **Background**
  - [ ] Method: **Shirley** (not *Smart*, which is often the default)
  - [ ] Region: the *bg range* from the settings table
  - [ ] Averaging points: **5**
  - [ ] Offsets: **0**
- [ ] **Peaks:** create one peak per component, at the *start BE* from the settings table.
- [ ] **Constraints** for every peak:
  - [ ] Position limited to the *BE limits* from the settings table
  - [ ] FWHM limited to the *FWHM limits* from the settings table
  - [ ] Line shape: **Gaussian–Lorentzian sum / pseudo-Voigt, 30 % Lorentzian**.
    If only a GL *product* shape is available, use it, but expect FWHM differences of about 0.1 eV
    (see troubleshooting).
- [ ] **Doublets (2p, 3d, …): ** for each species make *two* peaks linked by constraints, using the
      *doublet* column of the settings table:
  - [ ] second line position = main line **+ splitting**
  - [ ] second line area = **ratio ×** main line area
  - [ ] same FWHM
- [ ] **Fit.** Then fit again **2–3 times from different starting positions** (move peaks by hand
      inside their limits) and keep the result with the **lowest residual / χ²**. Overlapping peaks
      have several local minima, and this code also keeps the lowest of 10 starts.
- [ ] **Record** in `kherve_results.csv` (one row per component, names already filled in):
  - [ ] `BE (eV)`: position of the main line (for doublets, the lower-BE line, e.g. 2p3/2)
  - [ ] `FWHM (eV)`: FWHM of the main line
  - [ ] `area`: integrated area. For doublets, **add both lines' areas together**.
  - [ ] Save as **CSV** (if you edit in Excel, keep the CSV format and the column names).

## Compare

- [ ] Run section 2 of `xps_validate.ipynb`. It prints PASS / CHECK for every number and saves `comparison.csv`.

| Check | Passes when | What it tests |
|---|---|---|
| Peak position | \|Δ BE\| ≤ 0.10 eV | the fit itself |
| FWHM | \|Δ FWHM\| ≤ 0.15 eV | line shape |
| Area % (components ≥ 10 %) | \|Δ\| ≤ 3 points **or** within 2× the Monte Carlo error | quantification |
| Area % (components < 10 %) | shown for information only | at this signal level these are not resolvable in either program |
| Total region area | \|Δ\| ≤ 3 % | background |

- [ ] **All PASS:** done. Save `comparison.csv` with your thesis data.

## If something does not match

Work through these in order. Each mismatch has a typical cause.

1. **Total region area differs (background CHECK).** The background is different. Check:
   Shirley rather than Smart, identical start/end energies, 5 averaging points, offsets 0.
   Everything downstream depends on this, so fix it first.
2. **Every BE is shifted by the same amount.** A charge correction was applied twice. The exported
   files are already corrected, so KherveFitting must not shift them.
3. **FWHMs differ by about 0.1–0.2 eV, areas still agree.** Different line-shape formula
   (GL *product* vs *sum*). The fits are equivalent; note it and accept.
4. **Area % of overlapping peaks differs, but each program fits its own result well.** You are
   probably in different local minima. Start KherveFitting from this code's values
   (`this_code_results.csv`) and refit:
   - If its residual is **equal or lower** there, both programs agree on the best solution. ✔
   - If KherveFitting moves away to a solution with a **lower** residual, the code missed the best
     minimum. Increase `n_starts` in `xps.yaml` (e.g. 30), re-run, and compare again.
5. **A component is at its BE limit in one program but not the other.** The windows differ. Re-check
   the limits you typed in.
6. **Still a CHECK after all of this:** keep both results, note the difference, and bring it here.
   We'll look at the residuals side by side.

## What to write in your methods (after it passes)

> XPS spectra were charge-corrected to adventitious C 1s (C–C/C–H) at 284.8 eV and fitted in Python
> (lmfit) using a Shirley background and Gaussian–Lorentzian (30 % Lorentzian) line shapes. Peak
> positions were constrained to literature windows, and P 2p doublets were constrained to a
> spin–orbit splitting of 0.87 eV and a 2:1 area ratio. Each spectrum was fitted from 10 random
> starting points and the lowest-χ² solution was retained. Uncertainties were estimated from 100
> Monte Carlo refits. Atomic concentrations were calculated with Thermo Avantage sensitivity
> factors and a KE^0.75 escape-depth correction. The fitting procedure was validated against
> KherveFitting v2.0 on representative F 1s, P 2p and C 1s spectra (agreement within 0.1 eV in
> position and 3 % in relative area).

*(Adjust the last sentence to the numbers you actually get.)*
