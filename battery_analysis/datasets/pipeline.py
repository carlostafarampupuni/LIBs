"""
datasets.yaml -> download -> ingest (parallel, incremental) -> store.

    spec = load_spec("datasets.yaml")
    download(spec, ["bdf_examples"], dry_run=True)   # see file names and sizes first
    download(spec, ["bdf_examples"])
    report = ingest(spec, workers=4)                 # unchanged files are skipped
"""
from __future__ import annotations

import fnmatch
import hashlib
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yaml

from .store import DatasetStore

READER_VERSION = "5"     # bump when readers/cleaning change, so everything is re-ingested


# ----------------------------------------------------------------- spec
def load_spec(path, project_dir=None) -> dict:
    path = Path(path).resolve()
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if project_dir is None:
        project_dir = path.parent.parent if path.parent.name == "notebooks" else path.parent
    project_dir = Path(project_dir)
    spec["_project_dir"] = project_dir
    spec["_store"] = (project_dir / spec.get("store", "Data/Public/store")).resolve()
    spec["_raw_root"] = (project_dir / spec.get("raw_root", "Data/Public/raw")).resolve()
    for name, d in (spec.get("datasets") or {}).items():
        d.setdefault("enabled", True)
        src = d.get("source") or {}
        default_raw = f"zenodo_{src['zenodo']}" if "zenodo" in src else name
        d["_raw"] = (spec["_raw_root"] / d.get("raw", default_raw)).resolve()
    return spec


def _selected(spec, names):
    ds = spec.get("datasets") or {}
    if names is None:
        return {k: v for k, v in ds.items() if v.get("enabled", True)}
    unknown = [n for n in names if n not in ds]
    if unknown:
        raise KeyError(f"unknown dataset(s) {unknown}; datasets.yaml has {list(ds)}")
    return {n: ds[n] for n in names}


# ------------------------------------------------------------- download
def _zenodo_files(record: int | str) -> list[dict]:
    import requests
    r = requests.get(f"https://zenodo.org/api/records/{record}", timeout=60)
    r.raise_for_status()
    return [{"key": f["key"], "size": f["size"], "url": f["links"]["self"], "checksum": f.get("checksum", "")}
            for f in r.json()["files"]]


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(spec, names=None, dry_run: bool = False) -> pd.DataFrame:
    """
    Fetch each dataset's source files into its raw folder. Files already there
    with the right checksum are skipped. dry_run=True only lists names and sizes.
    Zip files are extracted next to themselves when source.unzip is true.
    """
    import requests
    from tqdm.auto import tqdm
    rows = []
    for name, d in _selected(spec, names).items():
        src = d.get("source") or {}
        if "zenodo" not in src:
            rows.append({"dataset": name, "file": "-", "status": "no download source (copy files in by hand)"})
            continue
        files = _zenodo_files(src["zenodo"])
        pats = src.get("files")
        if pats:
            files = [f for f in files if any(fnmatch.fnmatch(f["key"], p) for p in pats)]
        raw = d["_raw"]
        raw.mkdir(parents=True, exist_ok=True)
        for f in files:
            dest = raw / f["key"]
            algo, _, want = f["checksum"].partition(":")
            row = {"dataset": name, "file": f["key"], "size_mb": round(f["size"] / 1e6, 2)}
            if dest.exists() and dest.stat().st_size == f["size"] and (algo != "md5" or _md5(dest) == want):
                rows.append({**row, "status": "already here"})
                continue
            if dry_run:
                rows.append({**row, "status": "would download"})
                continue
            tmp = dest.with_suffix(dest.suffix + ".part")
            with requests.get(f["url"], stream=True, timeout=120) as r, open(tmp, "wb") as fh, \
                    tqdm(total=f["size"], unit="B", unit_scale=True, desc=f["key"][:40], leave=False) as bar:
                r.raise_for_status()
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
                    bar.update(len(chunk))
            if algo == "md5" and _md5(tmp) != want:
                tmp.unlink()
                rows.append({**row, "status": "CHECKSUM MISMATCH — deleted, try again"})
                continue
            tmp.replace(dest)
            rows.append({**row, "status": "downloaded"})
        unzip = src.get("unzip")
        if unzip and not dry_run:
            # unzip: true = everything; unzip: ["*.bdf.parquet", ...] = only matching members
            # (the Empa zip is 9.3 GB unpacked, of which the Parquet files are ~1 GB).
            for z in raw.glob("*.zip"):
                with zipfile.ZipFile(z) as zf:
                    members = zf.namelist() if unzip is True else \
                        [m for m in zf.namelist() if any(fnmatch.fnmatch(m, p) for p in unzip)]
                    for m in members:
                        if not (raw / m).exists():
                            zf.extract(m, raw)
    return pd.DataFrame(rows)


# --------------------------------------------------------------- ingest
def _source_files(d) -> list[Path]:
    pats = d.get("files", "*")
    pats = [pats] if isinstance(pats, str) else pats
    found = {p for pat in pats for p in d["_raw"].glob(pat) if p.is_file()}
    return sorted(found)


def _meta_for(d, path: Path) -> dict:
    meta = dict(d.get("meta") or {})
    for pat, extra in (d.get("meta_by_pattern") or {}).items():
        if fnmatch.fnmatch(path.name, pat):
            meta.update(extra)
    return meta


