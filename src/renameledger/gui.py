"""Tkinter window: pick a folder, watch the preview update, rename, undo."""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, core


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _open(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class App(ttk.Frame):
    def __init__(self, root: tk.Tk, folder: str = ""):
        super().__init__(root, padding=14)
        self.root = root
        self.items: list[core.Item] = []
        self.undo_ledger: str | None = None
        self._after = None

        self.folder = tk.StringVar(value=folder)
        self.prefix = tk.StringVar(value="file")
        self.sep = tk.StringVar(value="_")
        self.start = tk.StringVar(value="1")
        self.digits = tk.StringVar(value="3")
        self.sort = tk.StringVar(value="name")
        self.exts = tk.StringVar()
        self.lower = tk.BooleanVar()
        self.status = tk.StringVar(value="Choose a folder to see a preview.")

        self._build()
        for v in (self.folder, self.prefix, self.sep, self.start, self.digits, self.sort, self.exts, self.lower):
            v.trace_add("write", lambda *_: self._schedule())
        self._schedule()

    # ---- layout ----
    def _build(self) -> None:
        self.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Folder").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(top, textvariable=self.folder).grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="Browse…", command=self._browse).grid(row=0, column=2, padx=(8, 0))

        opts = ttk.LabelFrame(self, text="New names", padding=(10, 6))
        opts.grid(row=1, column=0, sticky="ew", pady=12)
        fields = [("Prefix", ttk.Entry(opts, textvariable=self.prefix, width=18)),
                  ("Separator", ttk.Entry(opts, textvariable=self.sep, width=4)),
                  ("Start at", ttk.Spinbox(opts, from_=0, to=999999, textvariable=self.start, width=7)),
                  ("Digits", ttk.Spinbox(opts, from_=1, to=9, textvariable=self.digits, width=4)),
                  ("Order by", ttk.Combobox(opts, textvariable=self.sort, values=["name", "modified"],
                                            state="readonly", width=9)),
                  ("Only types", ttk.Entry(opts, textvariable=self.exts, width=14))]
        for col, (label, widget) in enumerate(fields):
            ttk.Label(opts, text=label).grid(row=0, column=col, sticky="w", padx=(0, 12))
            widget.grid(row=1, column=col, sticky="w", padx=(0, 12))
        ttk.Checkbutton(opts, text="Lowercase extensions", variable=self.lower).grid(
            row=1, column=len(fields), sticky="w")
        ttk.Label(opts, text="Example: prefix “sample” gives sample_001.pdf.  Only types: pdf, jpg (blank = all)",
                  foreground="#666").grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))

        table = ttk.Frame(self)
        table.grid(row=2, column=0, sticky="nsew")
        self.rowconfigure(2, weight=1)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("old", "new", "size", "status"), show="headings", height=16)
        for col, text, width, anchor in (("old", "Current name", 280, "w"), ("new", "New name", 220, "w"),
                                         ("size", "Size", 80, "e"), ("status", "Status", 90, "w")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, anchor=anchor, stretch=col in ("old", "new"))
        self.tree.tag_configure("unchanged", foreground="#888")
        self.tree.tag_configure("missing", foreground="#b3261e")
        self.tree.tag_configure("failed", foreground="#b3261e")
        self.tree.tag_configure("renamed", foreground="#1b6e3a")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        bar = ttk.Frame(self)
        bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.status, wraplength=520).grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Undo a rename…", command=self._load_undo).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(bar, text="Open ledgers", command=self._open_ledgers).grid(row=0, column=2, padx=(8, 0))
        self.go = ttk.Button(bar, text="Rename", command=self._run, state="disabled")
        self.go.grid(row=0, column=3, padx=(8, 0))
        self.root.bind("<Escape>", lambda _: self._cancel_undo())

    # ---- behaviour ----
    def _browse(self) -> None:
        d = filedialog.askdirectory(title="Choose the folder to rename", initialdir=self.folder.get() or None)
        if d:
            self.folder.set(d)

    def _schedule(self) -> None:
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(250, self._refresh)

    def _refresh(self) -> None:
        if self.undo_ledger:
            return
        self.items = []
        try:
            if not self.folder.get().strip():
                raise core.RenameError("Choose a folder to see a preview.")
            self.items = core.plan(self.folder.get().strip(), self.prefix.get(), int(self.start.get() or 0),
                                   int(self.digits.get() or 3), self.sep.get(),
                                   [e.strip() for e in self.exts.get().split(",")] or None,
                                   self.sort.get(), self.lower.get())
            ready = sum(i.status == "ready" for i in self.items)
            self.status.set(f"{ready} of {len(self.items)} files will be renamed. "
                            f"The record is saved to {core.default_ledger_dir()}." if self.items
                            else "No matching files in this folder.")
        except ValueError:
            self.status.set("Start and digits must be whole numbers.")
        except (core.RenameError, OSError) as exc:
            self.status.set(str(exc))
        self._show(self.items)
        self.go.configure(text="Rename", state="normal" if any(i.status == "ready" for i in self.items) else "disabled")

    def _show(self, items) -> None:
        self.tree.delete(*self.tree.get_children())
        for i in items:
            label = {"ready": "", "unchanged": "same name"}.get(i.status, i.status)
            self.tree.insert("", "end", values=(i.old_name, i.new_name, _human(i.size_bytes), label), tags=(i.status,))

    def _run(self) -> None:
        todo = sum(i.status == "ready" for i in self.items)
        if self.undo_ledger:
            if not messagebox.askyesno("Undo rename", f"Restore the original names of {todo} files?"):
                return
            try:
                path = core.undo(self.undo_ledger)
            except core.RenameError as exc:
                messagebox.showerror("Undo failed", str(exc))
                return
            self._cancel_undo()
            messagebox.showinfo("Restored", f"Original names restored.\n\nUndo record:\n{path}")
            return
        if not messagebox.askyesno("Rename files", f"Rename {todo} files in\n{self.folder.get()}?\n\n"
                                   "A CSV record is saved so you can undo this later."):
            return
        try:
            path = core.apply(self.folder.get().strip(), self.items)
        except core.RenameError as exc:
            messagebox.showerror("Nothing was renamed", str(exc))
            self._refresh()
            return
        self._show(self.items)
        self.go.configure(state="disabled")
        self.status.set(f"Renamed {todo} files. Record saved to {path}")
        if messagebox.askyesno("Done", f"Renamed {todo} files.\n\nRecord saved to:\n{path}\n\nOpen the record?"):
            _open(path)

    def _load_undo(self) -> None:
        start = core.default_ledger_dir()
        path = filedialog.askopenfilename(title="Choose a RenameLedger record", filetypes=[("CSV record", "*.csv")],
                                          initialdir=start if start.is_dir() else None)
        if not path:
            return
        try:
            folder, items = core.plan_undo(path)
        except (core.RenameError, OSError, KeyError) as exc:
            messagebox.showerror("Can't undo", str(exc))
            return
        self.undo_ledger, self.items = path, items
        self._show(items)
        missing = sum(i.status == "missing" for i in items)
        self.status.set(f"Undo preview for {folder}." + (f" {missing} files are missing and will be skipped." if missing
                                                         else "") + "  Press Esc to cancel.")
        self.go.configure(text="Restore names", state="normal" if len(items) > missing else "disabled")

    def _cancel_undo(self) -> None:
        if self.undo_ledger:
            self.undo_ledger = None
            self._refresh()

    def _open_ledgers(self) -> None:
        d = core.default_ledger_dir()
        d.mkdir(parents=True, exist_ok=True)
        _open(d)


def run(folder: str = "") -> None:
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    root.title(f"RenameLedger {__version__}")
    root.minsize(760, 480)
    if "vista" in ttk.Style().theme_names():
        ttk.Style().theme_use("vista")
    App(root, folder or (sys.argv[1] if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir() else ""))
    root.mainloop()


if __name__ == "__main__":
    run()
