"""
Self-test: simulate cells with KNOWN effects, then check the analysis finds them.
Run:  python -m battery_analysis.analysis.doe.test_doe   (or pytest)
"""
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import yaml

from battery_analysis.analysis import doe

YAML = """
design_name: TEST
kind: full
replicates: 3
seed: 7
factors:
  - {name: Foil, levels: [Al, Cu]}
  - {name: P2O5, levels: [none, P2O5]}
  - {name: J, type: numeric, levels: [0.5, 1.0]}
responses:
  - {name: CE_mean, label: Mean CE, unit: "%", goal: maximize, from_gcd: {column: "CE (%)", cycles: all}}
"""

# True model in coded units: CE = 90 + 3·P2O5 − 2·J + 1.5·P2O5·J  (Foil does nothing)
TRUE = {"B": 6.0, "C": -4.0, "B:C": 3.0, "A": 0.0}       # effects = 2 × coefficient


def _simulate(design, noise=0.4, seed=0):
    rng = np.random.default_rng(seed)
    return 90 + 3 * design["B"] - 2 * design["C"] + 1.5 * design["B"] * design["C"] + rng.normal(0, noise, len(design))


def _cfg(tmp):
    p = Path(tmp) / "doe.yaml"
    p.write_text(YAML)
    return doe.load_doe(p)


def test_design_shape_and_balance():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        d = doe.build_design(cfg)
        assert len(d) == 24
        assert set(d["J"]) == {0.5, 1.0}
        assert (d.groupby(["A", "B", "C"]).size() == 3).all()        # 3 replicates of every combination
        assert list(d["run"]) == list(range(1, 25))
        assert not d["std_order"].is_monotonic_increasing             # randomized


def test_recovers_known_effects_and_writes_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        d = doe.build_design(cfg)
        path = doe.write_run_sheet(cfg, d, Path(tmp) / "data")
        # second call must not overwrite
        assert doe.write_run_sheet(cfg, d.iloc[:2], Path(tmp) / "data") == path
        assert len(doe.read_run_sheet(path)) == 24

        # fill through the GCD route: fake per-cycle table, 5 cycles per cell
        d["cell"] = [f"cell{i}" for i in d["run"]]
        ce = _simulate(d)
        cycles = pd.DataFrame([{"cell": c, "cycle": k, "CE (%)": v}
                               for c, v in zip(d["cell"], ce) for k in range(1, 6)])
        filled = doe.fill_from_gcd(d, cycles, cfg)
        assert np.allclose(filled["CE_mean"], ce)

        res = doe.fit_response(filled, cfg, "CE_mean")
        eff = res.effects.set_index("term")
        for term, true in TRUE.items():
            assert abs(eff.loc[term, "effect"] - true) < 0.6, (term, eff.loc[term, "effect"])
        assert eff.loc["B", "significant"] and eff.loc["C", "significant"] and eff.loc["B:C", "significant"]
        assert not eff.loc["A", "significant"]
        text = doe.findings(res, cfg)
        assert "**P2O5**" in text and "interaction" in text

        figs = doe.plot_all(res, cfg, Path(tmp) / "figs")
        assert len(figs) == 4 and all(Path(f).exists() for f in figs)


def test_unreplicated_uses_lenth():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        cfg["replicates"] = 1
        d = doe.build_design(cfg)
        d["CE_mean"] = _simulate(d, noise=0.1)
        res = doe.fit_response(d, cfg, "CE_mean", order=3)      # saturated: 0 residual df
        assert res.method.startswith("Lenth")
        assert res.effects.set_index("term").loc["B", "significant"]


def test_ccd_has_curvature_terms():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "doe.yaml"
        p.write_text(yaml.safe_dump({
            "kind": "ccd", "center_points": 3, "seed": 1,
            "factors": [{"name": "J", "type": "numeric", "levels": [0.5, 2.0]},
                        {"name": "T", "type": "numeric", "levels": [25, 45]}],
            "responses": [{"name": "y"}]}))
        cfg = doe.load_doe(p)
        d = doe.build_design(cfg)
        assert len(d) == 4 + 4 + 3 and set(d["J"]) == {0.5, 1.25, 2.0}
        d["y"] = 10 - 3 * d["A"] ** 2 + d["B"] + np.random.default_rng(1).normal(0, 0.05, len(d))
        res = doe.fit_response(d, cfg, "y")
        assert res.effects.set_index("term").loc["I(A ** 2)", "significant"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
