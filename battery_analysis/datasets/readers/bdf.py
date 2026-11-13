"""
Battery Data Format files (.bdf.csv, .bdf.parquet): already in the standard,
so this only reads them. Files named the BDF way,
    <institution>__<cell>__<date>__<test>__<temperature>__<cycler>.bdf.csv
get those fields as metadata.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import CellData, infer_form_factor, register


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    import pyarrow.csv as pacsv
    return pacsv.read_csv(path).to_pandas()        # multi-threaded, ~10x faster than pd.read_csv on big files


def _meta_from_name(stem: str) -> dict:
    parts = stem.split("__")
    meta = {"source_name": stem}
    if len(parts) >= 4:
        meta.update(institution=parts[0], cell_model=parts[1], test_date=parts[2], test_type=parts[3])
        rest = parts[4:]
        temp = next((p for p in rest if p.lower().endswith("degc")), None)
        if temp:
            meta["test_temperature"] = temp
        if rest and rest[-1] != temp:
            meta["cycler"] = rest[-1]
    meta["form_factor"] = infer_form_factor(parts[1] if len(parts) > 1 else stem)
    return meta


_SKIP = {"@id", "@context", "rdfs:comment", "schema:url", "schema:address"}


def flatten_jsonld(node, prefix="", out=None) -> dict:
    """
    BattINFO JSON-LD -> {"PositiveElectrode.RatedCapacity [MilliAmpereHourPerSquareCentiMetre]": 1.1, ...}.
    A measured property (number + unit) becomes one key named after its @type and unit;
    'has' prefixes and namespaces are dropped from the path.
    """
    out = {} if out is None else out
    if isinstance(node, list):
        for k, v in enumerate(node):
            flatten_jsonld(v, f"{prefix}[{k}]" if any(isinstance(x, (dict, list)) for x in node) else prefix, out)
        return out
    if not isinstance(node, dict):
        out[prefix] = node if prefix not in out else f"{out[prefix]}; {node}"
        return out
    typ = node.get("@type")
    typ = typ[-1] if isinstance(typ, list) else typ
    if "hasNumericalPart" in node:
        unit = str(node.get("hasMeasurementUnit", "")).split(":")[-1]
        val = node["hasNumericalPart"].get("hasNumberValue") if isinstance(node["hasNumericalPart"], dict) else None
        out[f"{prefix.rsplit('[', 1)[0]}.{typ} [{unit}]".strip(".")] = val
        return out
    for k, v in node.items():
        if k in _SKIP or k == "@type":
            continue
        name = k.split(":")[-1]
        name = name[3:] if name.startswith("has") and len(name) > 3 else name
        if name == "MeasuredProperty":
            flatten_jsonld(v, prefix, out)
        else:
            flatten_jsonld(v, f"{prefix}.{name}".strip("."), out)
    if typ and not any(k.startswith(prefix) and k != prefix for k in out):
        out[f"{prefix}.type".strip(".")] = typ
    return out


@register("bdf")
def read(path: Path, options: dict) -> list[CellData]:
    stem = path.name.split(".bdf")[0]
    extra = {}
    sidecar = path.with_name(f"{stem}.metadata.json")
    if sidecar.exists():                                  # e.g. Empa/BattINFO JSON-LD per cell
        import json
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        graph = doc.get("@graph", [doc])
        extra = flatten_jsonld(graph[0] if len(graph) == 1 else graph)
    return [CellData(cell_id=stem, meta=_meta_from_name(stem), timeseries=_read_table(path), extra=extra)]
