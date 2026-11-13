"""
Capacity retention / CE degradation analysis — interpretation logic
copied verbatim from CycleAnalyzer in the original battery_analysis/cycle.py.
Reads from the database (cycle_point) instead of an in-memory DataFrame.

ComparativeAnalyzer, DegradationAnalyzer, AnomalyDetector, and
BatteryIntelligence from the original cycle.py are NOT yet ported — this
covers CycleAnalyzer's four core methods (capacity_fade_rate,
ce_decline_rate, detect_sudden_death, predict_eol), which is what the
story report needs.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import linregress


class CycleAnalyzer:
    """Analyze capacity retention and coulombic efficiency trends."""

    def __init__(self, cycles, capacity_discharge, ce_pct, sample_name):
        self.sample_name = sample_name
        self.cycles = np.asarray(cycles, dtype=float)
        self.capacity = np.asarray(capacity_discharge, dtype=float)
        self.ce = np.asarray(ce_pct, dtype=float)

    def capacity_fade_rate(self, window=10):
        if len(self.capacity) < 2:
            return None
        slope, intercept, r_value, p_value, std_err = linregress(self.cycles, self.capacity)
        early_cycles = self.cycles[:min(window, len(self.cycles))]
        early_cap = self.capacity[:min(window, len(self.capacity))]
        late_cycles = self.cycles[-min(window, len(self.cycles)):]
        late_cap = self.capacity[-min(window, len(self.capacity)):]
        early_slope = linregress(early_cycles, early_cap)[0] if len(early_cycles) > 1 else 0
        late_slope = linregress(late_cycles, late_cap)[0] if len(late_cycles) > 1 else 0
        return {
            "overall_rate": slope,
            "early_rate": early_slope,
            "late_rate": late_slope,
            "acceleration": late_slope < early_slope,
            "r_squared": r_value ** 2,
            "initial_capacity": self.capacity[0],
            "final_capacity": self.capacity[-1],
            "retention_pct": 100 * self.capacity[-1] / self.capacity[0],
        }

    def ce_decline_rate(self):
        if len(self.ce) < 2:
            return None
        slope, intercept, r_value, p_value, std_err = linregress(self.cycles, self.ce)
        mean_ce = np.mean(self.ce)
        std_ce = np.std(self.ce)
        anomalies = self.cycles[self.ce < (mean_ce - 2 * std_ce)]
        return {
            "average_ce": mean_ce,
            "decline_rate": slope,
            "std_dev": std_ce,
            "min_ce": np.min(self.ce),
            "max_ce": np.max(self.ce),
            "anomalies": anomalies.tolist(),
            "r_squared": r_value ** 2,
        }

    def detect_sudden_death(self, threshold=0.8):
        retention = self.capacity / self.capacity[0]
        failure_cycles = self.cycles[retention < threshold]
        if len(failure_cycles) == 0:
            return None
        failure_cycle = failure_cycles[0]
        failure_idx = np.where(self.cycles == failure_cycle)[0][0]
        if failure_idx > 5:
            before_slope = linregress(self.cycles[:failure_idx], self.capacity[:failure_idx])[0]
            after_idx = min(failure_idx + 5, len(self.cycles))
            after_slope = linregress(self.cycles[failure_idx:after_idx], self.capacity[failure_idx:after_idx])[0]
            is_sudden = abs(after_slope) > 2 * abs(before_slope)
            return {
                "failure_cycle": int(failure_cycle),
                "failure_capacity": self.capacity[failure_idx],
                "is_sudden": is_sudden,
                "slope_change": after_slope / before_slope if before_slope != 0 else np.inf,
            }
        return None

    def predict_eol(self, eol_threshold=0.8):
        fade = self.capacity_fade_rate()
        if fade is None or fade["overall_rate"] >= 0:
            return None
        initial_cap = self.capacity[0]
        current_cap = self.capacity[-1]
        current_cycle = self.cycles[-1]
        target_cap = initial_cap * eol_threshold
        if current_cap < target_cap:
            return None
        cycles_to_eol = (current_cap - target_cap) / abs(fade["overall_rate"])
        predicted_eol = current_cycle + cycles_to_eol
        return {
            "predicted_eol_cycle": int(predicted_eol),
            "cycles_remaining": int(cycles_to_eol),
            "current_retention": 100 * current_cap / initial_cap,
            "fade_rate": fade["overall_rate"],
        }

    def predict_eol_from_early_cycles(self, n_early_cycles: int = 10, eol_threshold: float = 0.8):
        """
        Project cycle life using ONLY the first n_early_cycles data
        points — a genuine "predict from a few cycles" forecast, as
        opposed to predict_eol() above, which uses the fade rate over
        however much data has actually been collected so far (better
        once you have it, but not a few-cycle forecast).

        This is a straight-line extrapolation of early capacity fade —
        it deliberately does NOT need enough long-run data to see any
        acceleration in the fade rate, because at this point there isn't
        any yet. That's also its main limitation: real capacity fade
        very often accelerates later in life (see capacity_fade_rate's
        own early_rate vs late_rate split, and detect_sudden_death for
        the same effect at a shorter timescale) — so a linear early-only
        projection is systematically an OPTIMISTIC estimate; the real
        knee point tends to arrive sooner than this says. Treat the
        result as an early lower-bound heads-up ("could fail around
        cycle X — recheck once you have more cycles"), not a committed
        number.
        """
        if len(self.capacity) < max(3, n_early_cycles // 2):
            return None
        n = min(n_early_cycles, len(self.capacity))
        early_cycles, early_cap = self.cycles[:n], self.capacity[:n]
        if len(set(early_cycles)) < 2:
            return None
        slope, intercept, r_value, _, _ = linregress(early_cycles, early_cap)
        if slope >= 0:
            return None  # not fading over this early window — nothing to project

        initial_cap = early_cap[0]
        target_cap = initial_cap * eol_threshold
        current_cap = early_cap[-1]
        if current_cap < target_cap:
            return None  # already past threshold within the early window itself

        predicted_eol_cycle = (target_cap - intercept) / slope
        return dict(
            n_early_cycles=n,
            basis=f"first {n} cycles only",
            fade_rate=slope,
            r_squared=r_value ** 2,
            predicted_eol_cycle=int(predicted_eol_cycle),
            cycles_remaining=int(predicted_eol_cycle - early_cycles[-1]),
            caveat="Linear extrapolation from early cycles alone — real fade often "
                   "accelerates later, so treat this as an optimistic lower bound.",
        )

    def analyze(self, verbose: bool = True, early_window: int = 10) -> dict:
        results = dict(
            sample=self.sample_name,
            total_cycles=len(self.cycles),
            capacity_fade=self.capacity_fade_rate(),
            ce_trend=self.ce_decline_rate(),
            sudden_death=self.detect_sudden_death(),
            eol_prediction=self.predict_eol(),
            early_eol_prediction=self.predict_eol_from_early_cycles(n_early_cycles=early_window),
        )
        if verbose:
            print(self.narrative(results))
        return results

    def narrative(self, results: dict | None = None) -> str:
        r = results or self.analyze(verbose=False)
        lines = [f"Cycle — {self.sample_name}"]
        fade = r["capacity_fade"]
        if fade:
            lines.append(
                f"  Discharge capacity retention: {fade['retention_pct']:.1f}% after "
                f"{r['total_cycles']} cycles (fade rate {fade['overall_rate']:.3f} mAh/g/cycle)"
            )
            if fade["acceleration"] and fade["early_rate"] != 0:
                ratio = abs(fade["late_rate"] / fade["early_rate"])
                lines.append(f"  Fade is accelerating: {ratio:.1f}x faster in later cycles")
        ce = r["ce_trend"]
        if ce:
            lines.append(f"  Coulombic efficiency: mean {ce['average_ce']:.2f}%, std {ce['std_dev']:.2f}%")
            if ce["decline_rate"] < -0.01:
                lines.append(f"  CE declining: {ce['decline_rate']:.4f}%/cycle")
        sd = r["sudden_death"]
        if sd and sd["is_sudden"]:
            lines.append(f"  Sudden capacity loss detected at cycle {sd['failure_cycle']}")
        eol = r["eol_prediction"]
        if eol:
            lines.append(f"  Predicted 80% EOL at cycle {eol['predicted_eol_cycle']} ({eol['cycles_remaining']} cycles remaining)")
        early_eol = r.get("early_eol_prediction")
        if early_eol:
            lines.append(
                f"  Early-cycle forecast ({early_eol['basis']}): 80% EOL around cycle "
                f"{early_eol['predicted_eol_cycle']} (R²={early_eol['r_squared']:.2f}) — {early_eol['caveat']}"
            )
        return "\n".join(lines)


def run_degradation_analysis(config, repo, project_id: int, verbose: bool = True, batch_name: str | None = None) -> dict:
    """
    Run CycleAnalyzer for every representative cycle-technique experiment
    in the database, scoped to batch_name. Returns a dict keyed by sample
    name, matching the shape of the EIS/DRT story-report inputs — with
    keys renamed to match project.story()'s expectations (retention/ce).
    """
    experiments_df = repo.list_cycle_experiments(project_id, batch_name=batch_name)
    out = {}
    for _, row in experiments_df.iterrows():
        if not row["is_representative"]:
            continue
        points = repo.load_cycle_points(row["experiment_id"])
        if points.empty:
            continue
        analyzer = CycleAnalyzer(
            points["cycle_number"], points["capacity_discharge"], points["ce_pct"], row["sample"]
        )
        raw = analyzer.analyze(verbose=verbose)

        result = {"sample": row["sample"]}
        if raw["capacity_fade"]:
            f = raw["capacity_fade"]
            status = "Accelerating fade" if f["acceleration"] else "Stable fade rate"
            result["retention"] = {
                "final_pct": f["retention_pct"],
                "n_cycles": raw["total_cycles"],
                "status": status,
                "interpretation": (
                    f"{f['overall_rate']:.3f} mAh/g lost per cycle"
                    + (f" — accelerating in later cycles" if f["acceleration"] else "")
                ),
            }
        if raw["ce_trend"]:
            c = raw["ce_trend"]
            status = "Declining" if c["decline_rate"] < -0.01 else "Stable"
            result["ce"] = {
                "mean": c["average_ce"],
                "status": status,
                "interpretation": f"std dev {c['std_dev']:.2f}%, range {c['min_ce']:.1f}-{c['max_ce']:.1f}%",
            }
        # Kept as the raw dict (not pre-formatted text) here — story()
        # formats its own sentence from it below, and export_features()
        # pulls the numeric fields (predicted_eol_cycle, r_squared, ...)
        # straight out of it; a pre-formatted string would hide those
        # numbers from both.
        if raw["eol_prediction"]:
            result["eol"] = raw["eol_prediction"]
        if raw["early_eol_prediction"]:
            result["early_eol"] = raw["early_eol_prediction"]
        out[row["sample"]] = result

    return out
