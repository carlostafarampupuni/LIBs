"""
Galvanostatic pulse protocols — the shared layer under Tafel and GITT.

A pulse file (one state of charge) typically holds:
    titration step (long, constant current)  -> moves to the next state of charge
    relaxation rest                           -> its end voltage is the OCV
    short pulses (+/- I_k), each followed by a rest   -> Tafel points / sqrt(t) data

    read_pulse_file(path)        -> DataFrame t_s, I_A, E_V   (any units in the header)
    segment(df)                  -> one row per constant-current step or rest
    analyse_state(df)            -> PulseState: OCV, titration, pulses (with E_before)
    particle_geometry(spec)      -> radius, area, volume from a particle / electrode spec
"""
from .io import read_pulse_file, pulse_files
from .segment import segment, analyse_state, PulseState
from .geometry import particle_geometry, molar_mass, R_GAS, FARADAY

__all__ = ["read_pulse_file", "pulse_files", "segment", "analyse_state", "PulseState",
           "particle_geometry", "molar_mass", "R_GAS", "FARADAY"]
