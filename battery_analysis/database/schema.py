"""
Table DDL for project.db — experiment-centric design.

Three shared tables sit above every technique:

  project    — one row per project (this config.yaml)
  sample     — a material/electrode identity (e.g. "Bare", "PA800")
  experiment — one row per PHYSICAL CELL / test article: which sample,
               which raw folder, cathode/anode, full-or-half cell,
               electrolyte, additives, temperature, replicate number,
               date, notes. This is the "which experiment was this"
               answer, and it's what a technique table joins against.

Every technique's tables reference experiment_id, not sample_id
directly — so a Nyquist sweep, a cycling curve, and an XPS peak taken on
the same physical cell are all findable from one experiment record, and
a future cross-technique report can join precisely on "the same cell"
rather than loosely on "the same sample name".

All tables — including techniques not yet migrated — are created up
front (idempotently) the first time a Project opens its database.
"""

SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    config_path TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sample (
    id INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES project(id),
    name TEXT NOT NULL,
    color TEXT,
    group_label TEXT,
    metadata_json TEXT,
    UNIQUE(project_id, name)
);

-- One row per physical cell / test article. This is the record that
-- answers "which experiment was this" — every technique table below
-- hangs off experiment_id.
CREATE TABLE IF NOT EXISTS experiment (
    id INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES project(id),
    sample_id INTEGER REFERENCES sample(id),
    technique TEXT NOT NULL,          -- 'eis', 'cycle', 'xps', ...
    source_folder TEXT,               -- raw data subfolder, e.g. "Bare2"
    batch_name TEXT,
    replicate_number INTEGER,         -- 1, 2, 3... among this sample's folders, for reproducibility tracking
    is_representative INTEGER DEFAULT 0,  -- 1 for the folder chosen to represent this sample in per-cycle summaries
    cathode TEXT,
    anode TEXT,
    cell_type TEXT,                   -- 'full' | 'half'
    electrolyte TEXT,
    additives_json TEXT,              -- JSON list, e.g. ["FEC", "VC"]
    temperature_c REAL,
    experiment_date TEXT,             -- ISO date; from config override or earliest raw file mtime
    notes TEXT,
    created_at TEXT,
    UNIQUE(project_id, sample_id, technique, source_folder, batch_name)
);

