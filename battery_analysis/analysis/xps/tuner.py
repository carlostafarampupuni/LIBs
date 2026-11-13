"""
Interactive XPS peak tuner for Jupyter / PyCharm notebooks (ipywidgets).

    from battery_analysis.analysis.xps.tuner import tune
    tune("../Data/XPS/surface_total.xls", "xps.yaml", region="F1s")

What it gives you, per region:
  * sample + background + fit-range controls
  * per component: on/off, start position, allowed BE window, start FWHM
  * live preview of the starting guess as you move sliders
  * [Fit this sample]  [Fit all samples]  [Use fitted values]  [Save to YAML]

"Save to YAML" writes your windows/positions back into the model file
(a .bak copy is kept), so the batch fit uses exactly what you tuned.
Components you switch off are commented out, not deleted.
"""
from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path

import numpy as np

from . import fitting as xf


# ──────────────────────────────────────────────────────────────────────
# YAML write-back (text edit, so your comments and layout survive)
# ──────────────────────────────────────────────────────────────────────
def _region_block(lines, region):
    start = next((i for i, l in enumerate(lines) if re.match(rf"^  {re.escape(region)}:\s*$", l)), None)
    if start is None:
        raise KeyError(f"Region '{region}' not found in YAML")
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^  \S", lines[i]) or re.match(r"^\S", lines[i])), len(lines))
    return start, end


def _set_key(line: str, key: str, value: str) -> str:
    pat = rf"(?<![\w_]){key}:\s*(\[[^\]]*\]|[^,}}]+)"
    if re.search(pat, line):
        return re.sub(pat, lambda _: f"{key}: {value}", line, count=1)
    i = line.rfind("}")
    return line if i < 0 else line[:i].rstrip() + f", {key}: {value}}}" + line[i + 1:]


