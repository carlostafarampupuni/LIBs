"""
How fast is the pipeline on large data? Measured, not guessed.

    from battery_analysis.datasets.benchmark import run, plot_benchmark
    bench = run(spec, "empa_aurora_full", cells=[10, 25, 50, 100, 199], workers=[1, 2, 4, 6])
    plot_benchmark(bench, spec)

run() ingests the first N files of a dataset into a throw-away store for every
(N, workers) pair and records wall time, rows and the time spent in each stage
(read, clean, cycle summary, write: summed over all files, i.e. CPU-side work).
It then times the downstream steps on the full real store: loading every cycle
table, the feature table and one many-cell figure.

Results: Exports/Benchmark/benchmark_<dataset>_<date>.csv plus machine.json
(CPU, cores, memory, library versions). Raw files are read from disk each run;
after the first run they sit in the operating system's file cache, so these are
warm-cache times (state that in a paper).
"""
from __future__ import annotations

import copy
import json
import os
import platform
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .pipeline import _source_files, ingest
from .store import DatasetStore


def machine_info() -> dict:
    def sh(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""
    import pyarrow
    info = {
        "date": date.today().isoformat(), "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(), "cpu": sh(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor(),
        "logical_cores": os.cpu_count(), "python": platform.python_version(),
        "pandas": pd.__version__, "numpy": np.__version__, "pyarrow": pyarrow.__version__,
    }
    mem = sh(["sysctl", "-n", "hw.memsize"])
    if mem.isdigit():
        info["memory_gb"] = round(int(mem) / 2**30, 1)
    return info


def _bench_spec(spec, dataset, files, store_dir):
    s = copy.deepcopy({k: v for k, v in spec.items() if not k.startswith("_")})
    s.update({k: v for k, v in spec.items() if k.startswith("_")})
    s["_store"] = store_dir
    d = copy.deepcopy(spec["datasets"][dataset])
    d["_raw"] = spec["datasets"][dataset]["_raw"]
    d["files"] = [str(f.relative_to(d["_raw"])) for f in files]
    d["enabled"] = True
    s["datasets"] = {dataset: d}
    return s


def run(spec, dataset: str, cells=(10, 25, 50, 100, None), workers=(1, 2, 4, 6), repeats: int = 1,
        out_dir=None) -> pd.DataFrame:
    """None in `cells` = all files of the dataset. Returns one row per (cells, workers, repeat)."""
    files = _source_files(spec["datasets"][dataset])
    tmp = Path(spec["_store"]).parent / "_benchmark_store"
    rows = []
    for n in cells:
        sub = files[: n] if n else files
        for w in workers:
            for r in range(repeats):
                shutil.rmtree(tmp, ignore_errors=True)
                s = _bench_spec(spec, dataset, sub, tmp)
                t0 = time.perf_counter()
                rep = ingest(s, workers=w, force=True)
                wall = time.perf_counter() - t0
                ok = rep[rep["status"] == "ok"]
                rows.append({"stage": "ingest", "cells": len(sub), "workers": w, "repeat": r,
                             "wall_s": wall, "rows": int(ok["n_rows"].sum()), "errors": int((rep["status"] == "error").sum()),
                             **{k: float(ok[k].sum()) for k in ("t_read", "t_clean", "t_cycles", "t_write")}})
                print(f"ingest {len(sub):4d} cells, {w} worker(s): {wall:6.1f} s  ({rows[-1]['rows'] / wall / 1e6:.2f} M rows/s)")
    shutil.rmtree(tmp, ignore_errors=True)

    # downstream steps on the real (full) store
    from .features import feature_table
    from .plot import plot_figures
    store = DatasetStore(spec["_store"])
    n_all = int((store.catalog()["dataset"] == dataset).sum())
    for name, fn in [("load_cycles", lambda: store.cycles(dataset)),
                     ("features", lambda: feature_table(store, dataset)),
                     ("figure_all_cells", lambda: plot_figures(store, {**spec, "figures": {"_bench": {
                         "kind": "capacity", "dataset": dataset, "size": "large", "legend": False}}}))]:
        t0 = time.perf_counter()
        fn()
        rows.append({"stage": name, "cells": n_all, "workers": 1, "repeat": 0, "wall_s": time.perf_counter() - t0})
    bench_fig = Path(spec["_project_dir"]) / spec.get("figures_root", "Figures") / "Datasets" / dataset / f"_bench_{dataset}.svg"
    bench_fig.unlink(missing_ok=True)

    df = pd.DataFrame(rows)
    out_dir = Path(out_dir or Path(spec["_project_dir"]) / spec.get("exports_root", "Exports") / "Benchmark")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    df.to_csv(out_dir / f"benchmark_{dataset}_{stamp}.csv", index=False)
    (out_dir / f"machine_{stamp}.json").write_text(json.dumps(machine_info(), indent=2))
    print(f"saved -> {out_dir}")
    return df


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """One line per (cells, workers): wall time, million rows per second, speed-up vs 1 worker."""
    ing = df[df["stage"] == "ingest"].groupby(["cells", "workers"], as_index=False).agg(
        wall_s=("wall_s", "median"), rows=("rows", "max"))
    ing["M_rows_per_s"] = ing["rows"] / ing["wall_s"] / 1e6
    base = ing[ing["workers"] == 1].set_index("cells")["wall_s"]
    ing["speedup"] = ing["cells"].map(base) / ing["wall_s"]
    return ing


def plot_benchmark(df: pd.DataFrame, spec, dataset: str = "benchmark") -> list:
    """Three house-style figures: time vs cells, speed-up vs workers, where the time goes."""
    import matplotlib.pyplot as plt
    from battery_analysis.plotting.axes import set_axis
    from battery_analysis.plotting.plotdata import register_project
    from battery_analysis.plotting.style import SCATTER, apply_style, fig_sizes, legend_style
    from .labels import PALETTE
    from .plot import _save

    proj = Path(spec["_project_dir"])
    figures_root = proj / spec.get("figures_root", "Figures")
    register_project(figures_root, proj / spec.get("exports_root", "Exports"))
    out_root = figures_root / "Datasets"
    s = summary(df)
    paths = []
    apply_style()

    # (a) wall time vs number of cells, one series per worker count
    fig, ax = plt.subplots(figsize=fig_sizes["medium"])
    for c, (w, g) in zip(PALETTE[1:], s.groupby("workers")):
        ax.plot(g["cells"], g["wall_s"], color=c, lw=0.8, zorder=1)
        ax.scatter(g["cells"], g["wall_s"], s=SCATTER["marker_size"] * 0.6, color=c, zorder=2,
                   edgecolors=SCATTER["solid_edge_color"], linewidth=SCATTER["solid_edge_width"],
                   label=f"{w} worker" + ("s" if w > 1 else ""))
    ax.set_xlabel("Cells ingested"); ax.set_ylabel("Ingest time (s)")
    set_axis(ax, "x", 0, s["cells"].max()); set_axis(ax, "y", 0, s["wall_s"].max())
    top = s.groupby("cells")["rows"].max()
    sec = ax.secondary_xaxis("top", functions=(lambda x: np.interp(x, top.index, top.values / 1e6),
                                                lambda y: np.interp(y, top.values / 1e6, top.index)))
    sec.set_xlabel("Rows (millions)")
    ax.legend(loc="upper left", **legend_style)
    paths.append(_save(fig, out_root, "benchmark_time_vs_cells", dataset))

    # (b) speed-up vs workers on the largest set, with ideal linear scaling
    big = s[s["cells"] == s["cells"].max()].sort_values("workers")
    fig, ax = plt.subplots(figsize=fig_sizes["small"])
    ax.plot(big["workers"], big["workers"], color="black", lw=0.6, ls="--", label="Ideal")
    ax.plot(big["workers"], big["speedup"], color=PALETTE[1], lw=0.8)
    ax.scatter(big["workers"], big["speedup"], s=SCATTER["marker_size"] * 0.6, color=PALETTE[1], zorder=2,
               edgecolors=SCATTER["solid_edge_color"], linewidth=SCATTER["solid_edge_width"], label="Measured")
    ax.set_xlabel("Workers"); ax.set_ylabel("Speed-up")
    wmax = int(big["workers"].max())
    ticks = list(range(0, wmax + 1, 2))
    set_axis(ax, "x", lim=(0, wmax + 0.5), ticks=ticks); set_axis(ax, "y", lim=(0, wmax + 0.5), ticks=ticks)
    ax.legend(loc="upper left", **legend_style)
    paths.append(_save(fig, out_root, "benchmark_speedup", dataset))

    # (c) where the time goes (one worker, largest set): share of each stage
    one = df[(df["stage"] == "ingest") & (df["workers"] == 1) & (df["cells"] == df["cells"].max())].iloc[0]
    stages = {"Read": one["t_read"], "Clean": one["t_clean"], "Cycle summary": one["t_cycles"], "Write Parquet": one["t_write"]}
    total = sum(stages.values())
    fig, ax = plt.subplots(figsize=(fig_sizes["medium"][0], 0.75))
    left = 0.0
    for c, (k, v) in zip(PALETTE, stages.items()):
        ax.barh([0], [v / total * 100], left=left, color=c, edgecolor="black", lw=0.5, height=0.5,
                label=f"{k} ({v:.0f} s)")
        left += v / total * 100
    ax.set_yticks([]); ax.set_xlabel("Share of processing time (%)")
    set_axis(ax, "x", lim=(0, 100), step=20)
    ax.set_ylim(-0.4, 0.4)
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.15), borderaxespad=0.0, **legend_style)
    paths.append(_save(fig, out_root, "benchmark_stages", dataset))
    return paths
