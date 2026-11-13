"""
dQ/dV peak tracking across cycles — a lightweight LLI (loss of lithium
inventory) vs LAM (loss of active material) diagnostic.

READ THIS BEFORE TRUSTING THE VERDICT. The textbook LLI/LAM separation
(Dubarry et al.) reconstructs a full cell's dQ/dV curve from
INDEPENDENTLY measured half-cell reference curves for the anode and the
cathode, then fits how much each electrode's curve has to slide
(lithium inventory lost) and shrink (active material lost) to reproduce
the aged full-cell curve. That needs two separately-characterized
half-cells and a curve-fitting step this project has no inputs for.

What's here instead is a PROXY appropriate for what this project
actually has — half-cells, cathode vs Li metal (see config.yaml's
cell_defaults) — built by tracking each phase-transition peak across
cycles:

  - a peak's INTENSITY dropping is read as a LAM-like signal — less
    active material is left engaging in that particular redox reaction.
  - a peak's VOLTAGE POSITION shifting is read as an LLI/kinetic-like
    signal — extra overpotential (impedance growth, lithium plating,
    concentration polarization) is pushing the reaction to a different
    voltage.

This is a genuinely useful trend indicator, and a fair, consistent way
to compare samples against each other under identical thresholds. It is
NOT the literature LLI/LAM percentage split — don't quote these numbers
as if they were a validated capacity-loss breakdown. Use it to flag
which samples/cycles are worth a closer look, not as a final answer.
"""
from __future__ import annotations

import pandas as pd

# A peak's position typically drifts a couple mV cycle-to-cycle from
# ordinary curve noise/binning alone; SHIFT_THRESHOLD_V is how far it has
# to move before that's treated as a real kinetic/LLI-like signal rather
# than noise. INTENSITY_DROP_PCT/SEVERE_INTENSITY_DROP_PCT are how much a
# peak's height has to fall before that's read as a real LAM-like signal
# (mild) or a severe one, rather than normal measurement noise.
SHIFT_THRESHOLD_V = 0.015
INTENSITY_DROP_PCT = 20.0
SEVERE_INTENSITY_DROP_PCT = 50.0
# A peak shifting AND clearly growing (not fading) reads as early-cycle
# activation/SEI stabilization — wetting and kinetics still settling in
# — not degradation. Only a shift paired with flat-or-fading intensity
# gets the "extra overpotential" degradation framing.
ACTIVATION_GROWTH_PCT = 20.0
# Below this last-tracked cycle, verdicts get an early-cycle caveat —
# LLI/LAM is conventionally a life-long fade diagnosis; a handful of
# formation-adjacent checkup cycles mostly show activation, not fade.
EARLY_CYCLE_CAVEAT_THRESHOLD = 15