def save_region_to_yaml(yaml_path, model: xf.RegionModel, enabled: dict) -> Path:
    """
    Write fit_range, background and each component's center / window / fwhm
    back into the YAML text. `enabled` = {component name: bool}.
    """
    path = Path(yaml_path)
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end = _region_block(lines, model.region)

    def region_key(key, value):
        nonlocal end
        for i in range(start + 1, end):
            if re.match(rf"^    {key}:", lines[i]):
                lines[i] = re.sub(rf"^(    {key}:\s*)(\[[^\]]*\]|\S+)", rf"\g<1>{value}", lines[i])
                return
        lines.insert(start + 1, f"    {key}: {value}")
        end += 1

    lo, hi = model.fit_range
    region_key("fit_range", f"[{lo:.2f}, {hi:.2f}]")
    region_key("background", model.background)

    for c in model.components:
        idx = next((i for i in range(start, end)
                    if re.search(rf'name:\s*"?{re.escape(c.name)}"?\s*[,}}]', lines[i])), None)
        if idx is None:
            continue
        line = lines[idx].lstrip("# ").rstrip()
        indent = re.match(r"^\s*", lines[idx].replace("#", " ")).group(0)
        line = indent + line.strip()
        line = _set_key(line, "center", f"{c.center:.2f}")
        line = _set_key(line, "window", f"[{c.window[0]:.2f}, {c.window[1]:.2f}]")
        line = _set_key(line, "fwhm", f"{c.fwhm:.2f}")
        if not enabled.get(c.name, True):
            line = re.sub(r"^(\s*)", r"\1# ", line, count=1)
        lines[idx] = line
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────────────
# The widget
# ──────────────────────────────────────────────────────────────────────
def tune(source, yaml_path, region: str = "C1s", sample=None):
    import ipywidgets as W
    import matplotlib.pyplot as plt
    from IPython.display import display

    cfg = xf.load_fit_config(yaml_path)
    if region not in cfg["regions"]:
        raise KeyError(f"Region '{region}' not in {yaml_path} — add it there first")
    base_model: xf.RegionModel = cfg["regions"][region]
    data = xf.read_avantage_workbook(source)
    if region not in data:
        raise KeyError(f"No '{region} Scan' sheet in {source}")
    df = data[region]
    samples = list(df.columns[1:])
    shifts = xf.compute_shifts(data, cfg["settings"], samples)

    xs = df["BE"].values
    full_lo = float(min(xs) + max(shifts.values(), default=0))
    full_hi = float(max(xs) + min(shifts.values(), default=0))
    fr = base_model.fit_range or (full_lo, full_hi)

    lay = W.Layout(width="330px")
    sty = {"description_width": "70px"}
    w_sample = W.Dropdown(options=samples, value=sample or samples[0], description="Sample", style=sty)
    w_bg = W.Dropdown(options=["shirley", "smart", "tougaard", "linear"], value=base_model.background,
                      description="Background", style=sty)
    w_range = W.FloatRangeSlider(value=(max(fr[0], full_lo), min(fr[1], full_hi)), min=full_lo, max=full_hi,
                                 step=0.05, description="Fit range", continuous_update=False,
                                 layout=W.Layout(width="520px"), style=sty, readout_format=".2f")
    w_share = W.Checkbox(value=base_model.share_fwhm, description="Shared FWHM", indent=False)

    rows, ctl = [], []
    for c in base_model.components:
        on = W.Checkbox(value=True, description=c.name, indent=False, layout=W.Layout(width="190px"))
        cen = W.FloatSlider(value=c.center, min=full_lo, max=full_hi, step=0.05, description="start",
                            continuous_update=False, layout=lay, style={"description_width": "36px"},
                            readout_format=".2f")
        win = W.FloatRangeSlider(value=c.window, min=full_lo, max=full_hi, step=0.05, description="window",
                                 continuous_update=False, layout=lay, style={"description_width": "48px"},
                                 readout_format=".2f")
        fw = W.FloatSlider(value=c.fwhm, min=0.3, max=4.0, step=0.05, description="FWHM",
                           continuous_update=False, layout=W.Layout(width="230px"),
                           style={"description_width": "40px"}, readout_format=".2f")
        ctl.append((c, on, cen, win, fw))
        rows.append(W.HBox([on, cen, win, fw]))

    b_fit = W.Button(description="Fit this sample", button_style="primary", icon="play")
    b_all = W.Button(description="Fit all samples", icon="th")
    b_use = W.Button(description="Use fitted values", icon="arrow-up")
    b_save = W.Button(description="Save to YAML", button_style="success", icon="save")
    msg = W.HTML()
    out = W.Output()
    state = {"last": None}

    def current_model() -> tuple:
        m = copy.deepcopy(base_model)
        m.background = w_bg.value
        m.fit_range = tuple(w_range.value)
        m.share_fwhm = w_share.value
        comps, enabled = [], {}
        for c, on, cen, win, fw in ctl:
            cc = copy.deepcopy(c)
            lo, hi = win.value
            cc.center = float(np.clip(cen.value, lo, hi))
            cc.window = (float(lo), float(hi))
            cc.fwhm = float(fw.value)
            cc.fwhm_bounds = (min(cc.fwhm_bounds[0], cc.fwhm), max(cc.fwhm_bounds[1], cc.fwhm))
            enabled[c.name] = on.value
            if on.value:
                comps.append(cc)
        m.components = comps
        return m, enabled

    def spectrum(s):
        return df["BE"].values, df[s].values, shifts.get(s, 0.0)

    def draw(o, title):
        with out:
            out.clear_output(wait=True)
            fig, (ax, rax) = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True,
                                          gridspec_kw={"height_ratios": [4, 1]})
            xf.draw_fit(ax, o, res_ax=rax, title=title)
            for c, on, cen, win, fw in ctl:
                if on.value:
                    ax.axvspan(*win.value, color="0.85", alpha=0.25, lw=0)
            ax.invert_xaxis()
            ax.legend(fontsize=7, ncol=2, frameon=False)
            ax.set_ylabel("Counts / s")
            rax.set_xlabel("Binding energy (eV)")
            fig.tight_layout()
            plt.show()
            if o.fitted:
                cols = [c for c in ("component", "BE (eV)", "FWHM (eV)", "shape", "area %",
                                    "± area %", "at BE limit") if c in o.table.columns]
                display(o.table[cols].round(2))

    def preview(*_):
        m, _ = current_model()
        be, y, sh = spectrum(w_sample.value)
        # chosen background/range + the starting guess (no fit)
        x = be + sh
        order = np.argsort(x)
        x, y = x[order], y[order]
        msk = (x >= m.fit_range[0]) & (x <= m.fit_range[1])
        x, y = x[msk], y[msk]
        bg = xf.make_background(m.background, x, y, m.n_avg, m.tougaard_C)
        if m.components:
            p = xf._build_params(m, x, y - bg)
            comps = xf._component_curves(m, p, x)
            env = sum(comps.values())
        else:
            comps, env = {}, None
        o = xf.FitOutcome(sample=w_sample.value, region=region, status="preview", shift=sh, x=x, y=y,
                          background=bg, envelope=env, components=comps, model=m)
        draw(o, f"{w_sample.value} — starting guess (not fitted)")

    def fit_one(*_):
        m, _ = current_model()
        be, y, sh = spectrum(w_sample.value)
        msg.value = "<i>fitting…</i>"
        o = xf.fit_spectrum(be, y, m, w_sample.value, region, shift=sh)
        if o.fitted:
            xf.monte_carlo(o, n=30)
        state["last"] = o
        msg.value = (f"<b>{o.status}</b> — R² {o.r2:.4f}, reduced χ² {o.redchi:.2f}, SNR {o.snr:.1f}"
                     if o.fitted else f"<b>{o.status}</b> (SNR {o.snr:.1f})")
        draw(o, None)

    def fit_all(*_):
        m, _ = current_model()
        msg.value = "<i>fitting all samples…</i>"
        res = [xf.fit_spectrum(*spectrum(s)[:2], m, s, region, shift=spectrum(s)[2]) for s in samples]
        msg.value = " · ".join(f"{o.sample}: R² {o.r2:.3f}" if o.fitted else f"{o.sample}: {o.status}"
                               for o in res)
        with out:
            out.clear_output(wait=True)
            xf.plot_region(res, region)
            plt.show()
            display(xf.results_table(res).pivot_table(index="component", columns="sample",
                                                      values="area %", sort=False).round(1))

    def use_fitted(*_):
        o = state["last"]
        if o is None or not o.fitted:
            msg.value = "Fit a sample first."
            return
        by_name = {r["component"]: r for _, r in o.table.iterrows()}
        for c, on, cen, win, fw in ctl:
            if c.name in by_name:
                cen.value = float(by_name[c.name]["BE (eV)"])
                fw.value = float(np.clip(by_name[c.name]["FWHM (eV)"], fw.min, fw.max))
        msg.value = "Sliders set to the fitted values (windows unchanged)."

    def save(*_):
        m, enabled = current_model()
        full = copy.deepcopy(m)
        full.components = []
        for c, on, cen, win, fw in ctl:     # include switched-off ones so they get commented out
            cc = copy.deepcopy(c)
            cc.center, cc.window, cc.fwhm = float(cen.value), tuple(win.value), float(fw.value)
            full.components.append(cc)
        p = save_region_to_yaml(yaml_path, full, enabled)
        msg.value = f"Saved {region} to <code>{p.name}</code> (backup: <code>{p.name}.bak</code>)."

    for w in [w_sample, w_bg, w_range, w_share] + [x for _, *ws in ctl for x in ws]:
        w.observe(preview, names="value")
    b_fit.on_click(fit_one)
    b_all.on_click(fit_all)
    b_use.on_click(use_fitted)
    b_save.on_click(save)

    ui = W.VBox([
        W.HTML(f"<h3 style='margin:4px 0'>{region} tuner</h3>"),
        W.HBox([w_sample, w_bg, w_share]), w_range,
        W.HTML("<b>Components</b> — untick to remove; <i>window</i> = where the fit may move it"),
        *rows,
        W.HBox([b_fit, b_all, b_use, b_save]), msg, out,
    ])
    display(ui)
    preview()
    return ui
