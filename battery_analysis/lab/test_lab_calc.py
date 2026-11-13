"""
Reproduces the battery_lab_toolkit's own test numbers (electrode,
slurry, N/P) against the lab_electrode.py / lab_slurry.py /
lab_np_ratio.py modules living in battery_analysis/lab/ — a
direct check that folding the toolkit in didn't change any of its math.
Combined into one file (rather than a tests/ package) so nothing here
needs a new folder. Run with:

    python -m pytest battery_analysis/lab/test_lab_calc.py -v
"""
import math

from battery_analysis.lab.lab_electrode import ElectrodeMeasurement
from battery_analysis.lab.lab_slurry import SlurryComponent, SlurryFormulation
from battery_analysis.lab.lab_np_ratio import np_ratio, np_ratio_from_electrodes

TOL = 1e-6  # relative tolerance for the cross-checks below


def approx(a, b, rel=TOL):
    return math.isclose(a, b, rel_tol=rel)


# =========================================================
# Electrode — "LFP Low Temperature" sheet
# =========================================================

def test_low_temp_bare():
    e = ElectrodeMeasurement(
        name="Bare (Low Temp)",
        foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.013, disc_thickness_mm=0.034,
        am_fraction=0.8, theoretical_capacity_mAhg=160,
        diameter_mm=14.0,
    )
    assert approx(e.am_mass_g, 0.0036)
    assert approx(e.coating_volume_cm3, 0.0021551325603626)
    assert approx(e.coating_density_gcc, 2.08803861199279)
    assert approx(e.loading_mg_cm2, 2.33860324543193)
    expected_currents = {
        0.05: 0.0288, 0.1: 0.0576, 0.5: 0.288, 1: 0.576, 3: 1.728,
        5: 2.88, 7: 4.032, 10: 5.76, 15: 8.64, 20: 11.52,
    }
    for c, mA in expected_currents.items():
        assert approx(e.current_at_c_rate(c), mA), f"{c}C: got {e.current_at_c_rate(c)}, expected {mA}"


def test_low_temp_mx():
    e = ElectrodeMeasurement(
        name="MX (Low Temp)",
        foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.0132, disc_thickness_mm=0.035,
        am_fraction=0.8, theoretical_capacity_mAhg=160,
        diameter_mm=14.0,
    )
    assert approx(e.am_mass_g, 0.00376)
    assert approx(e.coating_volume_cm3, 0.0023090706003885)
    assert approx(e.coating_density_gcc, 2.03545097287594)
    assert approx(e.loading_mg_cm2, 2.44254116745113)
    expected_currents = {
        0.05: 0.03008, 0.1: 0.06016, 0.5: 0.3008, 1: 0.6016, 3: 1.8048,
        5: 3.008, 7: 4.2112, 10: 6.016, 15: 9.024, 20: 12.032,
    }
    for c, mA in expected_currents.items():
        assert approx(e.current_at_c_rate(c), mA), f"{c}C: got {e.current_at_c_rate(c)}, expected {mA}"


# =========================================================
# Electrode — "LFP Cathode" sheet
# =========================================================

def test_cathode_bare():
    e = ElectrodeMeasurement(
        name="Bare (Cathode)",
        foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.0205, disc_thickness_mm=0.058,
        am_fraction=0.8, theoretical_capacity_mAhg=170,
        diameter_mm=14.0,
    )
    assert approx(e.am_mass_g, 0.0096)
    assert approx(e.coating_volume_cm3, 0.005849645520984201)
    assert approx(e.coating_density_gcc, 2.05140635564204)
    assert approx(e.loading_mg_cm2, 6.23627532115181)
    expected_currents = {
        0.05: 0.0816, 0.1: 0.1632, 0.5: 0.816, 1: 1.632, 3: 4.896,
        5: 8.16, 7: 11.424, 10: 16.32, 15: 24.48, 20: 32.64,
    }
    for c, mA in expected_currents.items():
        assert approx(e.current_at_c_rate(c), mA), f"{c}C: got {e.current_at_c_rate(c)}, expected {mA}"


