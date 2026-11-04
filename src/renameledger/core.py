"""Plan, apply and undo batch renames without moving files between folders."""
from __future__ import annotations

import csv
from collections import Counter
import os
import re
import stat
import tempfile
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


class _MoveCleanupError(OSError):
    pass


@dataclass
class Item:
    # Relative POSIX paths; old root-only ledgers simply contain filenames.
    old_name: str
    new_name: str
    size_bytes: int = 0
    status: str = "ready"


def default_ledger_dir() -> Path:
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


def _link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _root(folder) -> Path:
    p = Path(folder).absolute()
    if not p.is_dir():
        raise RenameError(f"Folder not found: {p}")
    # Reject symlinks/junctions in every component, not just the selected directory.
    for component in (p, *p.parents):
        if _link_or_reparse(component):
            raise RenameError(f"Linked folders are not supported: {component}")
    return p


def _path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        raise RenameError(f"Invalid relative path in record: {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") or _INVALID_CHARS.search(p) or p != p.rstrip(" .")
           or p.upper().split(".")[0] in _RESERVED for p in parts):
        raise RenameError(f"Invalid relative path in record: {name!r}")
    p = root.joinpath(*parts)
    for component in (p, *list(p.parents)[:len(parts)-1]):
        if _link_or_reparse(component):
            raise RenameError(f"Linked files and folders are not supported: {component}")
    return p


def list_files(folder: Path, extensions: list[str] | None = None, sort: str = "name",
               recursive: bool = False) -> list[Path]:
    folder = _root(folder)
    exts = {e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}" for e in (extensions or []) if e.strip()}
    files = []
    def collect(directory: Path):
        for p in directory.iterdir():
            if _link_or_reparse(p):
                continue
            if p.is_dir() and recursive:
                collect(p)
            elif p.is_file() and p.name.lower() not in _SKIP_NAMES and not p.name.startswith(_TMP_PREFIX):
                if not exts or p.suffix.lower() in exts:
                    files.append(p)
    collect(folder)
    if sort == "modified":
        return sorted(files, key=lambda p: (p.stat().st_mtime, _natural_key(p.relative_to(folder).as_posix())))
    return sorted(files, key=lambda p: _natural_key(p.relative_to(folder).as_posix()))


def plan(folder, prefix: str, start: int = 1, digits: int = 3, separator: str = "_",
         extensions: list[str] | None = None, sort: str = "name", lowercase_ext: bool = False,
         recursive: bool = False) -> list[Item]:
    folder = _root(folder)
    validate_prefix(prefix)
    if _INVALID_CHARS.search(separator):
        raise RenameError("Separator contains a character Windows doesn't allow.")
    if start < 0 or not 1 <= digits <= 9:
        raise RenameError("Start must be 0 or more, and digits must be between 1 and 9.")
    files = list_files(folder, extensions, sort, recursive)
    in_batch = {p.relative_to(folder).as_posix().lower() for p in files}
    taken = {d: {p.name.lower() for p in d.iterdir()
                 if p.relative_to(folder).as_posix().lower() not in in_batch}
             for d in {p.parent for p in files}}
    items, n = [], start
    for p in files:
        ext = p.suffix.lower() if lowercase_ext else p.suffix
        while True:
            name = f"{prefix}{separator}{n:0{digits}d}{ext}"
            n += 1
            if name.lower() not in taken[p.parent]:
                break
        old = p.relative_to(folder).as_posix()
        new = p.with_name(name).relative_to(folder).as_posix()
        items.append(Item(old, new, p.stat().st_size, "unchanged" if new == old else "ready"))
    return items


def _move(src: Path, dst: Path) -> None:
    """Move within a directory without replacing an existing destination."""
    if os.name == "nt":
        os.rename(src, dst)  # Windows rename fails if destination exists.
    else:
        # Linking is atomic and fails if dst exists. Both names are on the same filesystem.
        os.link(src, dst, follow_symlinks=False)
        try:
            os.unlink(src)
        except OSError as exc:
            try:
                os.unlink(dst)
            except OSError as cleanup:
                raise _MoveCleanupError(f"Could not remove temporary link {dst}: {cleanup}") from exc
            raise


