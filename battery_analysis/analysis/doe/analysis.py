"""
Analyse a filled DOE run sheet with statsmodels.

The model is a linear regression on the coded factors (−1/+1):
    y = b0 + bA·A + bB·B + ... + bAB·A·B + ...
An *effect* is how much the response changes going from a factor's low to
high level, which is 2 × its coefficient. A small p-value (< alpha) means the
effect is bigger than the cell-to-cell noise would explain.

No replicates (every run built once) leaves no degrees of freedom to estimate
noise, so p-values are impossible. Then Lenth's method is used instead: it
treats the many small effects as noise and flags the ones that stand out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


def read_run_sheet(path) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name="runs")


def fill_from_gcd(sheet: pd.DataFrame, cycles: pd.DataFrame, cfg) -> pd.DataFrame:
    """
    Fill response columns from GCD results (project.gcd_tables()["cycles"]).
    A response with `from_gcd: {column: "CE (%)", cycles: all}` in doe.yaml
    takes that column for the run's `cell` — the mean over all cycles
    (cycles: all) or cycle 1 only (cycles: first). Values you typed by hand
    are kept; only empty cells are filled.
    """
    out = sheet.copy()
    for r in cfg["responses"]:
        src = r.get("from_gcd")
        if not src:
            continue
        col, which = src["column"], src.get("cycles", "all")
        if col not in cycles.columns:
            raise KeyError(f"GCD tables have no column {col!r}. Available: {list(cycles.columns)}")
        per_cell = cycles.sort_values("cycle").groupby("cell")[col]
        values = per_cell.first() if which == "first" else per_cell.mean()
        if r["name"] not in out.columns:
            out[r["name"]] = np.nan
        fill = out["cell"].astype(str).map(values)
        out[r["name"]] = out[r["name"]].fillna(fill)
    missing = out[out["cell"].astype(str).str.strip().isin(["", "nan"])]
    if len(missing):
        print(f"{len(missing)} run(s) have no `cell` file name yet: {', '.join(missing['cell_id'])}")
    return out


@dataclass
class DOEResult:
    response: str
    label: str
    n_runs: int
    model: object = None                 # statsmodels results (None with Lenth)
    effects: pd.DataFrame = field(default_factory=pd.DataFrame)
    anova: pd.DataFrame = field(default_factory=pd.DataFrame)
    method: str = "OLS"
    r2: float = np.nan
    r2_adj: float = np.nan
    r2_pred: float = np.nan
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    terms_named: dict = field(default_factory=dict)


def _term_name(term: str, names: dict) -> str:
    if term.startswith("I("):                       # I(A ** 2) -> A²
        code = term[2]
        return f"{names.get(code, code)}²"
    return " × ".join(names.get(c, c) for c in term.split(":"))


def _lenth(effects: np.ndarray, alpha: float):
    """Lenth's pseudo standard error and margin of error for unreplicated designs."""
    from scipy import stats
    a = np.abs(effects)
    s0 = 1.5 * np.median(a)
    pse = 1.5 * np.median(a[a < 2.5 * s0]) if np.any(a < 2.5 * s0) else s0
    d = len(effects) / 3
    me = stats.t.ppf(1 - alpha / 2, d) * pse
    p = 2 * (1 - stats.t.cdf(a / pse, d)) if pse > 0 else np.full(len(a), np.nan)
    return pse, me, p


