import argparse

from .ingest import ingest

ap = argparse.ArgumentParser(prog="python -m battery_analysis.ingest",
                             description="Convert NOVA .nox/.xlsx files under Data/ into the pipeline's formats.")
ap.add_argument("project_dir", nargs="?", default=".", help="the Results/ folder (holds config.yaml)")
ingest(ap.parse_args().project_dir)