def _validate(root: Path, items: list[Item]):
    if any(i.status == "conflict" for i in items):
        raise RenameError("The restore preview contains destination conflicts. Resolve them before continuing.")
    todo = [i for i in items if i.status == "ready"]
    if not todo:
        raise RenameError("Nothing to rename.")
    pairs = []
    for i in items:
        old, new = _path(root, i.old_name), _path(root, i.new_name)
        if old.parent != new.parent:
            raise RenameError("A record cannot move files between folders.")
        if i.status == "ready":
            if not old.is_file():
                raise RenameError(f"File or folder no longer exists: {i.old_name}")
            if not new.parent.is_dir():
                raise RenameError(f"Folder no longer exists: {new.parent}")
            pairs.append((i, old, new))
    olds = [i.old_name.lower() for i, _, _ in pairs]
    news = [i.new_name.lower() for i, _, _ in pairs]
    if len(set(olds)) != len(olds) or len(set(news)) != len(news):
        raise RenameError("The plan contains duplicate source or destination paths.")
    names = {d: {p.name.lower() for p in d.iterdir()} for d in {old.parent for _, old, _ in pairs}}
    oldset = set(olds)
    for i, _, new in pairs:
        if new.name.lower() in names[new.parent] and i.new_name.lower() not in oldset:
            raise RenameError(f"Destination already exists: {i.new_name}. Preview again.")
    return pairs


def _write_ledger(path: Path, folder: Path, items, batch_id, stamp, pending=False, create=False) -> None:
    # Updates are replaced atomically so an interrupted write does not truncate the pending record.
    temporary = None
    try:
        if create:
            f = open(path, "x", newline="", encoding="utf-8-sig")
        else:
            f = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8-sig",
                                            dir=path.parent, prefix=".renameledger-record-", delete=False)
            temporary = Path(f.name)
        with f:
            w = csv.DictWriter(f, LEDGER_FIELDS)
            w.writeheader()
            for i in items:
                w.writerow({"batch_id": batch_id, "timestamp": stamp.isoformat(timespec="seconds"),
                            "folder": str(folder), "old_name": i.old_name, "new_name": i.new_name,
                            "size_bytes": i.size_bytes,
                            "status": "pending" if pending and i.status == "ready" else i.status})
        if temporary:
            os.replace(temporary, path)
            temporary = None
    finally:
        if temporary:
            try:
                temporary.unlink()
            except OSError:
                pass


