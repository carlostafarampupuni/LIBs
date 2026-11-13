"""
Metrohm NOVA (.nox + NOVA Excel exports) -> this pipeline's raw-file formats.

    from battery_analysis.ingest import ingest
    ingest("..")                      # from a notebook in notebooks/

    python -m battery_analysis.ingest # from Results/

See ingest.py for the input layout, the output files, and every assumption.
"""
from .ingest import ingest
from .nox import read_nox
from .nova_xlsx import read_nova_xlsx

__all__ = ["ingest", "read_nox", "read_nova_xlsx"]
