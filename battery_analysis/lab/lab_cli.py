"""
Interactive prompts — run this directly (`python -m battery_analysis.lab.lab_cli`)
and it'll ask you for mass/thickness/area/etc. one at a time, the same
way you'd fill in a spreadsheet row, and print the computed results.
Nothing here is required for batch runs (see lab_batch.py /
project.run_lab_batch()) — this is for "I've got one disc on the bench
right now."

Ported from the battery_lab_toolkit's electrode_lab/cli.py, folded
directly into battery_analysis (flat module, no new folder).
"""
from __future__ import annotations

from .lab_electrode import ElectrodeMeasurement, DEFAULT_C_RATES
from .lab_slurry import SlurryComponent, SlurryFormulation
from .lab_np_ratio import report as np_report


def _prompt_float(msg: str, default=None) -> float:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{msg}{suffix}: ").strip()
        if raw == "" and default is not None:
            return float(default)
        try:
            return float(raw)
        except ValueError:
            print(f"  '{raw}' isn't a number — try again.")


def _prompt_str(msg: str, default=None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{msg}{suffix}: ").strip()
    return raw or (default or "")


def _prompt_yes_no(msg: str, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = input(f"{msg}{suffix}: ").strip().lower()
    if raw == "":
        return default
    return raw.startswith("y")


def run_electrode_flow() -> ElectrodeMeasurement:
    print("\n--- Electrode disc measurement ---")
    name = _prompt_str("Sample name", default="Sample1")
    foil_weight_g = _prompt_float("Bare foil weight (g)")
    foil_thickness_mm = _prompt_float("Bare foil thickness (mm)")
    disc_weight_g = _prompt_float("Coated disc weight, total (g)")
    disc_thickness_mm = _prompt_float("Coated disc thickness, total (mm)")
    am_fraction = _prompt_float("Active material fraction in the dry coating (0-1)", default=0.8)
    theoretical_capacity = _prompt_float("Theoretical specific capacity (mAh/g)")

    if _prompt_yes_no("Enter area directly instead of a punch diameter?", default=False):
        area_cm2 = _prompt_float("Punch area (cm^2)")
        diameter_mm = None
    else:
        diameter_mm = _prompt_float("Punch diameter (mm)", default=14.0)
        area_cm2 = None

    custom_rates = _prompt_str(
        "C-rates to compute, comma-separated", default=",".join(str(c) for c in DEFAULT_C_RATES)
    )
    c_rates = [float(x.strip()) for x in custom_rates.split(",") if x.strip()]

    electrode = ElectrodeMeasurement(
        name=name, foil_weight_g=foil_weight_g, foil_thickness_mm=foil_thickness_mm,
        disc_weight_g=disc_weight_g, disc_thickness_mm=disc_thickness_mm,
        am_fraction=am_fraction, theoretical_capacity_mAhg=theoretical_capacity,
        diameter_mm=diameter_mm, area_cm2=area_cm2, c_rates=c_rates,
    )
    print()
    print(electrode.report())
    return electrode


def run_slurry_flow() -> SlurryFormulation:
    print("\n--- Slurry formulation ---")
    print("Enter each component; leave the name blank when you're done.")
    components = []
    while True:
        name = _prompt_str(f"  Component {len(components) + 1} name (blank to finish)")
        if not name:
            if len(components) < 2:
                print("  Need at least 2 components — keep going.")
                continue
            break
        ratio = _prompt_float(f"    {name}: ratio (e.g. the '8' in 8:1:0.5:0.5)")
        is_stock = _prompt_yes_no(f"    {name}: delivered as a dilute stock solution (not pure powder)?", default=False)
        conc = _prompt_float(f"    {name}: stock concentration, wt% (e.g. 10 for a 10% solution)", default=100) / 100 \
            if is_stock else 1.0
        components.append(SlurryComponent(name=name, ratio=ratio, concentration_fraction=conc))

    print("\nBasis: what actual batch size are you scaling this recipe to?")
    if _prompt_yes_no("Scale to a total dry-solids mass instead of one component's mass?", default=False):
        total_dry_solids_g = _prompt_float("Total dry solids across all components (g)")
        kwargs = dict(total_dry_solids_g=total_dry_solids_g)
    else:
        print("Components:", ", ".join(c.name for c in components))
        basis_name = _prompt_str("Which component are you setting the mass of?", default=components[0].name)
        basis_mass = _prompt_float(f"{basis_name} mass (g)")
        kwargs = dict(basis_component_name=basis_name, basis_mass_g=basis_mass)

    if _prompt_yes_no("Target a specific total-solids % instead of entering extra solvent directly?", default=False):
        kwargs["target_total_solids_pct"] = _prompt_float("Target total solids (%)")
    else:
        kwargs["extra_solvent_g"] = _prompt_float("Extra pure solvent to add on top of the stocks (g)", default=0)

    formulation = SlurryFormulation(components=components, **kwargs)
    print()
    print(formulation.report())
    return formulation


def run_np_flow():
    print("\n--- N/P ratio ---")
    print("Anode:")
    anode = run_electrode_flow()
    print("\nCathode:")
    cathode = run_electrode_flow()
    print()
    print(np_report(anode, cathode))


def main():
    print("battery_analysis lab calculators — electrode / slurry / N-P")
    print("(for many samples at once, use lab_batch.py / project.run_lab_batch() instead)\n")
    menu = {
        "1": ("Electrode: loading / density / areal capacity / C-rate currents", run_electrode_flow),
        "2": ("Slurry formulation: masses + total solids", run_slurry_flow),
        "3": ("N/P ratio (measures two electrodes)", run_np_flow),
    }
    while True:
        print("\nWhat do you want to calculate?")
        for k, (label, _) in menu.items():
            print(f"  {k}. {label}")
        print("  q. Quit")
        choice = input("> ").strip().lower()
        if choice == "q":
            break
        action = menu.get(choice)
        if action is None:
            print("Not a valid choice.")
            continue
        try:
            action[1]()
        except ValueError as e:
            print(f"\n!! {e}")


if __name__ == "__main__":
    main()
