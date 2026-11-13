"""
Native reader for Metrohm Autolab NOVA ``.nox`` project files.

A .nox is not text. It is several .NET ``BinaryFormatter`` object streams
written back to back (the first a small procedure header, the second the whole
procedure *plus the measured data*). This module decodes that format with no
third-party dependency (numpy only) and pulls the measured signals out of the
named command parameters NOVA stores them in, the same way a BioLogic .mpt
parser reads named columns:

  EIS (FRA impedance)   FIAMeasurement      one per frequency: Frequency, H_Real (Z'),
                                            H_Imaginary (NOVA's "-Z''"), H_Modulus, H_Phase,
                                            Time, DC potential/current
  CV                    FHCyclicVoltammetry2 one per scan rate: Scanrate (V/s), SetpointApplied,
                                            WE(1).Potential, WE(1).Current, Time, ScanNumber
  charge-discharge      FHLevel              one per step: Time, WE(1).Potential, WE(1).Current,
                                            Setpoint value (A)

Nothing is guessed: scan rates, frequencies and currents come from the file.

    from battery_analysis.ingest.nox import read_nox
    nf = read_nox("cell.nox")
    nf.technique            # "eis" | "cv" | "gcd" | "mixed" | "empty"
    nf.eis.freq, nf.eis.zre, nf.eis.zim          # zim = signed Im(Z), negative for capacitive behaviour
    nf.cv[i].scan_rate_mV_s, .e_applied, .current, .cycle
    nf.gcd[i].kind, .time, .potential, .current  # kind: rest | charge | discharge
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import numpy as np


class NoxFormatError(ValueError):
    """The file is not a readable NOVA .nox (or is truncated/corrupt)."""


# ===========================================================================
# 1. .NET BinaryFormatter (MS-NRBF) decoder
# ===========================================================================

_PRIM = {1: ("?", 1), 2: ("B", 1), 6: ("d", 8), 7: ("h", 2), 8: ("i", 4), 9: ("q", 8), 10: ("b", 1),
         11: ("f", 4), 14: ("H", 2), 15: ("I", 4), 16: ("Q", 8)}
_NP = {1: "?", 2: "u1", 6: "<f8", 7: "<i2", 8: "<i4", 9: "<i8", 10: "i1", 11: "<f4", 14: "<u2", 15: "<u4", 16: "<u8"}
_STREAM_START = b"\x00\x01\x00\x00\x00\xff\xff\xff\xff\x01\x00\x00\x00\x00\x00\x00\x00"


@dataclass
class _Obj:
    name: str
    members: dict = field(default_factory=dict)
    id: int = -1


@dataclass
class _Ref:
    id: int


class _Reader:
    def __init__(self, data: bytes, start: int = 0):
        self.b, self.p = data, start
        self.objs: dict = {}
        self.classes: dict = {}

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]
        self.p += 4
        return v

    def string(self):
        n = shift = 0
        while True:
            c = self.u8()
            n |= (c & 0x7F) << shift
            if not c & 0x80:
                break
            shift += 7
        s = self.b[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return s

    def prim(self, t):
        if t == 18:
            return self.string()
        if t == 3:
            c = self.u8()
            n = 1 if c < 0x80 else 2 if c < 0xE0 else 3 if c < 0xF0 else 4
            s = self.b[self.p - 1:self.p - 1 + n].decode("utf-8", "replace")
            self.p += n - 1
            return s
        if t == 5:
            return float(self.string())
        if t in (12, 13):
            v = struct.unpack_from("<q", self.b, self.p)[0]
            self.p += 8
            return v
        if t == 17:
            return None
        f, n = _PRIM[t]
        v = struct.unpack_from("<" + f, self.b, self.p)[0]
        self.p += n
        return v

    def type_info(self, count):
        types = [self.u8() for _ in range(count)]
        infos = []
        for t in types:
            if t in (0, 7):
                infos.append(self.u8())
            elif t == 3:
                infos.append(self.string())
            elif t == 4:
                infos.append((self.string(), self.i32()))
            else:
                infos.append(None)
        return types, infos

    def members(self, names, types, infos):
        return {n: (self.prim(i) if t == 0 else self.record()) for n, t, i in zip(names, types, infos)}

    def record(self):
        rt = self.u8()
        if rt == 6:
            oid = self.i32()
            s = self.string()
            self.objs[oid] = s
            return s
        if rt == 9:
            return _Ref(self.i32())
        if rt == 10:
            return None
        if rt == 13:
            return ("null", self.u8())
        if rt == 14:
            return ("null", self.i32())
        if rt == 8:
            return self.prim(self.u8())
        if rt in (2, 3, 4, 5):
            oid, name, n = self.i32(), self.string(), None
            n = self.i32()
            names = [self.string() for _ in range(n)]
            types, infos = self.type_info(n) if rt in (4, 5) else ([2] * n, [None] * n)
            if rt in (3, 5):
                self.i32()
            self.classes[oid] = (name, names, types, infos)
            o = _Obj(name, {}, oid)
            self.objs[oid] = o
            o.members = self.members(names, types, infos)
            return o
        if rt == 1:
            oid, meta = self.i32(), self.i32()
            name, names, types, infos = self.classes[meta]
            o = _Obj(name, {}, oid)
            self.objs[oid] = o
            o.members = self.members(names, types, infos)
            return o
        if rt == 12:
            self.i32()
            self.string()
            return self.record()
        if rt == 15:
            oid, n, t = self.i32(), self.i32(), self.u8()
            if t in _NP:
                arr = np.frombuffer(self.b, dtype=_NP[t], count=n, offset=self.p).copy()
                self.p += arr.itemsize * n
            else:
                arr = [self.prim(t) for _ in range(n)]
            self.objs[oid] = arr
            return arr
        if rt in (16, 17):
            oid, n = self.i32(), self.i32()
            items = self._read_n(n)
            self.objs[oid] = items
            return items
        if rt == 7:
            oid, kind, rank = self.i32(), self.u8(), self.i32()
            lens = [self.i32() for _ in range(rank)]
            if kind >= 3:
                for _ in range(rank):
                    self.i32()
            t = self.u8()
            inf = (self.u8() if t in (0, 7) else self.string() if t == 3
                   else (self.string(), self.i32()) if t == 4 else None)
            total = int(np.prod(lens))
            if t == 0 and inf in _NP:
                arr = np.frombuffer(self.b, dtype=_NP[inf], count=total, offset=self.p).copy()
                self.p += arr.itemsize * total
                arr = arr.reshape(lens)
            elif t == 0:
                arr = [self.prim(inf) for _ in range(total)]
            else:
                arr = self._read_n(total)
            self.objs[oid] = arr
            return arr
        if rt == 11:
            return "END"
        raise NoxFormatError(f"unsupported record type {rt} at byte {self.p - 1}")

    def _read_n(self, n):
        out = []
        while len(out) < n:
            r = self.record()
            if isinstance(r, tuple) and r and r[0] == "null":
                out.extend([None] * r[1])
            else:
                out.append(r)
        return out

    def parse(self):
        if self.u8() != 0:
            raise NoxFormatError("not a .NET BinaryFormatter stream")
        for _ in range(4):
            self.i32()
        root = None
        while self.p < len(self.b):
            r = self.record()
            if isinstance(r, str) and r == "END":
                break
            if root is None:
                root = r
        return root


def _streams(data: bytes):
    out, p = [], 0
    while p < len(data):
        if not data.startswith(_STREAM_START, p):
            break
        r = _Reader(data, p)
        root = r.parse()
        out.append((root, r))
        p = r.p
    return out


# ===========================================================================
# 2. Pulling named signals out of command parameters
# ===========================================================================

def _res(r, x):
    return r.objs.get(x.id) if isinstance(x, _Ref) else x


def _value(r, v):
    """ParameterObject._value -> numpy array (List<T> trimmed to _size) / scalar / None."""
    v = _res(r, v)
    if isinstance(v, _Obj):
        if "_items" in v.members:
            items = _res(r, v.members["_items"])
            n = v.members.get("_size", len(items))
            if isinstance(items, np.ndarray):
                return items[:n]
            return [_res(r, x) for x in items][:n]
        if "_value" in v.members:
            return _value(r, v.members["_value"])
        return None
    return v


def _collections(r):
    """Yield {name: value} + handler for every CommandParameters13 object."""
    for o in list(r.objs.values()):
        if not (isinstance(o, _Obj) and o.name.startswith("EcoChemie.Utils.Sequencer.CommandParameters13")):
            continue
        try:
            items = _res(r, _res(r, o.members["CollectionBase+list"]).members["_items"])
        except (KeyError, AttributeError):
            continue
        params, handler = {}, None
        for it in items:
            it = _res(r, it)
            if not isinstance(it, _Obj):
                continue
            g = it.members.get
            name = _res(r, g("_name", g("CommandParameter+_name")))
            po = _res(r, g("_parameter", g("CommandParameter+_parameter")))
            if name is None or not isinstance(po, _Obj):
                continue
            params[name] = _value(r, po.members.get("_value"))
            if handler is None:
                par = _res(r, g("_parent", g("CommandParameter+_parent")))
                handler = par.name.split(",")[0].split(".")[-1] if isinstance(par, _Obj) else None
        yield handler, params


def _arr(x, dtype=float):
    return np.asarray(x, dtype=dtype) if x is not None else None


def _has(x):
    return isinstance(x, (np.ndarray, list)) and len(x) > 0


def _scalar(x):
    if isinstance(x, (np.ndarray, list)):
        return float(x[0]) if len(x) else None
    return float(x) if isinstance(x, (int, float, np.floating, np.integer)) and not isinstance(x, bool) else None


# ===========================================================================
# 3. Data classes
# ===========================================================================

@dataclass
class EISData:
    freq: np.ndarray                  # Hz, in acquisition order (high -> low)
    zre: np.ndarray                   # Z'  (ohm)
    zim: np.ndarray                   # signed Im(Z) (ohm): negative for capacitive behaviour
    modulus: np.ndarray               # |Z| (ohm)
    phase_deg: np.ndarray             # NOVA "-Phase" (degrees)
    time_s: np.ndarray
    e_dc: np.ndarray                  # DC potential during the measurement (V)
    i_dc: np.ndarray                  # DC current (A)

    @property
    def minus_zim(self):              # NOVA's "-Z''" (positive for capacitive behaviour)
        return -self.zim


@dataclass
class CVScan:
    scan_rate_V_s: float
    time: Optional[np.ndarray]        # s (None when read from a NOVA Excel export: no time column)
    e_applied: np.ndarray             # V, "Potential applied"
    e_measured: Optional[np.ndarray]  # V, WE(1).Potential (None from an Excel export)
    current: np.ndarray               # A, WE(1).Current
    cycle: np.ndarray                 # NOVA "Scan" number per point (1, 2, ...)

    @property
    def scan_rate_mV_s(self):
        return self.scan_rate_V_s * 1000.0


@dataclass
class GCStep:
    kind: str                         # rest | charge | discharge
    time: np.ndarray                  # s
    potential: np.ndarray             # V, WE(1).Potential
    current: Optional[np.ndarray]     # A, WE(1).Current (measured; None from an Excel export)
    cycle: int = 0                    # 1.. (charge + the discharge after it); 0 for rest

    @property
    def current_A(self) -> float:     # the applied current, from the measurement itself
        return float(np.median(self.current)) if self.current is not None else float("nan")


@dataclass
class NoxFile:
    path: str
    procedure_name: str = ""
    procedure_text: str = ""          # e.g. "FRA impedance potentiostatic"
    instrument: str = ""
    recorded: Optional[datetime] = None
    eis: Optional[EISData] = None
    cv: List[CVScan] = field(default_factory=list)
    gcd: List[GCStep] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def technique(self) -> str:
        has = (("eis", self.eis is not None and len(self.eis.freq) > 0), ("cv", len(self.cv) > 0), ("gcd", len(self.gcd) > 0))
        kinds = [k for k, ok in has if ok]
        return "empty" if not kinds else kinds[0] if len(kinds) == 1 else "mixed"


def _net_datetime(ticks):
    try:
        t = int(ticks) & 0x3FFFFFFFFFFFFFFF
        return datetime(1, 1, 1) + timedelta(microseconds=t // 10)
    except (OverflowError, ValueError, TypeError):
        return None


# ===========================================================================
# 4. Public API
# ===========================================================================

_CACHE: dict = {}


def read_nox(path) -> NoxFile:
    """Parse a .nox (cached per file path + modification time, so discovery is not slow)."""
    p = Path(path)
    st = p.stat()
    key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    if key not in _CACHE:
        if len(_CACHE) >= 8:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = _read_nox_uncached(p)
    return _CACHE[key]


def _read_nox_uncached(path) -> NoxFile:
    data = Path(path).read_bytes()
    if not data.startswith(_STREAM_START):
        raise NoxFormatError(f"{Path(path).name}: not a NOVA .nox file (no .NET stream header)")
    try:
        streams = _streams(data)
    except (struct.error, IndexError, KeyError, ValueError) as e:
        raise NoxFormatError(f"{Path(path).name}: corrupt or truncated ({type(e).__name__}: {e})") from e
    if len(streams) < 2:
        raise NoxFormatError(f"{Path(path).name}: only {len(streams)} stream(s); expected procedure header + data")

    nf = NoxFile(str(path))
    head = streams[0][0]
    if isinstance(head, _Obj):
        g = head.members.get
        nf.procedure_name, nf.procedure_text = g("_name") or "", g("_text") or ""
        nf.instrument = g("_instrument") or ""
        nf.recorded = _net_datetime(g("_timeStamp"))

    _, r = max(streams[1:], key=lambda s: len(s[1].objs))
    eis, cv, gcd, seen = [], [], [], set()

    for handler, p in _collections(r):
        # ---- impedance: one collection per frequency
        if _has(p.get("Frequency")) and _has(p.get("H_Real")) and _has(p.get("H_Imaginary")):
            key = ("eis", round(float(_arr(p.get("Time", [0.0]))[0]), 6), float(_arr(p["Frequency"])[0]))
            if key not in seen:               # NOVA keeps a history copy of the first step: same time, same data
                seen.add(key)
                eis.append(p)
            continue
        # ---- cyclic voltammetry: one collection per scan rate
        if "Scanrate" in p and _has(p.get("EI_0.CalcCurrent")):
            t = _arr(p["CalcTime"])
            key = ("cv", round(float(t[0]), 6), len(t))
            if key in seen:
                continue
            seen.add(key)
            cv.append(CVScan(
                scan_rate_V_s=_scalar(p["Scanrate"]), time=t,
                e_applied=_arr(p.get("SetpointApplied", p.get("EI_0.CalcPotential"))),
                e_measured=_arr(p.get("EI_0.CalcPotential")), current=_arr(p["EI_0.CalcCurrent"]),
                cycle=_arr(p.get("ScanNumber", np.ones(len(t))), int)))
            continue
        # ---- chronopotentiometric level steps
        if handler == "FHLevel" and _has(p.get("CalcTime")) and _has(p.get("EI_0.CalcCurrent")):
            t = _arr(p["CalcTime"])
            key = ("gcd", round(float(t[0]), 6), len(t))
            if key in seen:
                continue
            seen.add(key)
            gcd.append(GCStep("unknown", t, _arr(p["EI_0.CalcPotential"]), _arr(p["EI_0.CalcCurrent"])))

    if eis:
        eis.sort(key=lambda p: float(_arr(p["Time"])[0]) if _has(p.get("Time")) else 0.0)
        col = lambda k, d=None: np.array([float(_arr(p.get(k, [np.nan]))[0]) if _has(p.get(k)) else np.nan
                                          for p in eis])
        zre, hi = col("H_Real"), col("H_Imaginary")
        freq = np.array([float(f"{v:.12g}") for v in col("Frequency")])   # 999999.9999999999 -> 1000000.0
        nf.eis = EISData(freq=freq, zre=zre, zim=-hi, modulus=col("H_Modulus"),
                         phase_deg=col("H_Phase"), time_s=col("Time"), e_dc=col("ChannelXDC"),
                         i_dc=col("ChannelYDC"))
    nf.cv = sorted(cv, key=lambda s: s.time[0])
    nf.gcd = sorted(gcd, key=lambda s: s.time[0])

    cycle = 0
    for s in nf.gcd:                                   # classify from the measured data, not from position
        span = float(s.potential.max() - s.potential.min())
        i_med = float(np.median(s.current))
        if span < 0.02 and abs(i_med) < 1e-5:
            s.kind = "rest"
        elif i_med > 0:
            s.kind, cycle = "charge", cycle + 1
            s.cycle = cycle
        else:
            s.kind, s.cycle = "discharge", max(cycle, 1)

    if nf.technique == "empty":
        nf.warnings.append("no measured signals found (procedure only, or a technique this reader does not know)")
    return nf


def nox_technique(path) -> Optional[str]:
    """Fast check for file discovery: 'eis' | 'cv' | 'gcd' from the small procedure header stream
    (no need to decode megabytes of data), or None if the file is not a .nox / not recognisable."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
        if not head.startswith(_STREAM_START):
            return None
        root = _Reader(head).parse()
        text = (root.members.get("_text") or "").lower() if isinstance(root, _Obj) else ""
    except (OSError, struct.error, IndexError, KeyError, ValueError, AttributeError):
        return None
    if "impedance" in text or "fra" in text.split():
        return "eis"
    if "voltammetry" in text:
        return "cv"
    if "potentiometry" in text or "chrono" in text:
        return "gcd"
    try:                                  # unfamiliar procedure title: decide from the data itself
        return read_nox(path).technique
    except (NoxFormatError, OSError):
        return None
