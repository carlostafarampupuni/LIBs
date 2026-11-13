"""
The Parquet store. One folder, readable by pandas, polars, DuckDB, Origin
(via CSV export) — no database server.

    store/
      catalog.parquet                  one row per cell: dataset, form factor, chemistry, rows, issues, ...
      manifest.parquet                 one row per source file: size, mtime, reader, status, error
      timeseries/<dataset>/<cell>.parquet   the cleaned BDF time series
      cycles/<dataset>/<cell>.parquet       one row per cycle (cycles.summarize)
      eis/<dataset>/<cell>.parquet          spectra: cycle, frequency_hertz, z_real_ohm, z_imag_ohm, ...

Per-cell files mean a 500-cell dataset never has to fit in memory at once,
a crashed ingest loses one cell, not all, and re-running only touches cells
whose source file changed (manifest).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

COMPRESSION = "zstd"


def safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_")[:150]


class DatasetStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- paths ----------
    def _path(self, kind: str, dataset: str, cell_id: str) -> Path:
        return self.root / kind / safe_id(dataset) / f"{safe_id(cell_id)}.parquet"

    # ---------- writing (safe to call from worker processes) ----------
    def write_table(self, kind: str, dataset: str, cell_id: str, df: pd.DataFrame) -> Path:
        path = self._path(kind, dataset, cell_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp, compression=COMPRESSION)
        tmp.replace(path)                       # atomic: a half-written file never looks finished
        return path

    def _upsert(self, name: str, rows: pd.DataFrame, key: str) -> None:
        path = self.root / name
        old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        if len(old) and key in old:
            old = old[~old[key].isin(rows[key])]
        new = pd.concat([old, rows], ignore_index=True) if len(old) else rows
        pq.write_table(pa.Table.from_pandas(new.astype({c: "string" for c in new.columns
                                                        if new[c].dtype == object}),
                                            preserve_index=False), path, compression=COMPRESSION)

    def upsert_catalog(self, rows: pd.DataFrame) -> None:
        self._upsert("catalog.parquet", rows, "cell_id")

    def upsert_manifest(self, rows: pd.DataFrame) -> None:
        self._upsert("manifest.parquet", rows, "source")

    def remove_dataset(self, dataset: str) -> None:
        import shutil
        for kind in ("timeseries", "cycles", "eis", "metadata"):
            shutil.rmtree(self.root / kind / safe_id(dataset), ignore_errors=True)
        for name, col in (("catalog.parquet", "dataset"), ("manifest.parquet", "dataset")):
            p = self.root / name
            if p.exists():
                df = pd.read_parquet(p)
                df[df[col] != dataset].to_parquet(p, index=False)

    # ---------- reading ----------
    def catalog(self) -> pd.DataFrame:
        p = self.root / "catalog.parquet"
        return pd.read_parquet(p) if p.exists() else pd.DataFrame()

    def manifest(self) -> pd.DataFrame:
        p = self.root / "manifest.parquet"
        return pd.read_parquet(p) if p.exists() else pd.DataFrame()

    def _dataset_of(self, cell_id: str) -> str:
        cat = self.catalog()
        hit = cat.loc[cat["cell_id"] == cell_id, "dataset"] if len(cat) else []
        if not len(hit):
            raise KeyError(f"unknown cell {cell_id!r}")
        return hit.iloc[0]

    def timeseries(self, cell_id: str, columns=None) -> pd.DataFrame:
        return pd.read_parquet(self._path("timeseries", self._dataset_of(cell_id), cell_id), columns=columns)

    def _concat(self, kind: str, dataset: str | None) -> pd.DataFrame:
        base = self.root / kind
        folders = [base / safe_id(dataset)] if dataset else sorted(p for p in base.glob("*") if p.is_dir())
        files = [f for d in folders for f in sorted(d.glob("*.parquet"))]
        if not files:
            return pd.DataFrame()
        cat = self.catalog()
        name_of = {safe_id(c): c for c in cat["cell_id"]} if len(cat) else {}
        parts = []
        for f in files:
            df = pd.read_parquet(f)
            df.insert(0, "cell_id", name_of.get(f.stem, f.stem))
            df.insert(0, "dataset", f.parent.name)
            parts.append(df)
        return pd.concat(parts, ignore_index=True)

    def cycles(self, dataset: str | None = None) -> pd.DataFrame:
        return self._concat("cycles", dataset)

    def eis(self, dataset: str | None = None) -> pd.DataFrame:
        return self._concat("eis", dataset)

    def metadata(self, dataset: str | None = None, varying_only: bool = True) -> pd.DataFrame:
        """
        Detailed per-cell metadata (from sidecar files) as one wide table, one row per cell.
        varying_only=True keeps only keys whose value differs between cells, i.e. the
        build/test conditions you can compare against performance.
        """
        long = self._concat("metadata", dataset)
        if long.empty:
            return long
        wide = long.pivot_table(index="cell_id", columns="key", values="value", aggfunc="first")
        if varying_only:
            wide = wide.loc[:, wide.nunique(dropna=False) > 1]
        num = wide.apply(pd.to_numeric, errors="coerce")
        wide = wide.where(num.isna(), num)                 # numbers back to numbers
        return wide.reset_index()

    def summary(self) -> pd.DataFrame:
        cat = self.catalog()
        if cat.empty:
            return cat
        return cat.groupby("dataset").agg(cells=("cell_id", "size"), rows=("n_rows", "sum"),
                                         form_factors=("form_factor", lambda s: ", ".join(sorted(set(map(str, s))))))
