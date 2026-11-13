"""
Short names, groups and colours for cells, so figures never show raw file names.

datasets.yaml, per dataset:

    labels:                         # glob on the cell name -> legend text; {fields} come from the catalog
      "faraday__lg-INR21700M50*": "LG M50 (21700)"
      "*": "Cell {n}"               # {n} = the number at the end of the name, without leading zeros
    group:                          # optional: one group per cell (colour + legend for large sets)
      from: "TestObject.dateCreated"   # a catalog column or a metadata key (store.metadata())
      regex: "^(\\d+)/(\\d+)/(\\d+)$"   # optional: keep the matched part(s)
      format: "20{3}-{2}-{1}"           # optional: build the label from the groups
      name: Build date                  # legend title

Without labels: the name without its dataset prefix, or "Cell n" for numbered names.
"""
from __future__ import annotations

import fnmatch
import re

import numpy as np
import pandas as pd

# house palette (same as analysis/sheets/plot.py and config.yaml colours)
PALETTE = ["#6B7280", "#E8746A", "#5B9BD5", "#70AD47", "#FFC000", "#7030A0", "#264478", "#9E480E"]


class _Fmt(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _number(name: str):
    m = re.search(r"(\d+)$", name)
    return str(int(m.group(1))) if m else ""


def _default_label(name: str) -> str:
    n = _number(name)
    return f"Cell {n}" if n and re.search(r"(ccid|cell|_)\d+$", name, flags=re.I) else name


def _group_values(spec_group: dict, cells: pd.DataFrame, meta: pd.DataFrame) -> pd.Series:
    col = spec_group["from"]
    if col in cells:
        raw = cells[col]
    elif meta is not None and len(meta) and col in meta:
        raw = cells["cell_id"].map(meta.set_index("cell_id")[col])
    else:
        return pd.Series([""] * len(cells), index=cells.index)
    rx, fmt = spec_group.get("regex"), spec_group.get("format")

    def one(v):
        v = "" if pd.isna(v) else str(v).split(";")[0].strip()
        if rx:
            m = re.search(rx, v)
            if not m:
                return v
            parts = m.groups() or (m.group(0),)
            return fmt.format(*([""] + list(parts))) if fmt else " ".join(parts)
        return v
    return raw.map(one)


def cell_table(store, spec, dataset: str | None = None) -> pd.DataFrame:
    """cell_id, name, label, group, colour — one row per cell (of one dataset or all)."""
    cat = store.catalog()
    if dataset:
        cat = cat[cat["dataset"] == dataset]
    cat = cat.copy()
    cat["name"] = cat["cell_id"].str.split(":", n=1).str[-1]
    out = []
    for ds, cells in cat.groupby("dataset", sort=False):
        d = (spec.get("datasets") or {}).get(ds, {})
        rules = d.get("labels") or {}
        labels = []
        for _, r in cells.iterrows():
            text = next((t for pat, t in rules.items() if fnmatch.fnmatch(r["name"], pat)), None)
            fields = _Fmt({k: v for k, v in r.items() if isinstance(v, str)}, n=_number(r["name"]))
            labels.append(text.format_map(fields) if text else _default_label(r["name"]))
        cells = cells.assign(label=labels)
        g = d.get("group")
        if g:
            meta = store.metadata(ds, varying_only=False) if g["from"] not in cells else None
            cells = cells.assign(group=_group_values(g, cells, meta).to_numpy(), group_name=g.get("name", "Group"))
        else:
            cells = cells.assign(group="", group_name="")
        out.append(cells)
    t = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["cell_id", "label", "group"])
    return assign_colours(t)


def assign_colours(t: pd.DataFrame) -> pd.DataFrame:
    """Up to 8 cells (or groups): house palette. More: an evenly spread colour map."""
    import matplotlib.pyplot as plt
    keys = t["group"] if (t["group"] != "").any() else t["label"]
    uniq = list(dict.fromkeys(keys))
    if len(uniq) <= len(PALETTE):
        cmap = dict(zip(uniq, PALETTE))
    else:
        cols = plt.cm.viridis(np.linspace(0.05, 0.9, len(uniq)))
        from matplotlib.colors import to_hex
        cmap = {k: to_hex(c) for k, c in zip(uniq, cols)}
    return t.assign(colour=keys.map(cmap).to_numpy())
