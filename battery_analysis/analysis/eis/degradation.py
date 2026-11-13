"""
EIS degradation analysis — interpretation thresholds and mechanism
labels copied verbatim from the original EISAnalyzer/ComparativeEISAnalyzer.
Only the data source changed: reads the database's wide fit-result table
instead of EIS_Summary2.xlsx.

Each analyzer also exposes narrative(), a plain-text summary — this is
the hook a future cross-technique "story" report calls to pull EIS's
findings alongside DRT/cycling/dQ/dV/XPS without re-deriving the logic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import linregress


class EISAnalyzer:
    """Analyse EIS evolution to identify degradation mechanisms."""

    def __init__(self, df: pd.DataFrame, sample_name: str):
        self.df = df.copy()
        self.sample_name = sample_name
        self.cycles = df["CycleNum"].values
        self.rs = df["Rs"].values if "Rs" in df.columns else None
        self.rsei = df["Rsei"].values if "Rsei" in df.columns else None
        self.rct = df["Rct"].values if "Rct" in df.columns else None
        # Warburg coefficient — only present when this batch's circuit_key
        # includes a diffusion element (fitting.py's "SEI_CT_W"; "CT" and
        # "SEI_CT" don't fit one at all, so this stays None for those).
        self.w = df["W"].values if "W" in df.columns else None

    def analyze_ohmic_resistance(self):
        if self.rs is None or len(self.rs) < 2:
            return None
        initial, final = self.rs[0], self.rs[-1]
        change_pct = 100 * (final - initial) / initial
        std_dev = np.std(self.rs)

        if abs(change_pct) < 10 and std_dev < initial * 0.1:
            status, interp = "Stable", "Good electrolyte and contact stability"
        elif change_pct > 10:
            status, interp = "Increasing", "Possible electrolyte drying or contact degradation"
        elif change_pct < -10:
            status, interp = "Decreasing", "Unusual — check data quality"
        else:
            status, interp = "Minor fluctuation", "Normal variation"

        return dict(initial=initial, final=final, change_pct=change_pct,
                    std_dev=std_dev, status=status, interpretation=interp)

    def analyze_sei_resistance(self):
        if self.rsei is None or len(self.rsei) < 2:
            return None
        initial, final = self.rsei[0], self.rsei[-1]
        change_pct = 100 * (final - initial) / initial
        diff = np.diff(self.rsei)
        fluc_score = np.sum(np.diff(np.sign(diff)) != 0) / len(diff) if len(diff) else 0
        slope, _, r_value, _, _ = linregress(self.cycles, self.rsei)

        if change_pct > 50:
            status, interp, mech = "Severe thickening", "Continuous SEI reformation — Si volume expansion exposes fresh surface", "SEI Growth (Si swelling)"
        elif change_pct > 20:
            status, interp, mech = "Moderate thickening", "Gradual SEI growth", "SEI Growth"
        elif change_pct < -20:
            status, interp, mech = "Decreasing", "SEI cracking/instability", "SEI Cracking"
        elif fluc_score > 0.3:
            status, interp, mech = "Fluctuating", "Repeated SEI cracking and reformation cycle", "SEI Crack-Reform Cycle"
        else:
            status, interp, mech = "Relatively stable", "Good SEI passivation achieved", "Stable SEI"

        return dict(initial=initial, final=final, change_pct=change_pct,
                    slope=slope, fluctuation_score=fluc_score,
                    status=status, interpretation=interp, mechanism=mech, r_squared=r_value ** 2)

    def analyze_charge_transfer_resistance(self):
        if self.rct is None or len(self.rct) < 2:
            return None
        initial, final = self.rct[0], self.rct[-1]
        change_pct = 100 * (final - initial) / initial
        slope, _, r_value, _, _ = linregress(self.cycles, self.rct)

        if change_pct > 100:
            status, interp, mech = "Severe increase", "Major loss of active surface — particle pulverization/disconnection", "Active Material Loss (LAM)"
        elif change_pct > 50:
            status, interp, mech = "Moderate increase", "Significant kinetic degradation", "Partial LAM / Contact Loss"
        elif change_pct > 20:
            status, interp, mech = "Gradual increase", "Slow electrode degradation", "Minor Contact Loss"
        elif abs(change_pct) < 20:
            status, interp, mech = "Stable", "Good electrode integrity maintained", "Stable Electrode"
        else:
            status, interp, mech = "Decreasing", "Unusual — possible SEI cracking exposing fresh surface", "SEI Instability"

        return dict(initial=initial, final=final, change_pct=change_pct,
                    slope=slope, status=status, interpretation=interp, mechanism=mech, r_squared=r_value ** 2)

    def analyze_diffusion_resistance(self):
        """
        Warburg coefficient (W) trend — only meaningful for a batch fit
        with a diffusion element (circuit_key "SEI_CT_W"; None for "CT"/
        "SEI_CT", same guard pattern as the other analyze_* methods). A
        rising W means Li+ transport into/through the electrode is
        getting harder — thickening SEI/CEI blocking pores, particle
        cracking lengthening diffusion paths, or electrolyte depletion —
        distinct from Rct (charge-transfer AT the surface) and Rsei
        (resistance THROUGH the surface film): this is resistance to
        getting lithium ions to/from the bulk of the particle at all.
        """
        if self.w is None or len(self.w) < 2:
            return None
        initial, final = self.w[0], self.w[-1]
        change_pct = 100 * (final - initial) / initial if initial else 0.0
        slope, _, r_value, _, _ = linregress(self.cycles, self.w)

        if change_pct > 50:
            status, interp, mech = "Severe increase", "Diffusion strongly hindered — particle cracking, pore clogging, or electrolyte depletion", "Diffusion-Limited (Mass Transport)"
        elif change_pct > 20:
            status, interp, mech = "Moderate increase", "Growing diffusion resistance — electrode thickening or partial pore blocking", "Emerging Diffusion Limitation"
        elif abs(change_pct) < 20:
            status, interp, mech = "Stable", "Lithium transport into the bulk electrode still unobstructed", "Stable Diffusion"
        else:
            status, interp, mech = "Decreasing", "Unusual — possible electrode restructuring easing transport, or noisy low-frequency fit", "Improving/Noisy"

        return dict(initial=initial, final=final, change_pct=change_pct,
                    slope=slope, status=status, interpretation=interp, mechanism=mech, r_squared=r_value ** 2)

    # Below this |% change|, an element's own analyze_*() already calls it
    # "Stable" — dominant_degradation_mode() shouldn't confidently crown a
    # "dominant" mechanism out of what's actually just noise on every
    # element at once (e.g. -1%/+1%/+2% across the board isn't a real
    # signal, however those three numbers happen to rank against each
    # other).
    NEGLIGIBLE_CHANGE_PCT = 10.0

    def dominant_degradation_mode(self):
        """
        Which resistance element grew the most, of every element this
        batch's circuit actually fit — Ohmic (Rs, electrolyte/contacts),
        SEI (Rsei, surface film), Charge Transfer (Rct, reaction
        kinetics at the surface), and Diffusion (W, transport into the
        bulk, only present for a "SEI_CT_W" batch). Whichever element's
        |% change| is clearly largest (more than 2x the runner-up) is
        called "Dominated"; anything closer than that is "Mixed" across
        however many elements are close, so two comparably-degrading
        mechanisms don't get one arbitrarily picked over the other. If
        even the largest change is below NEGLIGIBLE_CHANGE_PCT, this
        reports "Stable" instead of forcing a winner out of noise.
        """
        candidates = {
            "Ohmic": (self.analyze_ohmic_resistance(),
                      "Improve electrolyte wetting/contact pressure, check for connector corrosion"),
            "SEI": (self.analyze_sei_resistance(),
                    "Better coating (VGCF, carbon), FEC/VC additives, pre-SEI formation"),
            "Charge Transfer": (self.analyze_charge_transfer_resistance(),
                                 "Better binder (CMC, PAA), optimise particle size, improve compression"),
            "Diffusion": (self.analyze_diffusion_resistance(),
                          "Reduce electrode thickness/tortuosity, improve electrolyte wetting, lower C-rate"),
        }
        contributions = {
            name: abs(result["change_pct"]) for name, (result, _rec) in candidates.items() if result is not None
        }
        if not contributions:
            return None

        ranked = sorted(contributions.items(), key=lambda kv: -kv[1])
        top_name, top_change = ranked[0]
        runner_up_change = ranked[1][1] if len(ranked) > 1 else 0.0

        if top_change < self.NEGLIGIBLE_CHANGE_PCT:
            dominant = "Stable (no resistance element changed meaningfully)"
            recommendation = "No action needed based on EIS alone — keep monitoring."
        elif len(ranked) == 1 or top_change > 2 * max(runner_up_change, 1e-9):
            dominant = f"{top_name} Dominated"
            recommendation = candidates[top_name][1]
        else:
            # Every element within 2x of the top one shares the "Mixed" label.
            close = [name for name, change in ranked if change >= top_change / 2]
            dominant = "Mixed (" + " + ".join(close) + ")"
            recommendation = "Address each: " + "; ".join(candidates[name][1] for name in close)

        return dict(dominant=dominant, contributions=contributions, recommendation=recommendation)

    def analyze(self, verbose: bool = True) -> dict:
        results = dict(
            sample=self.sample_name,
            ohmic=self.analyze_ohmic_resistance(),
            sei=self.analyze_sei_resistance(),
            charge_transfer=self.analyze_charge_transfer_resistance(),
            diffusion=self.analyze_diffusion_resistance(),
            degradation_mode=self.dominant_degradation_mode(),
        )
        if verbose:
            print(self.narrative(results))
        return results

    def narrative(self, results: dict | None = None) -> str:
        """Plain-text summary — reusable by a cross-technique story report."""
        r = results or self.analyze(verbose=False)
        lines = [f"EIS — {self.sample_name}"]
        if r["ohmic"]:
            o = r["ohmic"]
            lines.append(
                f"  Ohmic resistance (Rs): {o['change_pct']:+.1f}% ({o['status']}) — {o['interpretation']}"
            )
        if r["sei"]:
            s = r["sei"]
            lines.append(
                f"  SEI resistance: {s['change_pct']:+.1f}% over the tested cycles "
                f"({s['status']}, {s['mechanism']}) — {s['interpretation']}"
            )
        if r["charge_transfer"]:
            c = r["charge_transfer"]
            lines.append(
                f"  Charge-transfer resistance: {c['change_pct']:+.1f}% "
                f"({c['status']}, {c['mechanism']}) — {c['interpretation']}"
            )
        if r["diffusion"]:
            d = r["diffusion"]
            lines.append(
                f"  Diffusion resistance (Warburg): {d['change_pct']:+.1f}% "
                f"({d['status']}, {d['mechanism']}) — {d['interpretation']}"
            )
        if r["degradation_mode"]:
            m = r["degradation_mode"]
            lines.append(f"  Dominant mode: {m['dominant']} → {m['recommendation']}")
        return "\n".join(lines)


class ComparativeEISAnalyzer:
    """Compare EIS evolution between two samples."""

    def __init__(self, samples_dict: dict):
        self.analyzers = {name: EISAnalyzer(df, name) for name, df in samples_dict.items()}
        self.results = {name: a.analyze(verbose=False) for name, a in self.analyzers.items()}

    def compare(self, verbose: bool = True) -> dict:
        samples = list(self.results.keys())
        text = []
        if len(samples) != 2:
            if verbose:
                print("Comparative analysis requires exactly 2 samples.")
            return self.results

        s1, s2 = samples
        r1, r2 = self.results[s1], self.results[s2]

        if r1["sei"] and r2["sei"]:
            imp = r1["sei"]["change_pct"] - r2["sei"]["change_pct"]
            verdict = (
                f"{s2} significantly reduces SEI growth" if imp > 20 else
                f"{s2} moderately reduces SEI growth" if imp > 0 else
                "No improvement in SEI stability"
            )
            text.append(f"SEI resistance — {s1}: {r1['sei']['change_pct']:+.1f}%, {s2}: {r2['sei']['change_pct']:+.1f}% → {verdict}")

        if r1["charge_transfer"] and r2["charge_transfer"]:
            imp = r1["charge_transfer"]["change_pct"] - r2["charge_transfer"]["change_pct"]
            verdict = (
                f"{s2} significantly improves electrode kinetics" if imp > 30 else
                f"{s2} moderately improves charge transfer" if imp > 0 else
                "No improvement in charge transfer resistance"
            )
            text.append(f"Charge transfer — {s1}: {r1['charge_transfer']['change_pct']:+.1f}%, {s2}: {r2['charge_transfer']['change_pct']:+.1f}% → {verdict}")

        if r1["ohmic"] and r2["ohmic"]:
            imp = r1["ohmic"]["change_pct"] - r2["ohmic"]["change_pct"]
            verdict = (
                f"{s2} significantly improves contact/electrolyte stability" if imp > 10 else
                f"{s2} moderately improves ohmic stability" if imp > 0 else
                "No improvement in ohmic resistance"
            )
            text.append(f"Ohmic resistance — {s1}: {r1['ohmic']['change_pct']:+.1f}%, {s2}: {r2['ohmic']['change_pct']:+.1f}% → {verdict}")

        if r1["diffusion"] and r2["diffusion"]:
            imp = r1["diffusion"]["change_pct"] - r2["diffusion"]["change_pct"]
            verdict = (
                f"{s2} significantly improves lithium transport" if imp > 30 else
                f"{s2} moderately improves diffusion behaviour" if imp > 0 else
                "No improvement in diffusion resistance"
            )
            text.append(f"Diffusion resistance — {s1}: {r1['diffusion']['change_pct']:+.1f}%, {s2}: {r2['diffusion']['change_pct']:+.1f}% → {verdict}")

        if verbose:
            print("\n".join(text))
        return self.results


def run_degradation_analysis(
    config, repo, project_id: int, verbose: bool = True, batch_name: str | None = None,
    eis_cfg=None,
) -> dict:
    """
    Load fit results from the database, run EISAnalyzer per sample and
    ComparativeEISAnalyzer if exactly 2 samples are present. Returns a
    dict keyed by sample name (plus "_comparison" if applicable) so the
    caller (or a future story report) can consume it programmatically.

    Scoped to batch_name by default — mixing two batches' cycles into one
    trend would silently corrupt the degradation analysis.

    "initial vs final" (and everything derived from it — change_pct,
    status, mechanism) depends entirely on getting sheets in the right
    order first. That order comes from eis_cfg's own sheet_order/
    sheet_names — this batch's declared sequence — rather than parsing
    a number back out of the label text: label text can be anything
    (cycle counts, hours + a "Precycle" milestone, voltage steps,
    current densities, ...) and two different labels can easily parse
    to the same digit (e.g. "3.0V" and "3.15V" both read as "3"),
    silently scrambling the order. If eis_cfg isn't passed (older
    callers), this falls back to the previous digit/keyword parsing —
    exact same behaviour as before, just no longer the only option.
    """
    fit_df = repo.load_eis_fit_results(project_id, batch_name=batch_name)
    if fit_df.empty:
        print("⚠️  No fit results in the database yet — run project.fit_eis() first.")
        return {}

    fit_df = fit_df.copy()
    if eis_cfg is not None:
        stage_labels = [eis_cfg.sheet_names.get(k, k) for k in eis_cfg.sheet_order]
        stage_index = {label: i + 1 for i, label in enumerate(stage_labels)}
        fit_df["CycleNum"] = fit_df["Sheet"].map(stage_index)
    else:
        from .parser import extract_cycle_from_sheet
        fit_df["CycleNum"] = fit_df["Sheet"].apply(extract_cycle_from_sheet)
    fit_df = fit_df.dropna(subset=["CycleNum"]).sort_values("CycleNum")

    out = {}
    per_sample = {}
    for sample in fit_df["Sample"].unique():
        df_s = fit_df[fit_df["Sample"] == sample].reset_index(drop=True)
        analyzer = EISAnalyzer(df_s, sample)
        out[sample] = analyzer.analyze(verbose=verbose)
        per_sample[sample] = df_s

    if len(per_sample) == 2:
        out["_comparison"] = ComparativeEISAnalyzer(per_sample).compare(verbose=verbose)

    return out
