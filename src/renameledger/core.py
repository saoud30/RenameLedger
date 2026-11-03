"""Plan, apply, and undo batch renames. Everything that touches the disk lives here."""
from __future__ import annotations

import csv
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LEDGER_FIELDS = ["batch_id", "timestamp", "folder", "old_name", "new_name", "size_bytes", "status"]
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_SKIP_NAMES = {"desktop.ini", "thumbs.db"}
_TMP_PREFIX = ".renameledger-tmp-"


class RenameError(Exception):
    pass


@dataclass
class Item:
    old_name: str
    new_name: str
    size_bytes: int = 0
    status: str = "ready"  # ready | unchanged | renamed | failed | missing


def default_ledger_dir() -> Path:
    # Kept outside the renamed folder so the old-name mapping doesn't travel with anonymized files.
    return Path.home() / "Documents" / "RenameLedger"


def validate_prefix(prefix: str) -> None:
    if not prefix.strip():
        raise RenameError("Prefix can't be empty.")
    if _INVALID_CHARS.search(prefix):
        raise RenameError('Prefix can\'t contain any of these characters: < > : " / \\ | ? *')
    if prefix != prefix.rstrip(" ."):
        raise RenameError("Prefix can't end with a space or a dot.")
    if prefix.upper().split(".")[0] in _RESERVED:
        raise RenameError(f'"{prefix}" is a reserved name on Windows.')


def _natural_key(name: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def list_files(folder: Path, extensions: list[str] | None = None, sort: str = "name") -> list[Path]:
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or []) if e.strip()}
    files = [
        p for p in folder.iterdir()
        if p.is_file()
        and p.name.lower() not in _SKIP_NAMES
        and not p.name.startswith(_TMP_PREFIX)
        and (not exts or p.suffix.lower() in exts)
    ]
    if sort == "modified":
        return sorted(files, key=lambda p: (p.stat().st_mtime, _natural_key(p.name)))
    return sorted(files, key=lambda p: _natural_key(p.name))


def plan(folder, prefix: str, start: int = 1, digits: int = 3, separator: str = "_",
         extensions: list[str] | None = None, sort: str = "name", lowercase_ext: bool = False) -> list[Item]:
    """Return the proposed renames. Numbers already used by files outside the batch are skipped."""
    folder = Path(folder)
    if not folder.is_dir():
        raise RenameError(f"Folder not found: {folder}")
    validate_prefix(prefix)
    if _INVALID_CHARS.search(separator):
        raise RenameError("Separator contains a character Windows doesn't allow.")
    if start < 0 or not 1 <= digits <= 9:
        raise RenameError("Start must be 0 or more, and digits must be between 1 and 9.")

    files = list_files(folder, extensions, sort)
    in_batch = {p.name.lower() for p in files}
    taken = {p.name.lower() for p in folder.iterdir()} - in_batch  # Windows names are case-insensitive

    items, n = [], start
    for p in files:
        ext = p.suffix.lower() if lowercase_ext else p.suffix
        while True:
            new = f"{prefix}{separator}{n:0{digits}d}{ext}"
            n += 1
            if new.lower() not in taken:
                break
        items.append(Item(p.name, new, p.stat().st_size, "unchanged" if new == p.name else "ready"))
    return items


def _move(src: Path, dst: Path) -> None:
    # os.rename overwrites silently on Linux/macOS; never allow that.
    if dst.exists() and not (os.path.normcase(str(src)) == os.path.normcase(str(dst))):
        raise FileExistsError(f"{dst.name} already exists")
    os.rename(src, dst)


