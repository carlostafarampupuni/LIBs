"""
Lab book from the terminal (or a PyCharm run configuration). Run from
the Results/ folder:

    python -m battery_analysis.labbook list
    python -m battery_analysis.labbook new "2.5 wt% AdditiveA in 1 M NaPF6/G2" --template electrolyte_additive
    python -m battery_analysis.labbook calc 2026-10-22a       # masses to weigh, printed right here
    python -m battery_analysis.labbook report 2026-10-22a     # PDF of one entry
    python -m battery_analysis.labbook report latest
    python -m battery_analysis.labbook report all             # the whole book
    python -m battery_analysis.labbook report all --since 2026-10-01 --until 2026-10-31
    python -m battery_analysis.labbook templates
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .book import LabBook


def _default_root() -> Path:
    # ./LabBook, then ./Project/LabBook, then <LIBs>/Project/LabBook
    here = Path.cwd()
    for d in (here / "LabBook", here / "Project" / "LabBook", here.parent / "LabBook",
              Path(__file__).resolve().parents[2] / "Project" / "LabBook",
              Path(__file__).resolve().parents[2] / "LabBook"):
        if d.exists():
            return d
    return here / "LabBook"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="labbook", description="Electronic lab notebook.")
    ap.add_argument("--book", default=str(_default_root()), help="the LabBook folder")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="every entry")
    sub.add_parser("templates", help="templates `new` can start from")

    p = sub.add_parser("new", help="start a new entry (today, next free letter)")
    p.add_argument("title")
    p.add_argument("--template", default="blank")
    p.add_argument("--date", help="YYYY-MM-DD, if not today")

    p = sub.add_parser("calc", help="print the masses to weigh")
    p.add_argument("entry", nargs="?", default="latest")

    p = sub.add_parser("report", help="build the PDF")
    p.add_argument("entry", nargs="?", default="latest", help="an id, 'latest' or 'all'")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--no-open", action="store_true")

    a = ap.parse_args(argv)
    book = LabBook(a.book)
    if a.cmd == "list":
        print(book.list())
    elif a.cmd == "templates":
        print("\n".join(book.templates()))
    elif a.cmd == "new":
        book.new(a.title, template=a.template, date=a.date)
    elif a.cmd == "calc":
        print(book.get(a.entry).calc_text())
    elif a.cmd == "report":
        book.report(a.entry, since=a.since, until=a.until, open_pdf=not a.no_open)


if __name__ == "__main__":
    main()
