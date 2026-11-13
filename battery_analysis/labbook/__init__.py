"""
Electronic lab notebook: dated entries (LabBook/entries/), recipe
calculators (recipes.py), and one-command PDF reports (report.py).
See LabBook/HOW TO - Lab book.md for the walkthrough.
"""
from .book import LabBook
from .recipes import MixtureRecipe, MolarSolutionRecipe, build_recipes
from .materials import MaterialLibrary


def mix(total_g=None, *, basis=None, book=None, **components):
    """
    Quick sum without writing an entry — e.g. in a notebook cell:

        mix(5, AdditiveA=1, E0="balance")
        mix(basis={"PVDF8": 4.95}, AdditiveA=1, PVDF8="balance")

    Keyword = component name, value = its wt% or "balance". For names
    with spaces or %-signs use an entry instead; this is for quick sums.
    """
    library = book.library if book is not None else MaterialLibrary()
    lines = [{"name": k, "balance": True} if v == "balance" else {"name": k, "wt_pct": v}
             for k, v in components.items()]
    spec = {"id": "quick", "name": "quick mix", "components": lines}
    if total_g is not None:
        spec["total_g"] = total_g
    if basis is not None:
        spec["basis"] = basis
    r = build_recipes([spec], library)[0]
    print(r.table())
    return r