def track_peaks(repo, experiment_id: int, mode: str, match_tolerance_v: float = 0.08,
                 top_n_per_cycle: int = 8) -> pd.DataFrame:
    """
    One row per (track_id, cycle_number, voltage, intensity) — every
    cycle's peaks matched into cross-cycle "tracks" by nearest voltage
    position, across every cycle imported for this experiment/mode (not
    just first-vs-last). The earliest cycle's peaks each seed their own
    track; every later cycle's peaks are matched to their nearest
    EXISTING track within match_tolerance_v — strongest peaks (by
    |intensity|) matched first, so two peaks close together don't both
    grab the same track — and any peak that doesn't fall within
    tolerance of an existing track starts a brand new one instead of
    being dropped, so a genuinely new redox feature (e.g. a
    failure-driven side reaction) doesn't just disappear from the
    picture; it shows up as a short, separate track instead.

    Only the top_n_per_cycle peaks by |intensity| feed the tracker per
    cycle — same reasoning as analyzer.narrative()'s top_n: raw dQ/dV
    near a charge/discharge voltage cutoff produces dozens to hundreds of
    spurious near-duplicate local maxima (CC-CV current tapering), and
    matching every one of those into tracks would swamp genuine
    phase-transition peaks with noise-driven "tracks" that appear for one
    cycle and vanish the next — the intensity_change_pct on a track like
    that isn't a real LAM/LLI signal, it's an artifact of which noise
    spike the peak-finder happened to catch that cycle.
    """
    cycles_df = repo.list_dqdv_cycles(experiment_id)
    cycles_df = cycles_df[cycles_df["mode"] == mode].sort_values("cycle_number")
    if cycles_df.empty:
        return pd.DataFrame(columns=["track_id", "cycle_number", "voltage", "intensity"])

    rows = []
    track_last_voltage: dict[int, float] = {}
    next_track_id = 0

    for cycle_number in cycles_df["cycle_number"]:
        peaks_df = repo.load_dqdv_peaks(experiment_id, int(cycle_number), mode)
        if peaks_df.empty:
            continue
        peaks_df = peaks_df.reindex(peaks_df["intensity"].abs().sort_values(ascending=False).index)
        peaks_df = peaks_df.head(top_n_per_cycle)

        claimed = set()
        for _, p in peaks_df.iterrows():
            voltage, intensity = float(p["voltage"]), float(p["intensity"])
            best_track, best_dist = None, match_tolerance_v
            for track_id, last_voltage in track_last_voltage.items():
                if track_id in claimed:
                    continue
                dist = abs(voltage - last_voltage)
                if dist < best_dist:
                    best_track, best_dist = track_id, dist
            if best_track is None:
                best_track = next_track_id
                next_track_id += 1
            claimed.add(best_track)
            track_last_voltage[best_track] = voltage
            rows.append({"track_id": best_track, "cycle_number": int(cycle_number),
                         "voltage": voltage, "intensity": intensity})

    return pd.DataFrame(rows, columns=["track_id", "cycle_number", "voltage", "intensity"])


def classify_tracks(tracks_df: pd.DataFrame) -> list[dict]:
    """
    One dict per track seen in at least 2 cycles — a track matched only
    once has nothing to compare against yet, so it's skipped here (it's
    still visible in tracks_df itself if you want it). first/last are
    the EARLIEST and LATEST cycle that track was matched in, not
    necessarily every cycle in between — a peak that temporarily drops
    below the prominence threshold and reappears later is still treated
    as the same track, compared start to end.
    """
    out = []
    for track_id, g in tracks_df.groupby("track_id"):
        g = g.sort_values("cycle_number")
        if len(g) < 2:
            continue
        first, last = g.iloc[0], g.iloc[-1]
        intensity_change_pct = (
            100 * (last["intensity"] - first["intensity"]) / abs(first["intensity"])
            if first["intensity"] else 0.0
        )
        voltage_shift_v = last["voltage"] - first["voltage"]

        lam_signal = intensity_change_pct <= -INTENSITY_DROP_PCT
        lli_signal = abs(voltage_shift_v) >= SHIFT_THRESHOLD_V
        # A shift paired with the peak clearly GROWING, not fading, reads
        # as activation (wetting/SEI still settling in) rather than the
        # "extra overpotential" degradation story — those need opposite
        # recommendations, so they're not allowed to collapse into one
        # generic "LLI-like" label.
        activation_signal = lli_signal and intensity_change_pct >= ACTIVATION_GROWTH_PCT

        if lam_signal and lli_signal:
            verdict = "Mixed LLI + LAM signature"
        elif lam_signal:
            severity = "Severe" if intensity_change_pct <= -SEVERE_INTENSITY_DROP_PCT else "Moderate"
            verdict = f"{severity} LAM-like signature (less active material engaging in this reaction)"
        elif activation_signal:
            direction = "later" if voltage_shift_v > 0 else "earlier"
            verdict = f"Activation-like signature (peak intensifying and shifting {direction} — likely wetting/SEI still settling in, not degradation)"
        elif lli_signal:
            direction = "later" if voltage_shift_v > 0 else "earlier"
            verdict = f"LLI/kinetic-like signature (reaction shifting to {direction} voltage — extra overpotential)"
        else:
            verdict = "Stable"

        out.append(dict(
            track_id=int(track_id),
            first_cycle=int(first["cycle_number"]), last_cycle=int(last["cycle_number"]),
            first_voltage=float(first["voltage"]), last_voltage=float(last["voltage"]),
            voltage_shift_v=float(voltage_shift_v),
            first_intensity=float(first["intensity"]), last_intensity=float(last["intensity"]),
            intensity_change_pct=float(intensity_change_pct),
            verdict=verdict,
        ))
    return sorted(out, key=lambda t: t["first_voltage"])


