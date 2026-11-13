"""
Checks for the lab-book calculators, worked by hand. Run with

    python -m pytest battery_analysis/labbook/test_labbook.py -v
or, without pytest:
    python -m battery_analysis.labbook.test_labbook
"""
import math
import tempfile
from pathlib import Path

import yaml

from battery_analysis.labbook.book import LabBook
from battery_analysis.labbook.materials import MaterialLibrary, Material, Stock
from battery_analysis.labbook.recipes import build_recipes, RecipeError


def approx(a, b, tol=1e-9):
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def _lib():
    lib = MaterialLibrary()
    lib.add_material(Material(name="NaPF6", molar_mass_g_mol=167.95))
    lib.add_material(Material(name="diglyme", molar_mass_g_mol=134.17, aliases=["G2", "DMG"]))
    lib.add_material(Material(name="AdditiveA", molar_mass_g_mol=141.94))
    lib.add_stock(Stock.from_spec({"name": "8 wt% PVDF in NMP", "composition": {"PVDF": 8}, "balance": "NMP"}))
    lib.add_stock(Stock.from_spec({"name": "15 wt% PVDF-HFP in NMP", "composition": {"PVDF-HFP": 15}, "balance": "NMP"}))
    return lib


def _masses(r):
    return {row["component"]: row["mass_g"] for row in r.result()["rows"]}


def _comp(r):
    return {c["species"]: c["target_wt_pct"] for c in r.result()["composition"]}


def test_molar_solution_salt_mass():
    # 1 mol/L x 0.010 L x 167.95 g/mol = 1.6795 g
    (r,) = build_recipes([{"id": "E-0", "type": "molar_solution", "salt": "NaPF6", "solvent": "G2",
                           "molarity_M": 1, "volume_mL": 10}], _lib())
    assert approx(r.salt_g, 1.6795)
    assert r.solvent_g is None                      # no density -> "top up to the mark"


def test_molar_solution_with_density_gives_solvent_mass():
    (r,) = build_recipes([{"id": "E-0", "type": "molar_solution", "salt": "NaPF6", "solvent": "G2",
                           "molarity_M": 1, "volume_mL": 10, "solution_density_g_mL": 1.09}], _lib())
    assert approx(r.total_g, 10.9)
    assert approx(r.solvent_g, 10.9 - 1.6795)


def test_additive_in_electrolyte_one_percent():
    # 1 wt% of 5 g total = 0.05 g AdditiveA, 4.95 g electrolyte
    recipes = build_recipes([
        {"id": "E-0", "type": "molar_solution", "salt": "NaPF6", "solvent": "G2", "molarity_M": 1, "volume_mL": 10},
        {"id": "L-1", "total_g": 5, "components": [{"name": "AdditiveA", "wt_pct": 1}, {"name": "E-0", "balance": True}]},
    ], _lib())
    m = _masses(recipes[1])
    assert approx(m["AdditiveA"], 0.05) and approx(m["E-0"], 4.95)


def test_vary_makes_a_series():
    rs = build_recipes([{"id": "L", "total_g": 5, "vary": {"component": "AdditiveA", "wt_pct": [2.5, 5, 7.5]},
                         "components": [{"name": "AdditiveA", "wt_pct": 0}, {"name": "E", "balance": True}]}], _lib())
    assert [r.id for r in rs] == ["L-2.5", "L-5", "L-7.5"]
    assert approx(_masses(rs[2])["AdditiveA"], 0.375)


def test_additive_in_binder_stock():
    # 1 wt% AdditiveA, the other 99% is the 8 wt% PVDF stock -> PVDF 7.92 wt%, NMP 91.08 wt%
    (r,) = build_recipes([{"id": "C-1", "total_g": 5, "components": [
        {"name": "AdditiveA", "wt_pct": 1}, {"name": "8 wt% PVDF in NMP", "balance": True}]}], _lib())
    assert approx(_masses(r)["8 wt% PVDF in NMP"], 4.95)
    c = _comp(r)
    assert approx(c["PVDF"], 7.92, 1e-6) and approx(c["NMP"], 91.08, 1e-6)


def test_two_solutes_from_powder():
    # 1 % AdditiveA + 10 % PVDF-HFP in 10 g -> 0.1 g, 1.0 g, 8.9 g NMP
    (r,) = build_recipes([{"id": "P-1", "total_g": 10, "components": [
        {"name": "AdditiveA", "wt_pct": 1}, {"name": "PVDF-HFP", "wt_pct": 10}, {"name": "NMP", "balance": True}]}], _lib())
    m = _masses(r)
    assert approx(m["AdditiveA"], 0.1) and approx(m["PVDF-HFP"], 1.0) and approx(m["NMP"], 8.9)


