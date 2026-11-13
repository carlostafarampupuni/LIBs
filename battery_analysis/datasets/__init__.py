"""
Large-dataset layer: many cells, any format, any cell type (coin, pouch, 18650,
21700, prismatic), stored as Parquet so hundreds of cells stay fast.

    datasets.yaml ──download.py──> Data/Public/raw/<dataset>/      (untouched originals)
                  ──pipeline.py──> readers/<format>.py              (one function per file format)
                                   schema.py                        (one column standard: BDF)
                                   store.py ──> Data/Public/store/  (Parquet, one file per cell)
                                   cycles.py                        (time series -> one row per cycle)
                                   features.py                      (one row per cell: fade, EOL, ...)
                                   plot.py                          (overview figures)

The column standard is the Battery Data Format (BDF) of the Battery Data
Alliance: test_time_second, voltage_volt, current_ampere, cycle_count, ...
Every reader converts to it, so everything after the reader works on any
cycler's data. Current sign: positive = charge, negative = discharge.

The coin-cell technique modules (analysis/gcd, eis, ...) are unchanged;
readers/neware.py brings your own NEWARE files into this store as well.
"""
from .store import DatasetStore  # noqa: F401
from .pipeline import load_spec, ingest, download  # noqa: F401