CREATE TABLE IF NOT EXISTS raw_file (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER REFERENCES experiment(id),
    path TEXT NOT NULL,
    imported_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_experiment_lookup
    ON experiment(project_id, sample_id, technique);

CREATE INDEX IF NOT EXISTS idx_raw_file_experiment
    ON raw_file(experiment_id);
"""

# --- EIS (migrated) -------------------------------------------------------
EIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS eis_measurement (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER REFERENCES experiment(id),
    sheet_name TEXT,
    cycle_number TEXT
);

CREATE TABLE IF NOT EXISTS eis_spectrum_point (
    measurement_id INTEGER REFERENCES eis_measurement(id),
    idx INTEGER,
    freq REAL,
    z_real REAL,
    z_imag REAL
);

CREATE TABLE IF NOT EXISTS eis_fit_result (
    measurement_id INTEGER REFERENCES eis_measurement(id),
    circuit_key TEXT,
    param_name TEXT,
    value REAL,
    method TEXT
);

CREATE INDEX IF NOT EXISTS idx_eis_measurement_experiment
    ON eis_measurement(experiment_id);
CREATE INDEX IF NOT EXISTS idx_eis_spectrum_point_measurement
    ON eis_spectrum_point(measurement_id);
CREATE INDEX IF NOT EXISTS idx_eis_fit_result_measurement
    ON eis_fit_result(measurement_id);
"""

# --- Not yet migrated — tables created now, populated once each is -------
CYCLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cycle_point (
    experiment_id INTEGER REFERENCES experiment(id),
    cycle_number INTEGER,
    capacity_charge REAL,
    capacity_discharge REAL,
    ce_pct REAL,
    retention_pct REAL
);

CREATE INDEX IF NOT EXISTS idx_cycle_point_experiment
    ON cycle_point(experiment_id);
"""

RATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_point (
    experiment_id INTEGER REFERENCES experiment(id),
    rate_cycle_index INTEGER,
    capacity_charge REAL,
    capacity_discharge REAL,
    ce_pct REAL
);

CREATE INDEX IF NOT EXISTS idx_rate_point_experiment
    ON rate_point(experiment_id);
"""

CE_GCD_SCHEMA = """
-- CE and GCD are the same underlying technique (Zive/Wonatech Li-Li
-- symmetric-cell plating/stripping) — ce.py turned out to be gcd.py plus
-- one extra calculation (ce_method3). Both use these tables, technique='gcd'.

CREATE TABLE IF NOT EXISTS gcd_trace_point (
    experiment_id INTEGER REFERENCES experiment(id),
    idx INTEGER,
    time_h REAL,
    voltage_v REAL
);

CREATE TABLE IF NOT EXISTS gcd_cycle_result (
    experiment_id INTEGER REFERENCES experiment(id),
    cycle INTEGER,
    q_plate REAL,
    q_strip REAL,
    ce_pct REAL,
    lli REAL,
    lli_cumul REAL,
    cap_ret_pct REAL,
    t_nucleation_h REAL,
    v_nucleation REAL,
    genuine_nuc_dip INTEGER,
    v_plateau_plate REAL,
    eta_nuc REAL,
    t_pol_max_h REAL,
    v_pol_max REAL,
    v_plateau_strip REAL,
    strip_shape TEXT,
    hysteresis REAL
);

CREATE TABLE IF NOT EXISTS gcd_anomaly (
    experiment_id INTEGER REFERENCES experiment(id),
    cycle INTEGER,
    phase TEXT,
    time_h REAL,
    voltage_v REAL,
    delta_v REAL
);

CREATE TABLE IF NOT EXISTS gcd_method3_result (
    experiment_id INTEGER REFERENCES experiment(id),
    qt REAL,
    qs REAL,
    qc_avg REAL,
    ce_avg REAL,
    ce_initial REAL
);

CREATE INDEX IF NOT EXISTS idx_gcd_trace_point_experiment
    ON gcd_trace_point(experiment_id);
CREATE INDEX IF NOT EXISTS idx_gcd_cycle_result_experiment
    ON gcd_cycle_result(experiment_id);
CREATE INDEX IF NOT EXISTS idx_gcd_anomaly_experiment
    ON gcd_anomaly(experiment_id);
CREATE INDEX IF NOT EXISTS idx_gcd_method3_result_experiment
    ON gcd_method3_result(experiment_id);
"""

RIC_SCHEMA = """
-- RIC = (Cc-Cd)/Cd (overall irreversible capacity); RIC_disconn and
-- RIC_sei are one row shorter than the cycle series (computed between
-- consecutive cycles), so they're NULL on the last row.
CREATE TABLE IF NOT EXISTS ric_point (
    experiment_id INTEGER REFERENCES experiment(id),
    cycle_number INTEGER,
    cc REAL,
    cd REAL,
    ric REAL,
    ric_disconn REAL,
    ric_sei REAL
);

CREATE INDEX IF NOT EXISTS idx_ric_point_experiment
    ON ric_point(experiment_id);
"""

DQDV_SCHEMA = """
CREATE TABLE IF NOT EXISTS dqdv_point (
    experiment_id INTEGER REFERENCES experiment(id),
    cycle_number INTEGER,
    mode TEXT,              -- 'charge' | 'discharge'
    idx INTEGER,
    voltage REAL,
    dqdv REAL
);

CREATE TABLE IF NOT EXISTS dqdv_peak (
    experiment_id INTEGER REFERENCES experiment(id),
    cycle_number INTEGER,
    mode TEXT,
    voltage REAL,
    intensity REAL
);

CREATE INDEX IF NOT EXISTS idx_dqdv_point_experiment
    ON dqdv_point(experiment_id);
CREATE INDEX IF NOT EXISTS idx_dqdv_peak_experiment
    ON dqdv_peak(experiment_id);
"""

DRT_SCHEMA = """
-- DRT is a transform of an existing EIS spectrum, not an independent raw
-- measurement — so it references eis_measurement_id directly rather than
-- duplicating experiment/cycle bookkeeping.
CREATE TABLE IF NOT EXISTS drt_result (
    id INTEGER PRIMARY KEY,
    eis_measurement_id INTEGER REFERENCES eis_measurement(id),
    r_inf REAL,
    l_ind REAL,
    n_tau INTEGER,
    lambda_reg REAL,
    rbf_type TEXT,
    data_use TEXT,
    reg_order INTEGER,
    fwhm_coeff REAL
);

CREATE TABLE IF NOT EXISTS drt_point (
    result_id INTEGER REFERENCES drt_result(id),
    idx INTEGER,
    tau REAL,
    gamma REAL
);

CREATE TABLE IF NOT EXISTS drt_peak (
    result_id INTEGER REFERENCES drt_result(id),
    label TEXT,
    description TEXT,
    tau_peak REAL,
    gamma_peak REAL,
    log_tau REAL,
    frequency_hz REAL
);

CREATE INDEX IF NOT EXISTS idx_drt_result_eis_measurement
    ON drt_result(eis_measurement_id);
CREATE INDEX IF NOT EXISTS idx_drt_point_result
    ON drt_point(result_id);
CREATE INDEX IF NOT EXISTS idx_drt_peak_result
    ON drt_peak(result_id);
"""

PROFILES_SCHEMA = """
-- Voltage-vs-capacity profile points for one ordinal cycle/mode, read
-- straight out of a *_Cycle.csv file (same file dQ/dV already reads) —
-- keyed to the SAME experiment_id import_cycle()/import_rate() already
-- created for that sample, not a separate experiment. mode is
-- 'charge' | 'discharge'; idx preserves the row's original order in the
-- raw file (voltage/capacity are NOT sorted -- sorting destroys the
-- physical charge/discharge trajectory). time/current are kept for any
-- future raw format that has them; the *_Cycle.csv reader never
-- populates them.
CREATE TABLE IF NOT EXISTS profiles_point (
    experiment_id INTEGER REFERENCES experiment(id), cycle_number INTEGER,
    time REAL, voltage REAL, current REAL,
    mode TEXT, idx INTEGER, capacity REAL
);

CREATE INDEX IF NOT EXISTS idx_profiles_point_experiment
    ON profiles_point(experiment_id);
"""

CV_SCHEMA = """
-- cycle_number is unused by the CV importer (kept for the original
-- generic shape); scan_rate is the real index — one sweep per scan rate.
CREATE TABLE IF NOT EXISTS cv_point (
    experiment_id INTEGER REFERENCES experiment(id), cycle_number INTEGER,
    scan_rate REAL, idx INTEGER, voltage REAL, current REAL
);

CREATE INDEX IF NOT EXISTS idx_cv_point_experiment
    ON cv_point(experiment_id);
"""

LSV_SCHEMA = """
CREATE TABLE IF NOT EXISTS lsv_point (
    experiment_id INTEGER REFERENCES experiment(id), voltage REAL, current REAL
);

CREATE INDEX IF NOT EXISTS idx_lsv_point_experiment
    ON lsv_point(experiment_id);
"""

GITT_SCHEMA = """
CREATE TABLE IF NOT EXISTS gitt_point (
    experiment_id INTEGER REFERENCES experiment(id), step_number INTEGER,
    time REAL, voltage REAL, current REAL
);
CREATE TABLE IF NOT EXISTS gitt_diffusion_result (
    experiment_id INTEGER REFERENCES experiment(id), step_number INTEGER, diffusion_coefficient REAL
);

CREATE INDEX IF NOT EXISTS idx_gitt_point_experiment
    ON gitt_point(experiment_id);
CREATE INDEX IF NOT EXISTS idx_gitt_diffusion_result_experiment
    ON gitt_diffusion_result(experiment_id);
"""

SEM_SCHEMA = """
-- One row per (experiment, metric) — an experiment here is one imaged
-- sample+role+image (see analysis/sem/importer.py). role identifies
-- which analysis group the image belongs to ('cross_section' | 'surface'
-- | 'powder'); image_path is the raw file's path, kept for provenance
-- even though raw_file already records it too.
CREATE TABLE IF NOT EXISTS sem_metric (
    experiment_id INTEGER REFERENCES experiment(id), metric_name TEXT, value REAL,
    role TEXT, image_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_sem_metric_experiment
    ON sem_metric(experiment_id);
"""

FTIR_SCHEMA = """
CREATE TABLE IF NOT EXISTS ftir_point (
    experiment_id INTEGER REFERENCES experiment(id),
    spectrum_type TEXT,     -- 'Reflectance' | 'Absorbance' | 'Transmittance', ...
    idx INTEGER,
    wavenumber REAL,
    value REAL
);

CREATE INDEX IF NOT EXISTS idx_ftir_point_experiment
    ON ftir_point(experiment_id);
"""

XRD_SCHEMA = """
CREATE TABLE IF NOT EXISTS xrd_point (
    experiment_id INTEGER REFERENCES experiment(id), two_theta REAL, intensity REAL
);
CREATE TABLE IF NOT EXISTS xrd_peak (
    experiment_id INTEGER REFERENCES experiment(id), two_theta REAL, d_spacing REAL, assignment TEXT
);

CREATE INDEX IF NOT EXISTS idx_xrd_point_experiment
    ON xrd_point(experiment_id);
CREATE INDEX IF NOT EXISTS idx_xrd_peak_experiment
    ON xrd_peak(experiment_id);
"""

XPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS xps_trace_point (
    experiment_id INTEGER REFERENCES experiment(id),
    orbital TEXT,
    idx INTEGER,
    binding_energy REAL,
    raw_intensity REAL,
    peak_sum REAL,
    background REAL
);

CREATE TABLE IF NOT EXISTS xps_peak (
    experiment_id INTEGER REFERENCES experiment(id),
    orbital TEXT,
    binding_energy REAL,
    area REAL,
    fraction_pct REAL,
    assignment TEXT
);

CREATE TABLE IF NOT EXISTS xps_peak_curve (
    experiment_id INTEGER REFERENCES experiment(id),
    orbital TEXT,
    peak_index INTEGER,
    idx INTEGER,
    value REAL
);

-- experiment_id+orbital is the actual hot filter (see repository.py's
-- load_xps_trace/clear_xps_data) — a compound index serves that
-- directly instead of narrowing to experiment_id then scanning orbitals.
CREATE INDEX IF NOT EXISTS idx_xps_trace_point_experiment_orbital
    ON xps_trace_point(experiment_id, orbital);
CREATE INDEX IF NOT EXISTS idx_xps_peak_experiment_orbital
    ON xps_peak(experiment_id, orbital);
CREATE INDEX IF NOT EXISTS idx_xps_peak_curve_experiment_orbital
    ON xps_peak_curve(experiment_id, orbital);
"""

ALL_SCHEMAS = [
    SHARED_SCHEMA,
    EIS_SCHEMA,
    CYCLE_SCHEMA,
    RATE_SCHEMA,
    CE_GCD_SCHEMA,
    RIC_SCHEMA,
    DQDV_SCHEMA,
    DRT_SCHEMA,
    PROFILES_SCHEMA,
    CV_SCHEMA,
    LSV_SCHEMA,
    GITT_SCHEMA,
    SEM_SCHEMA,
    FTIR_SCHEMA,
    XRD_SCHEMA,
    XPS_SCHEMA,
]


# =========================================================
# SELF-HEALING MIGRATIONS
# =========================================================
# `CREATE TABLE IF NOT EXISTS` does nothing if a table already exists —
# which matters a lot here, because project.db files get carried forward
# across updates to this codebase (copied from an older project, or just
# never deleted). If a table's shape changes (a column gets added), an
# old database's copy of that table is silently left in its old shape,
# and the first INSERT that touches the new column crashes with
# "table X has no column named Y" — no warning beforehand.
#
# MIGRATIONS lists every column ANY released version of this schema has
# added to an EXISTING table after that table's first release. Every
# time you add a column to a table that might already exist in the wild,
# add an entry here too — that's what makes old database files upgrade
# themselves automatically instead of crashing.
MIGRATIONS = {
    "dqdv_point": [
        ("mode", "TEXT"),
        ("idx", "INTEGER"),
    ],
    "xps_peak": [
        ("fraction_pct", "REAL"),
    ],
    "profiles_point": [
        ("mode", "TEXT"),
        ("idx", "INTEGER"),
        ("capacity", "REAL"),
    ],
    # ric_point started out as a generic (experiment_id, x, y) placeholder
    # before RIC was migrated — self-heal any project.db that already
    # created it in that shape.
    "ric_point": [
        ("cycle_number", "INTEGER"),
        ("cc", "REAL"),
        ("cd", "REAL"),
        ("ric", "REAL"),
        ("ric_disconn", "REAL"),
        ("ric_sei", "REAL"),
    ],
    "cv_point": [
        ("scan_rate", "REAL"),
        ("idx", "INTEGER"),
    ],
    "sem_metric": [
        ("role", "TEXT"),
        ("image_path", "TEXT"),
    ],
}


def migrate_schema(conn) -> None:
    """Add any columns tables are missing, per MIGRATIONS. Safe to call every time."""
    cur = conn.cursor()
    for table, columns in MIGRATIONS.items():
        existing = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        if not existing:
            continue  # table doesn't exist yet — CREATE TABLE (already run) makes it in final shape
        for col_name, col_type in columns:
            if col_name not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    conn.commit()


def init_schema(conn) -> None:
    """Create every table (shared + all technique families) if absent, then self-heal any drift."""
    cur = conn.cursor()
    for ddl in ALL_SCHEMAS:
        cur.executescript(ddl)
    conn.commit()
    migrate_schema(conn)
