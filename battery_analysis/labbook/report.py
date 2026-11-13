"""
PDF reports — entries -> JSON -> Typst -> PDF.

Python only gathers the numbers (everything already calculated by
recipes.py / entry.py) into one JSON file; the look of the page lives
entirely in templates/report.typ, which you can restyle without
touching any Python. Typst (brew install typst) does the typesetting.

Photos: #photo("name.jpg") in any free-text field looks for, in order,
    Figures/Photos/<entry id>/name.jpg
    Figures/Photos/name.jpg
    <Results>/name.jpg                 (e.g. "Figures/SEM/cross.png")
iPhone .heic photos are converted, and big photos shrunk to 1600 px,
into LabBook/reports/.build/photos/ — your originals are never touched.
Photos in an entry's folder that no #photo() mentions are printed at the
end of that entry, so nothing gets forgotten.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

TEMPLATE = Path(__file__).parent / "templates" / "report.typ"
FONTS = Path(__file__).parent / "templates" / "fonts"
_PHOTO_RE = re.compile(r'#photo\(\s*"([^"]+)"')
_TEXT_FIELDS = ("aim", "notes", "results", "next_steps")
_MAX_PX = 1600


def _referenced(data: dict) -> list[str]:
    texts = [data.get(k) or "" for k in _TEXT_FIELDS]
    texts += [x["text"] for x in data["log"]]
    texts += [r.get("notes") or "" for r in data["recipes"]]
    texts += [str(s) for r in data["recipes"] for s in r.get("procedure") or []]
    return _PHOTO_RE.findall("\n".join(texts))


def _prepare_photo(src: Path, cache: Path) -> Path:
    """HEIC -> JPEG and shrink big photos (macOS `sips`); otherwise use the original."""
    needs_convert = src.suffix.lower() in {".heic", ".heif"}
    if src.suffix.lower() == ".svg":
        return src
    try:
        from PIL import Image
        if not needs_convert:
            with Image.open(src) as im:
                if max(im.size) <= _MAX_PX:
                    return src
    except Exception:
        pass
    out = cache / (src.stem + ".jpg")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    cache.mkdir(parents=True, exist_ok=True)
    if shutil.which("sips"):
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(_MAX_PX), str(src), "--out", str(out)],
                       check=True, capture_output=True)
        return out
    return src


def _typst_root(book) -> Path:
    """Typst only reads files under --root: the project folder, widened to also
    cover the photos folder when figures live outside it (e.g. ../Figures)."""
    return Path(os.path.commonpath([book.root.parent, book.photos_dir]))


def _photo_map(book, entry, data: dict, cache: Path) -> tuple[dict, list]:
    """{name used in #photo(): path from the Typst root}, plus the unreferenced ones."""
    root = _typst_root(book)
    project = book.root.parent
    own = {p.name: p for p in book.photos_for(entry.id)}
    found = {}
    for name in _referenced(data):
        for cand in (book.photos_dir / entry.id / name, book.photos_dir / name, project / name):
            if cand.exists():
                found[name] = cand
                break
    unref = [p for n, p in own.items() if n not in found]
    rel = lambda p: "/" + _prepare_photo(p, cache / entry.id).relative_to(root).as_posix()
    return ({n: rel(p) for n, p in found.items()},
            [{"path": rel(p), "name": p.name} for p in unref])


def _settings(book) -> dict:
    path = book.root / "settings.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def build_pdf(book, entries: list, out_path: Path, open_pdf: bool = True) -> Path:
    if not entries:
        raise ValueError("No entries to print.")
    typst = shutil.which("typst") or "/opt/homebrew/bin/typst"
    if not Path(typst).exists():
        raise RuntimeError("Typst isn't installed — run `brew install typst` once, then try again.")

    root = _typst_root(book)
    build = book.reports_dir / ".build"
    build.mkdir(parents=True, exist_ok=True)

    payload_entries = []
    for e in entries:
        d = e.data()
        d["photos"], d["unreferenced_photos"] = _photo_map(book, e, d, build / "photos")
        payload_entries.append(d)

    payload = {
        "settings": _settings(book),
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "single": len(entries) == 1,
        "entries": payload_entries,
    }
    data_path = build / (out_path.stem + ".json")
    data_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str))

    # Typst only reads sources inside --root (the project folder). The
    # template ships with the shared engine, which lives elsewhere, so
    # compile a copy placed in this project's build folder.
    template = build / TEMPLATE.name
    shutil.copyfile(TEMPLATE, template)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [typst, "compile", "--root", str(root), "--font-path", str(FONTS),
           "--input", "data=/" + data_path.relative_to(root).as_posix(),
           str(template), str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Typst could not build the report. Usually this is a typo in a notes/log text "
            "(Typst markup), e.g. an unclosed * or $, or a # before a word.\n\n" + proc.stderr
        )
    print(f"Report: {out_path}")
    if open_pdf and sys.platform == "darwin":
        subprocess.run(["open", str(out_path)])
    elif open_pdf and sys.platform == "win32":
        os.startfile(out_path)
    return out_path