def test_solute_from_stock_takes_its_solvent_off_the_balance():
    # 10 wt% PVDF-HFP of 10 g = 1 g, from a 15 % stock -> 6.6667 g stock (5.6667 g NMP in it)
    # NMP to add = 10 - 0.1 - 6.6667 = 3.2333 g
    (r,) = build_recipes([{"id": "P-2", "total_g": 10, "components": [
        {"name": "AdditiveA", "wt_pct": 1}, {"name": "PVDF-HFP", "wt_pct": 10, "from": "15 wt% PVDF-HFP in NMP"},
        {"name": "NMP", "balance": True}]}], _lib())
    m = _masses(r)
    assert approx(m["PVDF-HFP"], 1 / 0.15, 1e-6) and approx(m["NMP"], 10 - 0.1 - 1 / 0.15, 1e-6)
    c = _comp(r)
    assert approx(c["PVDF-HFP"], 10, 1e-6) and approx(c["AdditiveA"], 1, 1e-6)


def test_basis_scales_off_what_you_have():
    # "I have 4.95 g electrolyte" -> total 5 g, 0.05 g AdditiveA
    (r,) = build_recipes([{"id": "L-1", "basis": {"E": 4.95}, "components": [
        {"name": "AdditiveA", "wt_pct": 1}, {"name": "E", "balance": True}]}], _lib())
    assert approx(r.total_mass_g, 5.0) and approx(_masses(r)["AdditiveA"], 0.05)


def test_weighed_gives_actual_composition():
    (r,) = build_recipes([{"id": "L-1", "total_g": 5, "weighed": {"AdditiveA": 0.0510, "E": 4.9490},
                           "components": [{"name": "AdditiveA", "wt_pct": 1}, {"name": "E", "balance": True}]}], _lib())
    actual = {c["species"]: c["actual_wt_pct"] for c in r.result()["composition"]}
    assert approx(actual["AdditiveA"], 1.02, 1e-6)


def test_too_much_solute_is_an_error():
    try:
        build_recipes([{"id": "X", "total_g": 1, "components": [
            {"name": "PVDF", "wt_pct": 20, "from": "8 wt% PVDF in NMP"}, {"name": "NMP", "balance": True}]}],
            _lib())[0].masses_g()
    except RecipeError as e:
        assert "concentrated stock" in str(e)
    else:
        raise AssertionError("20 % PVDF from an 8 % stock should be impossible")


def test_book_new_lookup_and_cross_entry_stock():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "LabBook"
        (root / "templates").mkdir(parents=True)
        (root / "templates" / "blank.yaml").write_text("id: {{id}}\ntitle: '{{title}}'\n")
        book = LabBook(root)
        p = book.new("discs", date="2026-10-24")
        assert p.name.startswith("2026-10-24a")
        assert book.next_id("2026-10-24") == "2026-10-24b"
        p.write_text(yaml.safe_dump({
            "id": "2026-10-24a", "title": "discs",
            "electrode_defaults": {"foil_weight_g": 0.0085, "foil_thickness_mm": 0.02, "diameter_mm": 14,
                                   "am_fraction": 0.8, "theoretical_capacity_mAhg": 170},
            "electrodes": [{"name": "C1", "disc_weight_g": 0.0205, "disc_thickness_mm": 0.058}],
            "recipes": [{"id": "E-0", "total_g": 5, "components": [{"name": "salt", "wt_pct": 10},
                                                                  {"name": "solvent", "balance": True}]}],
        }))
        # (0.0205 - 0.0085) x 0.8 = 0.0096 g
        assert approx(book.lookup("2026-10-24a/C1"), 0.0096, 1e-6)
        # the same number reaches a cycle.yaml-style config through the loader
        from battery_analysis.config.loader import _resolve_labbook_refs
        raw = {"espec_channels": [{"channel": "CH1", "active_mass_g": "labbook:2026-10-24a/C1"}]}
        resolved = _resolve_labbook_refs(raw, root)
        assert approx(resolved["espec_channels"][0]["active_mass_g"], 0.0096, 1e-6)
        (root / "entries" / "2026-10-25a_use.yaml").write_text(yaml.safe_dump({
            "recipes": [{"id": "L-1", "total_g": 10, "components": [
                {"name": "AdditiveA", "wt_pct": 1}, {"name": "2026-10-24a/E-0", "balance": True}]}]}))
        c = _comp(book.get("2026-10-25a").recipes[0])
        assert approx(c["salt"], 9.9, 1e-6)


if __name__ == "__main__":
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_")]
    for n, f in tests:
        f()
        print(f"ok  {n}")
    print(f"{len(tests)} passed")
