"""Command line: renameledger preview|rename|undo ... (no arguments opens the window)."""
from __future__ import annotations

import argparse
import sys

from . import __version__, core


def _print_items(items) -> None:
    w = max((len(i.old_name) for i in items), default=0)
    for i in items:
        print(f"  {i.old_name:<{w}}  ->  {i.new_name}" + ("" if i.status == "ready" else f"   [{i.status}]"))


def _confirm(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="renameledger", description="Batch-rename files and keep a CSV record.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    for name in ("preview", "rename"):
        s = sub.add_parser(name, help=f"{name} a batch rename")
        s.add_argument("folder")
        s.add_argument("--prefix", required=True, help="e.g. sample -> sample_001.pdf")
        s.add_argument("--start", type=int, default=1)
        s.add_argument("--digits", type=int, default=3)
        s.add_argument("--separator", default="_")
        s.add_argument("--ext", default="", help="only these extensions, comma separated: pdf,jpg")
        s.add_argument("--sort", choices=["name", "modified"], default="name")
        s.add_argument("--lowercase-ext", action="store_true")
        if name == "rename":
            s.add_argument("--ledger", help="where to save the CSV (default: Documents\\RenameLedger)")
            s.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    u = sub.add_parser("undo", help="restore original names from a ledger CSV")
    u.add_argument("ledger")
    u.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("gui", help="open the window")

    a = p.parse_args(argv)
    if a.cmd in (None, "gui"):
        from .gui import run
        run()
        return 0

    try:
        if a.cmd == "undo":
            folder, items = core.plan_undo(a.ledger)
            print(f"Undo in {folder}:")
            _print_items(items)
            if not a.yes and not _confirm("Restore these names?"):
                return 1
            print(f"Done. Undo record: {core.undo(a.ledger)}")
            return 0

        items = core.plan(a.folder, a.prefix, a.start, a.digits, a.separator,
                          a.ext.split(",") if a.ext else None, a.sort, a.lowercase_ext)
        if not items:
            print("No matching files.")
            return 1
        _print_items(items)
        ready = sum(i.status == "ready" for i in items)
        print(f"{ready} of {len(items)} files will be renamed.")
        if a.cmd == "preview" or not ready:
            return 0
        if not a.yes and not _confirm("Rename now?"):
            return 1
        print(f"Done. Ledger saved to: {core.apply(a.folder, items, a.ledger)}")
        return 0
    except (core.RenameError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