def fit_response(sheet: pd.DataFrame, cfg, response: str, order: int | None = None,
                 alpha: float = 0.05, drop_terms=None) -> DOEResult:
    """
    Fit one response. order=2 (default) = main effects + two-factor
    interactions; 1 = main effects only (forced for plackett_burman); 3 adds
    three-factor interactions. A ccd also gets squared terms (curvature).
    drop_terms: e.g. ["A:C"] to leave non-significant terms out and refit.
    """
    import statsmodels.formula.api as smf
    import statsmodels.api as sm

    factors = cfg["factors"]
    codes = [f["code"] for f in factors]
    names = {f["code"]: f["name"] for f in factors}
    rcfg = next((r for r in cfg["responses"] if r["name"] == response), {"name": response})
    label = rcfg.get("label", response)

    d = sheet[codes + [response]].rename(columns={response: "y"}).apply(pd.to_numeric, errors="coerce")
    d = d.dropna()
    if len(d) < len(codes) + 2:
        raise ValueError(f"{response}: only {len(d)} runs have a value — not enough to fit.")

    if order is None:
        order = 1 if cfg["kind"] == "plackett_burman" else 2
    rhs = f"({' + '.join(codes)})**{order}" if order > 1 else " + ".join(codes)
    if cfg["kind"] == "ccd":
        rhs += " + " + " + ".join(f"I({c} ** 2)" for c in codes)
    model = smf.ols(f"y ~ {rhs}", data=d)
    if drop_terms:
        keep = [t for t in model.exog_names if t not in set(drop_terms)]
        model = sm.OLS(model.endog, pd.DataFrame(model.exog, columns=model.exog_names)[keep])
    res = model.fit()

    terms = [t for t in res.params.index if t != "Intercept"]
    out = DOEResult(response=response, label=label, n_runs=len(d), data=d,
                    terms_named={t: _term_name(t, names) for t in terms})

    is_quadratic = {t: t.startswith("I(") for t in terms}
    coef = res.params[terms]
    eff = pd.DataFrame({
        "term": terms,
        "name": [out.terms_named[t] for t in terms],
        "coefficient": coef.values,
        # a squared term has no low→high "effect"; report its coefficient only
        "effect": [np.nan if is_quadratic[t] else 2 * c for t, c in coef.items()],
    })

    if res.df_resid >= 1:
        eff["std_error"] = res.bse[terms].values
        eff["t"] = res.tvalues[terms].values
        eff["p"] = res.pvalues[terms].values
        out.method = "OLS"
        out.model = res
        out.r2, out.r2_adj = res.rsquared, res.rsquared_adj
        h = res.get_influence().hat_matrix_diag
        press = np.sum((res.resid / np.clip(1 - h, 1e-12, None)) ** 2)
        sst = np.sum((d["y"] - d["y"].mean()) ** 2)
        out.r2_pred = 1 - press / sst if sst > 0 else np.nan
        try:
            out.anova = sm.stats.anova_lm(res, typ=2) if hasattr(res.model, "formula") else pd.DataFrame()
            if len(out.anova):
                out.anova.index = [out.terms_named.get(i, i) for i in out.anova.index]
        except Exception:                        # aliased (fractional) designs can make anova_lm fail
            out.anova = pd.DataFrame()
    else:
        effects = eff["effect"].fillna(2 * eff["coefficient"]).values
        pse, me, p = _lenth(effects, alpha)
        eff["t"] = effects / pse if pse > 0 else np.nan
        eff["p"] = p
        eff["std_error"] = pse / 2
        out.method = f"Lenth (no replicates; PSE = {pse:.3g}, margin = ±{me:.3g})"
        out.model = res

    eff["significant"] = eff["p"] < alpha
    out.effects = eff.reindex(eff["t"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return out


def findings(result: DOEResult, cfg, alpha: float = 0.05) -> str:
    """Plain-English summary of one response's fit."""
    f_by_code = {f["code"]: f for f in cfg["factors"]}
    rcfg = next((r for r in cfg["responses"] if r["name"] == result.response), {})
    unit = rcfg.get("unit", "")
    goal = rcfg.get("goal")
    lines = [f"### {result.label}  ({result.n_runs} runs, {result.method})"]
    if result.method == "OLS":
        lines.append(f"R² = {result.r2:.2f}, adjusted R² = {result.r2_adj:.2f}, predicted R² = {result.r2_pred:.2f}"
                     + ("  ← predicted R² is low: the model may not predict new cells well."
                        if result.r2_pred < 0.5 else ""))
    sig = result.effects[result.effects["significant"]]
    if sig.empty:
        lines.append(f"No effect is significant at p < {alpha}. Either nothing matters in this range, "
                     "or the noise between replicate cells is too large to see it (add replicates).")
    for _, e in sig.iterrows():
        term = e["term"]
        if term.startswith("I("):
            lines.append(f"- **{e['name']}** (curvature): the response bends — the best setting may lie "
                         f"inside the range (p = {e['p']:.3g}).")
        elif ":" in term:
            a, b = term.split(":")[:2]
            lines.append(f"- **{e['name']}** interaction (p = {e['p']:.3g}): the effect of "
                         f"{f_by_code[a]['name']} depends on {f_by_code[b]['name']}. "
                         f"Read the interaction plot before trusting either main effect alone.")
        else:
            f = f_by_code[term]
            lo, hi = f["levels"]
            direction = "raises" if e["effect"] > 0 else "lowers"
            better = ""
            if goal in ("maximize", "minimize"):
                good = (e["effect"] > 0) == (goal == "maximize")
                better = f" → **{hi if good else lo}** is better"
            lines.append(f"- **{f['name']}**: {lo} → {hi} {direction} {result.label} by "
                         f"{abs(e['effect']):.3g} {unit} (p = {e['p']:.3g}){better}.")
    weak = result.effects[~result.effects["significant"]]["name"].tolist()
    if weak:
        lines.append(f"Not significant: {', '.join(weak)}.")
    return "\n".join(lines)


def export(results: list, out_dir, name: str) -> Path:
    """Effects (and ANOVA where available) for every response in one workbook."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"DOE_{name}_effects.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        for r in results:
            sheet = r.response[:28]
            r.effects.to_excel(xl, sheet_name=sheet, index=False)
            if len(r.anova):
                r.anova.to_excel(xl, sheet_name=f"{sheet[:24]}_anova")
    return path