def _process_file(job: dict) -> dict:
    """Runs in a worker process: read one source file, write its cells, return small rows."""
    from .cycles import summarize
    from .readers import get_reader
    from .schema import clean, normalize, validate_eis
    store = DatasetStore(job["store"])
    t0 = time.perf_counter()
    stage = {"t_read": 0.0, "t_clean": 0.0, "t_cycles": 0.0, "t_write": 0.0}
    out = {"catalog": [], "manifest": {**job["manifest"], "status": "ok", "error": "", "cells": "", "n_rows": 0}}
    try:
        t = time.perf_counter()
        cells = get_reader(job["reader"])(Path(job["path"]), job["options"])
        stage["t_read"] += time.perf_counter() - t
        for c in cells:
            cell_id = f"{job['dataset']}:{c.cell_id}"
            meta = {**c.meta, **job["meta"]}                       # datasets.yaml wins over guesses
            row = {"cell_id": cell_id, "dataset": job["dataset"], "source": job["manifest"]["source"],
                   "reader": job["reader"], **{k: str(v) for k, v in meta.items()}}
            if c.timeseries is not None:
                t = time.perf_counter()
                ts, issues = clean(normalize(c.timeseries))
                stage["t_clean"] += time.perf_counter() - t
                t = time.perf_counter()
                cyc = summarize(ts)
                stage["t_cycles"] += time.perf_counter() - t
                t = time.perf_counter()
                store.write_table("timeseries", job["dataset"], cell_id, ts)
                store.write_table("cycles", job["dataset"], cell_id, cyc)
                stage["t_write"] += time.perf_counter() - t
                out["manifest"]["n_rows"] += len(ts)
                row.update(n_rows=len(ts), n_cycles=len(cyc), duration_h=float(ts["test_time_second"].iloc[-1]
                           - ts["test_time_second"].iloc[0]) / 3600 if len(ts) else 0.0,
                           columns=",".join(ts.columns), issues=" | ".join(issues), kind="timeseries")
            if c.eis is not None:
                issues = validate_eis(c.eis)
                t = time.perf_counter()
                store.write_table("eis", job["dataset"], cell_id, c.eis)
                stage["t_write"] += time.perf_counter() - t
                out["manifest"]["n_rows"] += len(c.eis)
                row.update(n_rows=len(c.eis), n_spectra=int(c.eis["cycle"].nunique()) if "cycle" in c.eis else 1,
                           issues=" | ".join(issues), kind="eis" if c.timeseries is None else "timeseries+eis")
            if c.extra:
                store.write_table("metadata", job["dataset"], cell_id, pd.DataFrame(
                    {"key": list(c.extra), "value": [str(v) for v in c.extra.values()]}))
            out["catalog"].append(row)
        out["manifest"]["cells"] = ",".join(r["cell_id"] for r in out["catalog"])
    except Exception as err:                                      # one bad file never stops the run
        out["manifest"].update(status="error", error=f"{type(err).__name__}: {err}")
    out["manifest"].update({k: round(v, 4) for k, v in stage.items()})
    out["manifest"]["seconds"] = round(time.perf_counter() - t0, 4)
    return out


def ingest(spec, names=None, workers: int | None = None, force: bool = False) -> pd.DataFrame:
    """
    Read every source file of the selected datasets into the store.
    A file is skipped when its size, modification time and reader are unchanged
    since the last successful ingest (force=True redoes everything).
    Returns one row per source file: status, cells written, seconds, error.
    """
    store = DatasetStore(spec["_store"])
    manifest = store.manifest()
    done = {}
    if len(manifest):
        ok = manifest[manifest["status"] == "ok"]
        done = {r.source: (r.size, r.mtime, r.reader, r.reader_version) for r in ok.itertuples()}
    jobs, skipped = [], []
    for name, d in _selected(spec, names).items():
        files = _source_files(d)
        if not files:
            skipped.append({"dataset": name, "source": str(d["_raw"]), "status": "no files — run download() first"})
        for p in files:
            st = p.stat()
            source = str(p.relative_to(spec["_project_dir"]))
            key = (st.st_size, st.st_mtime, d["reader"], READER_VERSION)
            if not force and done.get(source) == key:
                skipped.append({"dataset": name, "source": source, "status": "unchanged, skipped"})
                continue
            jobs.append({"store": str(spec["_store"]), "dataset": name, "path": str(p), "reader": d["reader"],
                         "options": d.get("options") or {}, "meta": _meta_for(d, p),
                         "manifest": {"source": source, "dataset": name, "size": st.st_size, "mtime": st.st_mtime,
                                      "reader": d["reader"], "reader_version": READER_VERSION}})
    results = []
    workers = workers if workers is not None else spec.get("workers", 4)
    if workers and workers > 1 and len(jobs) > 1:
        from tqdm.auto import tqdm
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_process_file, j) for j in jobs]
            for f in tqdm(as_completed(futs), total=len(futs), desc="ingest"):
                results.append(f.result())
    else:
        results = [_process_file(j) for j in jobs]

    cat_rows = [r for res in results for r in res["catalog"]]
    if cat_rows:
        store.upsert_catalog(pd.DataFrame(cat_rows))
    if results:
        store.upsert_manifest(pd.DataFrame([res["manifest"] for res in results]))
    report = pd.DataFrame([res["manifest"] for res in results] + skipped)
    if len(report):
        n_err = int((report["status"] == "error").sum())
        print(f"ingest: {len(results)} file(s) read, {len(skipped)} skipped, {n_err} error(s) -> {spec['_store']}")
    return report