def diagnose(repo, experiment_id: int, mode: str, sample_name: str,
             match_tolerance_v: float = 0.08, top_n_per_cycle: int = 8) -> dict:
    """Track + classify every peak for one experiment/mode, and roll the
    tracks up into one overall verdict. See the module docstring for what
    "LLI/LAM-like" does and doesn't mean here."""
    tracks_df = track_peaks(repo, experiment_id, mode, match_tolerance_v=match_tolerance_v,
                             top_n_per_cycle=top_n_per_cycle)
    tracks = classify_tracks(tracks_df)
    lam_tracks = [t for t in tracks if "LAM" in t["verdict"]]
    lli_tracks = [t for t in tracks if "LLI" in t["verdict"] and "LAM" not in t["verdict"]]
    activation_tracks = [t for t in tracks if "Activation" in t["verdict"]]

    if not tracks:
        overall = "Not enough cycles with resolved peaks yet to track anything."
    elif lam_tracks and lli_tracks:
        overall = (
            f"Both signatures present — {len(lam_tracks)}/{len(tracks)} tracked reaction(s) "
            f"losing active material, {len(lli_tracks)}/{len(tracks)} shifting position."
        )
    elif lam_tracks:
        overall = f"LAM-dominated — {len(lam_tracks)}/{len(tracks)} tracked reaction(s) losing intensity."
    elif lli_tracks:
        overall = f"LLI/kinetic-dominated — {len(lli_tracks)}/{len(tracks)} tracked reaction(s) shifting voltage."
    elif activation_tracks:
        overall = (
            f"Activation-like — {len(activation_tracks)}/{len(tracks)} tracked reaction(s) intensifying "
            "and shifting, consistent with early-cycle wetting/SEI settling rather than degradation."
        )
    else:
        overall = "Stable — no tracked reaction shows a significant intensity drop or voltage shift."

    last_cycle_seen = int(tracks_df["cycle_number"].max()) if not tracks_df.empty else 0
    if 0 < last_cycle_seen < EARLY_CYCLE_CAVEAT_THRESHOLD:
        overall += (
            f" (Only up to cycle {last_cycle_seen} analyzed so far — this early, LLI/LAM is "
            "conventionally read as a life-long fade diagnosis, so treat this as an early trend "
            "to watch, not a settled verdict.)"
        )

    return dict(sample=sample_name, mode=mode, tracks=tracks, overall=overall)


def narrative(result: dict) -> str:
    lines = [f"dQ/dV degradation signature — {result['sample']} ({result['mode']})",
             f"  {result['overall']}"]
    for t in result["tracks"]:
        if t["verdict"] == "Stable":
            continue
        lines.append(
            f"  Track @ {t['first_voltage']:.3f} V (cycle {t['first_cycle']}) -> "
            f"{t['last_voltage']:.3f} V (cycle {t['last_cycle']}): "
            f"intensity {t['intensity_change_pct']:+.0f}%, shift {1000 * t['voltage_shift_v']:+.0f} mV "
            f"— {t['verdict']}"
        )
    return "\n".join(lines)