def test_cathode_mx():
    e = ElectrodeMeasurement(
        name="MX (Cathode)",
        foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.0206, disc_thickness_mm=0.057,
        am_fraction=0.8, theoretical_capacity_mAhg=170,
        diameter_mm=14.0,
    )
    assert approx(e.am_mass_g, 0.00968)
    assert approx(e.coating_volume_cm3, 0.0056957074809583005)
    assert approx(e.coating_density_gcc, 2.12440685208156)
    assert approx(e.loading_mg_cm2, 6.28824428216141)
    expected_currents = {
        0.05: 0.08228, 0.1: 0.16456, 0.5: 0.8228, 1: 1.6456, 3: 4.9368,
        5: 8.228, 7: 11.5192, 10: 16.456, 15: 24.684, 20: 32.912,
    }
    for c, mA in expected_currents.items():
        assert approx(e.current_at_c_rate(c), mA), f"{c}C: got {e.current_at_c_rate(c)}, expected {mA}"


def test_electrode_rejects_disc_lighter_than_foil():
    try:
        ElectrodeMeasurement(
            name="bad", foil_weight_g=0.02, foil_thickness_mm=0.02,
            disc_weight_g=0.013, disc_thickness_mm=0.034,
            am_fraction=0.8, theoretical_capacity_mAhg=160, diameter_mm=14.0,
        )
        assert False, "should have raised"
    except ValueError as e:
        assert "must be greater than" in str(e)


def test_electrode_rejects_both_diameter_and_area():
    try:
        ElectrodeMeasurement(
            name="bad", foil_weight_g=0.0085, foil_thickness_mm=0.02,
            disc_weight_g=0.013, disc_thickness_mm=0.034,
            am_fraction=0.8, theoretical_capacity_mAhg=160,
            diameter_mm=14.0, area_cm2=1.5,
        )
        assert False, "should have raised"
    except ValueError as e:
        assert "exactly one of" in str(e)


# =========================================================
# Slurry — "Super-P and MXene" table, both sheets
# =========================================================

def test_super_p_mxene_low_temp():
    f = SlurryFormulation(
        components=[
            SlurryComponent(name="LFP", ratio=0.8),
            SlurryComponent(name="Super-P", ratio=0.1),
            SlurryComponent(name="PVDF", ratio=0.1, concentration_fraction=0.10),
        ],
        total_dry_solids_g=5.0,
        extra_solvent_g=10.0,
    )
    dry = {c.name: f.dry_mass_g(c) for c in f.components}
    assert approx(dry["LFP"], 4.0)
    assert approx(dry["Super-P"], 0.5)
    assert approx(dry["PVDF"], 0.5)
    pvdf = next(c for c in f.components if c.name == "PVDF")
    assert approx(f.dispensed_mass_g(pvdf), 5.0)
    assert approx(f.total_dry_solids_mass_g, 5.0)
    assert approx(f.total_slurry_mass_g, 19.5)
    assert approx(f.total_solids_pct, 25.641025641025642)


def test_super_p_mxene_cathode():
    f = SlurryFormulation(
        components=[
            SlurryComponent(name="LFP", ratio=0.8),
            SlurryComponent(name="Super-P", ratio=0.1),
            SlurryComponent(name="PVDF", ratio=0.1, concentration_fraction=0.10),
        ],
        total_dry_solids_g=3.0,
        extra_solvent_g=6.0,
    )
    dry = {c.name: f.dry_mass_g(c) for c in f.components}
    assert approx(dry["LFP"], 2.4)
    assert approx(dry["Super-P"], 0.3)
    assert approx(dry["PVDF"], 0.3)
    pvdf = next(c for c in f.components if c.name == "PVDF")
    assert approx(f.dispensed_mass_g(pvdf), 3.0)
    assert approx(f.total_dry_solids_mass_g, 3.0)
    assert approx(f.total_slurry_mass_g, 11.7)
    assert approx(f.total_solids_pct, 25.64102564102564)


def _cfal_formulation():
    return SlurryFormulation(
        components=[
            SlurryComponent(name="VGCF", ratio=0.8),
            SlurryComponent(name="CMC", ratio=0.2, concentration_fraction=0.015),
        ],
        total_dry_solids_g=0.5,
    )


def test_cfal_low_temp():
    f = _cfal_formulation()
    dry = {c.name: f.dry_mass_g(c) for c in f.components}
    assert approx(dry["VGCF"], 0.4)
    assert approx(dry["CMC"], 0.1)
    cmc = next(c for c in f.components if c.name == "CMC")
    assert approx(f.dispensed_mass_g(cmc), 6.66666666666667)
    assert approx(f.total_dry_solids_mass_g, 0.5)
    assert approx(f.total_solids_pct, 7.0754716981132075)