def apply(folder, items: list[Item], ledger_path=None) -> Path:
    """Rename every 'ready' item in two phases (old -> temp -> new) so swaps and
    case-only changes work. On any failure, everything is rolled back. Returns the ledger path."""
    folder = Path(folder).resolve()
    todo = [i for i in items if i.status == "ready"]
    if not todo:
        raise RenameError("Nothing to rename.")

    names_lower = {p.name.lower() for p in folder.iterdir()}
    olds_lower = {i.old_name.lower() for i in todo}
    news = [i.new_name.lower() for i in todo]
    if len(set(news)) != len(news):
        raise RenameError("Two files would get the same new name.")
    for i in todo:
        if not (folder / i.old_name).is_file():
            raise RenameError(f"File no longer exists: {i.old_name}")
        if i.new_name.lower() in names_lower and i.new_name.lower() not in olds_lower:
            raise RenameError(f"A file named {i.new_name} already exists. Preview again.")

    batch_id = uuid.uuid4().hex[:8]
    stamp = datetime.now()
    ledger_path = Path(ledger_path) if ledger_path else (
        default_ledger_dir() / f"{folder.name}_{stamp:%Y-%m-%d_%H%M%S}_{batch_id}.csv")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    # Write the plan before touching files so a crash still leaves a record.
    _write_ledger(ledger_path, folder, items, batch_id, stamp, pending=True)

    temps: list[tuple[Item, Path]] = []
    done: list[Item] = []
    try:
        for k, i in enumerate(todo):
            tmp = folder / f"{_TMP_PREFIX}{batch_id}-{k}"
            _move(folder / i.old_name, tmp)
            temps.append((i, tmp))
        for i, tmp in temps:
            _move(tmp, folder / i.new_name)
            done.append(i)
    except OSError as exc:
        for i in done:
            os.rename(folder / i.new_name, folder / i.old_name)
        for i, tmp in temps:
            if i not in done and tmp.exists():
                os.rename(tmp, folder / i.old_name)
        for i in todo:
            i.status = "failed"
        _write_ledger(ledger_path, folder, items, batch_id, stamp)
        raise RenameError(f"Rename stopped and was rolled back: {exc}") from exc

    for i in todo:
        i.status = "renamed"
    _write_ledger(ledger_path, folder, items, batch_id, stamp)
    return ledger_path


def _write_ledger(path: Path, folder: Path, items, batch_id, stamp, pending=False) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows non-ASCII names
        w = csv.DictWriter(f, LEDGER_FIELDS)
        w.writeheader()
        for i in items:
            w.writerow({"batch_id": batch_id, "timestamp": stamp.isoformat(timespec="seconds"),
                        "folder": str(folder), "old_name": i.old_name, "new_name": i.new_name,
                        "size_bytes": i.size_bytes,
                        "status": "pending" if pending and i.status == "ready" else i.status})


def read_ledger(path) -> tuple[Path, list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows or not set(LEDGER_FIELDS) <= set(rows[0]):
        raise RenameError("This doesn't look like a RenameLedger CSV.")
    folders = {r["folder"] for r in rows}
    if len(folders) != 1:
        raise RenameError("Ledger contains more than one folder.")
    return Path(folders.pop()), rows


def plan_undo(ledger) -> tuple[Path, list[Item]]:
    """Reverse every 'renamed' row of a ledger. Rows whose file is gone are marked 'missing'."""
    folder, rows = read_ledger(ledger)
    if not folder.is_dir():
        raise RenameError(f"Folder from the ledger no longer exists: {folder}")
    items = []
    for r in rows:
        if r["status"] != "renamed":
            continue
        ok = (folder / r["new_name"]).is_file()
        items.append(Item(r["new_name"], r["old_name"], int(r["size_bytes"] or 0), "ready" if ok else "missing"))
    if not items:
        raise RenameError("This ledger has no completed renames to undo.")
    return folder, items


def undo(ledger, ledger_path=None) -> Path:
    folder, items = plan_undo(ledger)
    if ledger_path is None:
        src = Path(ledger)
        ledger_path = src.with_name(f"{src.stem}_undo_{datetime.now():%H%M%S}.csv")
    return apply(folder, items, ledger_path)
