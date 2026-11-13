"""
Design of Experiments (DOE): plan a set of cells, then find which factors matter.

    design.py    doe.yaml -> randomized run sheet (Excel) to build the cells from
    analysis.py  filled run sheet -> effects, p-values, ANOVA, plain-English findings
    plot.py      Pareto of effects, main effects, interactions, residual checks

Designs come from pyDOE3; the statistics come from statsmodels.
"""
from .design import load_doe, build_design, write_run_sheet  # noqa: F401
from .analysis import read_run_sheet, fill_from_gcd, fit_response, findings  # noqa: F401
from .plot import plot_all  # noqa: F401