def test_cfal_cathode():
    f = _cfal_formulation()
    cmc = next(c for c in f.components if c.name == "CMC")
    assert approx(f.dispensed_mass_g(cmc), 6.66666666666667)
    assert approx(f.total_solids_pct, 7.0754716981132075)


def test_from_ratio_string_matches_manual_construction():
    a = SlurryFormulation.from_ratio_string(
        "8:1:0.5:0.5", ["LFP", "Binder", "Super-P", "Additive"],
        concentrations=[1.0, 0.10, 1.0, 1.0],
        basis_component_name="LFP", basis_mass_g=4.0,
    )
    b = SlurryFormulation(
        components=[
            SlurryComponent(name="LFP", ratio=8),
            SlurryComponent(name="Binder", ratio=1, concentration_fraction=0.10),
            SlurryComponent(name="Super-P", ratio=0.5),
            SlurryComponent(name="Additive", ratio=0.5),
        ],
        basis_component_name="LFP", basis_mass_g=4.0,
    )
    for ca, cb in zip(a.components, b.components):
        assert approx(a.dry_mass_g(ca), b.dry_mass_g(cb))
    assert approx(a.total_dry_solids_mass_g, b.total_dry_solids_mass_g)


def test_slurry_rejects_bad_concentration():
    try:
        SlurryComponent(name="bad", ratio=1, concentration_fraction=1.5)
        assert False, "should have raised"
    except ValueError as e:
        assert "concentration_fraction" in str(e)


def test_slurry_rejects_two_bases_at_once():
    try:
        SlurryFormulation(
            components=[SlurryComponent(name="A", ratio=1), SlurryComponent(name="B", ratio=1)],
            basis_component_name="A", basis_mass_g=1.0,
            total_dry_solids_g=2.0,
        )
        assert False, "should have raised"
    except ValueError as e:
        assert "exactly one basis" in str(e)


def test_slurry_rejects_no_basis():
    try:
        SlurryFormulation(components=[SlurryComponent(name="A", ratio=1), SlurryComponent(name="B", ratio=1)])
        assert False, "should have raised"
    except ValueError as e:
        assert "exactly one basis" in str(e)


# =========================================================
# N/P ratio
# =========================================================

def test_np_ratio_basic_division():
    assert approx(np_ratio(2.0, 1.0), 2.0)
    assert approx(np_ratio(1.0, 1.0), 1.0)


def test_np_ratio_rejects_non_positive():
    try:
        np_ratio(0, 1.0)
        assert False, "should have raised"
    except ValueError:
        pass


def test_np_ratio_from_electrodes_uses_areal_capacity():
    anode = ElectrodeMeasurement(
        name="Anode", foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.030, disc_thickness_mm=0.08,
        am_fraction=0.8, theoretical_capacity_mAhg=350, diameter_mm=14.0,
    )
    cathode = ElectrodeMeasurement(
        name="Bare (Cathode)", foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.0205, disc_thickness_mm=0.058,
        am_fraction=0.8, theoretical_capacity_mAhg=170, diameter_mm=14.0,
    )
    d = np_ratio_from_electrodes(anode, cathode)
    expected = anode.areal_capacity_mAh_cm2 / cathode.areal_capacity_mAh_cm2
    assert approx(d["np_ratio"], round(expected, 4), rel=1e-3)
    assert d["warning"] is None


def test_np_ratio_from_electrodes_warns_below_one():
    small_anode = ElectrodeMeasurement(
        name="Thin anode", foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.0095, disc_thickness_mm=0.025,
        am_fraction=0.8, theoretical_capacity_mAhg=160, diameter_mm=14.0,
    )
    big_cathode = ElectrodeMeasurement(
        name="Thick cathode", foil_weight_g=0.0085, foil_thickness_mm=0.02,
        disc_weight_g=0.0205, disc_thickness_mm=0.058,
        am_fraction=0.8, theoretical_capacity_mAhg=170, diameter_mm=14.0,
    )
    d = np_ratio_from_electrodes(small_anode, big_cathode)
    assert d["np_ratio"] < 1.0
    assert d["warning"] is not None
    assert "plating" in d["warning"]
