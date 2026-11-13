"""
N/P ratio — the anode-to-cathode areal capacity ratio for a full-cell
build. Below 1.0 the anode can't accept all the lithium the cathode
offers on charge (lithium plating risk); the usual target for a
graphite/hard-carbon-type anode is roughly 1.05-1.2, higher for anodes
with more irreversible first-cycle loss.

Ported from the battery_lab_toolkit's electrode_lab/np_ratio.py, folded
directly into battery_analysis (flat module, no new folder) — this
module just divides one lab_electrode.ElectrodeMeasurement's areal
capacity by another's, with the same input validation the rest of this
toolkit uses.
"""
from __future__ import annotations

from typing import Optional

from .lab_electrode import ElectrodeMeasurement


def np_ratio(anode_areal_capacity_mAh_cm2: float, cathode_areal_capacity_mAh_cm2: float) -> float:
    if anode_areal_capacity_mAh_cm2 <= 0 or cathode_areal_capacity_mAh_cm2 <= 0:
        raise ValueError("Both areal capacities must be > 0.")
    return anode_areal_capacity_mAh_cm2 / cathode_areal_capacity_mAh_cm2


def np_ratio_from_electrodes(anode: ElectrodeMeasurement, cathode: ElectrodeMeasurement) -> dict:
    """
    Full breakdown, not just the number — this is what you actually want
    when deciding whether a build is safe: the two areal capacities that
    produced the ratio, plus a quick call-out if it's below 1.0.
    """
    ratio = np_ratio(anode.areal_capacity_mAh_cm2, cathode.areal_capacity_mAh_cm2)
    return {
        "anode": anode.name,
        "cathode": cathode.name,
        "anode_areal_capacity_mAh_cm2": round(anode.areal_capacity_mAh_cm2, 6),
        "cathode_areal_capacity_mAh_cm2": round(cathode.areal_capacity_mAh_cm2, 6),
        "np_ratio": round(ratio, 4),
        "warning": (
            "N/P < 1.0 — anode capacity is LOWER than cathode capacity; "
            "lithium plating risk on charge." if ratio < 1.0 else None
        ),
    }


def report(anode: ElectrodeMeasurement, cathode: ElectrodeMeasurement,
           target_range: Optional[tuple] = (1.05, 1.20)) -> str:
    d = np_ratio_from_electrodes(anode, cathode)
    lines = [f"=== N/P ratio: {d['anode']} (anode) / {d['cathode']} (cathode) ==="]
    lines.append(f"  Anode areal capacity:   {d['anode_areal_capacity_mAh_cm2']:.4f} mAh/cm^2")
    lines.append(f"  Cathode areal capacity: {d['cathode_areal_capacity_mAh_cm2']:.4f} mAh/cm^2")
    lines.append(f"  N/P ratio:              {d['np_ratio']:.4f}")
    if d["warning"]:
        lines.append(f"  !! {d['warning']}")
    elif target_range and not (target_range[0] <= d["np_ratio"] <= target_range[1]):
        lines.append(f"  Note: outside the typical {target_range[0]}-{target_range[1]} target range.")
    return "\n".join(lines)
