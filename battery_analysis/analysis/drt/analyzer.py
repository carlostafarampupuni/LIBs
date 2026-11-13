"""
DRT orchestration: pulls spectra straight from the eis_measurement table
(no new raw-file reading at all — DRT is a transform of data EIS already
imported), computes DRT via fitting.compute_drt_result, and saves to the
database. Only representative-folder measurements are used, matching
EIS's fit_eis() convention.
"""
from __future__ import annotations

from .fitting import compute_drt_result


def run_drt(config, drt_cfg, repo, project_id: int, batch_name: str | None = None) -> int:
    """
    Compute DRT for every representative-folder EIS measurement already in
    the database. Returns the number of results computed.

    Scoped to batch_name by default, matching fit_eis()/analyze_eis().
    """
    measurements_df = repo.list_eis_measurements(project_id, batch_name=batch_name)
    n = 0
    for _, row in measurements_df.iterrows():
        if not row["is_representative"]:
            continue
        spectrum = repo.load_eis_spectrum(row["measurement_id"])
        if spectrum.empty:
            continue

        try:
            result = compute_drt_result(
                spectrum["freq"].to_numpy(),
                spectrum["z_real"].to_numpy(),
                spectrum["z_imag"].to_numpy(),
                n_tau=drt_cfg.n_tau,
                lambda_reg=drt_cfg.lambda_reg,
                rbf_type=drt_cfg.rbf_type,
                data_use=drt_cfg.data_use,
                reg_order=drt_cfg.reg_order,
                fwhm_coeff=drt_cfg.fwhm_coeff,
                inductance=drt_cfg.inductance,
                engine=drt_cfg.engine,
                lambda_selection=drt_cfg.lambda_selection,
            )
        except ValueError as e:
            print(f"⚠️  DRT failed for {row['sample']} [{row['sheet_name']}]: {e}")
            continue

        repo.save_drt_result(
            row["measurement_id"], result.r_inf, result.l_ind,
            result.tau, result.gamma,
            [
                dict(label=p.label, description=p.description, tau_peak=p.tau_peak,
                     gamma_peak=p.gamma_peak, log_tau=p.log_tau, frequency_hz=p.frequency_hz)
                for p in result.peaks
            ],
            n_tau=drt_cfg.n_tau, lambda_reg=drt_cfg.lambda_reg, rbf_type=drt_cfg.rbf_type,
            data_use=drt_cfg.data_use, reg_order=drt_cfg.reg_order, fwhm_coeff=drt_cfg.fwhm_coeff,
        )
        n += 1

    print(f"✅  Computed DRT for {n} EIS measurement(s).")
    return n