def apply(folder, items: list[Item], ledger_path=None, progress=None) -> Path:
    root = _root(folder)
    pairs = _validate(root, items)
    batch_id, stamp = uuid.uuid4().hex, datetime.now()
    ledger_path = Path(ledger_path) if ledger_path else default_ledger_dir() / f"{root.name}_{stamp:%Y-%m-%d_%H%M%S}_{batch_id[:8]}.csv"
    # A user-specified record must never overwrite input, output or an existing record.
    record_abs = ledger_path.absolute()
    if any(str(record_abs).lower() == str(p).lower() for _, old, new in pairs for p in (old, new)):
        raise RenameError("The ledger path conflicts with a file being renamed.")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_ledger(ledger_path, root, items, batch_id[:8], stamp, pending=True, create=True)
    except FileExistsError as exc:
        raise RenameError(f"Ledger already exists: {ledger_path}. Choose a new path.") from exc
    temps, done = [], []
    def report(n):
        if progress:
            try:
                progress(n, len(pairs))
            except Exception:
                pass  # feedback must not break a file operation
    report(0)
    try:
        for k, (i, old, new) in enumerate(pairs):
            # Revalidate links immediately before touching each path.
            _path(root, i.old_name); _path(root, i.new_name)
            tmp = old.with_name(f"{_TMP_PREFIX}{batch_id}-{k}")
            _move(old, tmp)
            temps.append((i, old, new, tmp))
        for i, old, new, tmp in temps:
            _path(root, i.new_name)
            _move(tmp, new)
            done.append((i, old, new, tmp))
            report(len(done))
    except (OSError, RenameError) as exc:
        errors = [str(exc)] if isinstance(exc, _MoveCleanupError) else []
        # Move completed destinations back to their temp names first, so cycles can roll back.
        for i, old, new, tmp in reversed(done):
            try:
                _path(root, i.new_name); _move(new, tmp)
            except (OSError, RenameError) as error:
                errors.append(str(error))
        for i, old, new, tmp in reversed(temps):
            if tmp.exists():
                try:
                    _path(root, i.old_name); _move(tmp, old)
                except (OSError, RenameError) as error:
                    errors.append(str(error))
        if not errors:
            for i, _, _ in pairs:
                i.status = "failed"
        try:
            _write_ledger(ledger_path, root, items, batch_id[:8], stamp, pending=bool(errors))
        except OSError as error:
            errors.append(f"Could not update the record: {error}")
        if errors:
            raise RenameError(f"Rename stopped: {exc}. Rollback or record update was incomplete; check files and {ledger_path}. Details: {'; '.join(errors)}") from exc
        raise RenameError(f"Rename stopped and was rolled back: {exc}") from exc
    for i, _, _ in pairs:
        i.status = "renamed"
    try:
        _write_ledger(ledger_path, root, items, batch_id[:8], stamp)
    except OSError as exc:
        raise RenameError(f"Files were renamed, but the record could not be updated: {ledger_path}. Check the files before retrying. {exc}") from exc
    return ledger_path


def read_ledger(path) -> tuple[Path, list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(LEDGER_FIELDS) <= set(reader.fieldnames):
            raise RenameError("This doesn't look like a RenameLedger CSV.")
        rows = list(reader)
    if not rows or any(not r.get("folder") for r in rows):
        raise RenameError("This record is empty or has an invalid folder.")
    folders = {r["folder"] for r in rows}
    if len(folders) != 1:
        raise RenameError("Ledger contains more than one folder.")
    return Path(folders.pop()), rows


def plan_undo(ledger) -> tuple[Path, list[Item]]:
    folder, rows = read_ledger(ledger)
    root = _root(folder)
    items = []
    for r in rows:
        if r.get("status") != "renamed":
            continue
        old, new = _path(root, r.get("new_name")), _path(root, r.get("old_name"))
        if old.parent != new.parent:
            raise RenameError("A record cannot move files between folders.")
        try:
            size = int(r.get("size_bytes") or 0)
        except ValueError as exc:
            raise RenameError("Invalid file size in record.") from exc
        items.append(Item(r["new_name"], r["old_name"], size, "ready" if old.is_file() else "missing"))
    if not items:
        raise RenameError("This ledger has no completed renames to undo.")
    ready = [i for i in items if i.status == "ready"]
    sources = [i.old_name.lower() for i in ready]
    targets = [i.new_name.lower() for i in ready]
    source_counts, target_counts = Counter(sources), Counter(targets)
    sources = set(sources)
    directories = {_path(root, i.old_name).parent for i in ready}
    occupied = {directory: {p.name.lower() for p in directory.iterdir()} for directory in directories}
    for i in ready:
        old, target = i.old_name.lower(), i.new_name.lower()
        parent = _path(root, i.new_name).parent
        if source_counts[old] > 1 or target_counts[target] > 1 or (
                _path(root, i.new_name).name.lower() in occupied[parent] and target not in sources):
            i.status = "conflict"
    return root, items


def undo(ledger, ledger_path=None, progress=None) -> Path:
    folder, items = plan_undo(ledger)
    if ledger_path is None:
        src = Path(ledger)
        ledger_path = src.with_name(f"{src.stem}_undo_{datetime.now():%H%M%S}_{uuid.uuid4().hex[:8]}.csv")
    return apply(folder, items, ledger_path, progress)
