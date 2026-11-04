"""Tkinter interface; filesystem work runs in background threads."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from datetime import datetime
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
        self.undo_ledger = None
        self.undo_folder = None
        self._after = None
        self._poll_after = None
        self._render_after = None
        self._generation = 0
        self._render_generation = 0
        self._busy = False
        self._preview_ready = False
        self._messages = queue.Queue()
        self.folder = tk.StringVar(value=folder)
        self.prefix = tk.StringVar(value="file")
        self.sep = tk.StringVar(value="_")
        self.start = tk.StringVar(value="1")
        self.digits = tk.StringVar(value="3")
        self.sort = tk.StringVar(value="name")
        self.exts = tk.StringVar()
        self.lower = tk.BooleanVar()
        self.recursive = tk.BooleanVar()
        self.status = tk.StringVar(value="Choose a folder to see a preview.")
        self._build()
        for v in (self.folder, self.prefix, self.sep, self.start, self.digits, self.sort, self.exts, self.lower, self.recursive):
            v.trace_add("write", lambda *_: self._schedule())
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._poll_after = self.after(75, self._poll)
        self._schedule()

    def _build(self):
        self.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1); self.root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1); self.rowconfigure(2, weight=1)
        self.controls = []
        top = ttk.Frame(self); top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Folder").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.folder_entry = ttk.Entry(top, textvariable=self.folder)
        self.folder_entry.grid(row=0, column=1, sticky="ew"); self.controls.append(self.folder_entry)
        browse = ttk.Button(top, text="Browse…", underline=0, command=self._browse)
        browse.grid(row=0, column=2, padx=(8, 0)); self.controls.append(browse)

        opts = ttk.LabelFrame(self, text="New names", padding=(10, 8))
        opts.grid(row=1, column=0, sticky="ew", pady=12)
        fields = [("Prefix", ttk.Entry(opts, textvariable=self.prefix, width=18)),
                  ("Separator", ttk.Entry(opts, textvariable=self.sep, width=5)),
                  ("Start at", ttk.Spinbox(opts, from_=0, to=999999, textvariable=self.start, width=7)),
                  ("Digits", ttk.Spinbox(opts, from_=1, to=9, textvariable=self.digits, width=4)),
                  ("Order by", ttk.Combobox(opts, textvariable=self.sort, values=["name", "modified"], state="readonly", width=9)),
                  ("Only types", ttk.Entry(opts, textvariable=self.exts, width=14))]
        for col, (label, widget) in enumerate(fields):
            ttk.Label(opts, text=label).grid(row=0, column=col, sticky="w", padx=(0, 12))
            widget.grid(row=1, column=col, sticky="w", padx=(0, 12)); self.controls.append(widget)
        checks = ttk.Frame(opts)
        checks.grid(row=2, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self.include_subfolders = ttk.Checkbutton(checks, text="Include subfolders", variable=self.recursive)
        self.include_subfolders.grid(row=0, column=0, sticky="w", padx=(0, 24))
        self.controls.append(self.include_subfolders)
        lower = ttk.Checkbutton(checks, text="Lowercase extensions", variable=self.lower)
        lower.grid(row=0, column=1, sticky="w")
        self.controls.append(lower)
        ttk.Label(opts, text="Example: sample_001.pdf  •  Types: pdf, jpg (blank = all)").grid(
            row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))

        table = ttk.Frame(self); table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1); table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("old", "new", "size", "status"), show="headings", height=16)
        for col, text, width, anchor in (("old", "Current path", 280, "w"), ("new", "Proposed path", 280, "w"),
                                         ("size", "Size", 75, "e"), ("status", "Status", 90, "w")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, minwidth=65, anchor=anchor, stretch=col in ("old", "new"))
        for tag, color in (("unchanged", "#666666"), ("missing", "#a12a22"), ("failed", "#a12a22"), ("conflict", "#a12a22"), ("renamed", "#1b6e3a")):
            self.tree.tag_configure(tag, foreground=color)
        vs = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.grid(row=3, column=0, sticky="ew", pady=(9, 0))
        bar = ttk.Frame(self); bar.grid(row=4, column=0, sticky="ew", pady=(8, 0)); bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.status, wraplength=520).grid(row=0, column=0, sticky="w")
        undo = ttk.Button(bar, text="Undo a rename…", command=self._load_undo)
        undo.grid(row=0, column=1, padx=(8, 0)); self.controls.append(undo)
        ledgers = ttk.Button(bar, text="Open ledgers", command=self._open_ledgers)
        ledgers.grid(row=0, column=2, padx=(8, 0)); self.controls.append(ledgers)
        self.go = ttk.Button(bar, text="Rename", command=self._run, state="disabled")
        self.go.grid(row=0, column=3, padx=(8, 0))
        self.root.bind("<Escape>", lambda _: self._cancel_undo())
        self.root.bind("<Alt-b>", lambda _: self._browse() if not self._busy else None)
        self.root.bind("<F5>", lambda _: self._schedule() if not self._busy else None)

    def _browse(self):
        d = filedialog.askdirectory(title="Choose the folder to rename", initialdir=self.folder.get() or None)
        if d:
            self.folder.set(d)

    def _schedule(self):
        if self._busy:
            return
        if self.undo_ledger:
            self.undo_ledger = self.undo_folder = None
        self._generation += 1
        self._preview_ready = False
        if self._after:
            self.after_cancel(self._after)
        self.items = []
        self._show([])
        self.go.configure(text="Rename", state="disabled")
        self.status.set("Updating preview…")
        self._after = self.after(250, self._refresh)

    def _refresh(self):
        self._after = None
        if self._busy or self.undo_ledger:
            return
        generation = self._generation
        try:
            folder = self.folder.get().strip()
            if not folder:
                raise core.RenameError("Choose a folder to see a preview.")
            args = (folder, self.prefix.get(), int(self.start.get() or 0), int(self.digits.get() or 3),
                    self.sep.get(), [e.strip() for e in self.exts.get().split(",")], self.sort.get(),
                    self.lower.get(), self.recursive.get())
        except (ValueError, core.RenameError) as exc:
            self.items = []; self._show([])
            self.status.set("Start and digits must be whole numbers." if isinstance(exc, ValueError) else str(exc))
            return
        def work():
            try:
                self._messages.put(("preview", generation, core.plan(*args), None))
            except Exception as exc:
                self._messages.put(("preview", generation, [], str(exc) or "Could not create preview."))
        threading.Thread(target=work, daemon=True).start()

    def _show(self, items, on_complete=None):
        # Insert in chunks, allowing repaint and input between large table updates.
        self._render_generation += 1
        generation = self._render_generation
        if self._render_after:
            self.after_cancel(self._render_after); self._render_after = None
        self.tree.delete(*self.tree.get_children())
        def insert(start=0):
            if generation != self._render_generation:
                return
            for i in items[start:start + 150]:
                label = {"ready": "ready", "unchanged": "unchanged"}.get(i.status, i.status)
                self.tree.insert("", "end", values=(i.old_name, i.new_name, _human(i.size_bytes), label), tags=(i.status,))
            if start + 150 < len(items):
                self._render_after = self.after(1, lambda: insert(start + 150))
            else:
                self._render_after = None
                if on_complete:
                    on_complete()
        insert()

    def _set_busy(self, busy):
        self._busy = busy
        for widget in self.controls:
            widget.state(["disabled"] if busy else ["!disabled"])
        self.go.configure(state="disabled" if busy else "normal")

    def _run(self):
        if self._busy or not self._preview_ready:
            return
        todo = sum(i.status == "ready" for i in self.items)
        missing = sum(i.status == "missing" for i in self.items)
        unchanged = sum(i.status == "unchanged" for i in self.items)
        if not todo:
            return
        is_undo = bool(self.undo_ledger)
        verb = "Restore" if is_undo else "Rename"
        target_folder = self.undo_folder if is_undo else self.folder.get().strip()
        if not messagebox.askyesno(f"{verb} files", f"{verb} {todo} files in:\n{target_folder}\n\nA CSV record will be saved."):
            return
        folder = self.undo_folder if is_undo else self.folder.get().strip()
        items = self.items
        ledger = self.undo_ledger
        self._generation += 1
        self._preview_ready = False
        if self._after:
            self.after_cancel(self._after); self._after = None
        self._set_busy(True)
        self.progress.configure(value=0)
        self.status.set(f"Preparing {todo} files… Processed: 0; remaining: {todo}.")
        def work():
            last = [0.0]
            def progress(done, total):
                now = time.monotonic()
                if done in (0, total) or now - last[0] >= 0.05:
                    self._messages.put(("progress", done, total)); last[0] = now
            try:
                record = None
                if ledger:
                    source = Path(ledger)
                    record = source.with_name(f"{source.stem}_undo_{datetime.now():%H%M%S}_{uuid.uuid4().hex[:8]}.csv")
                path = core.apply(folder, items, record, progress)
                self._messages.put(("finished", is_undo, todo, missing, unchanged, path, None))
            except Exception as exc:
                self._messages.put(("finished", is_undo, todo, missing, unchanged, None, str(exc) or "The operation failed."))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            for _ in range(100):
                try:
                    msg = self._messages.get_nowait()
                except queue.Empty:
                    break
                if msg[0] == "preview":
                    _, generation, items, error = msg
                    if generation != self._generation or self._busy or self.undo_ledger:
                        continue
                    self.items = items
                    ready = sum(i.status == "ready" for i in items)
                    same = sum(i.status == "unchanged" for i in items)
                    scope = "Including subfolders" if self.recursive.get() else "Selected folder only"
                    self.status.set(error or (f"{scope}: {ready} ready, {same} unchanged; {len(items)} total." if items else f"{scope}: no matching files."))
                    self.go.configure(text="Rename", state="disabled")
                    def preview_ready(generation=generation, ready=ready):
                        if generation == self._generation and not self._busy and not self.undo_ledger:
                            self._preview_ready = True
                            self.go.configure(state="normal" if ready else "disabled")
                    self._show(items, preview_ready)
                elif msg[0] == "undo_preview":
                    _, generation, path, folder, items, error = msg
                    if generation != self._generation or self._busy:
                        continue
                    if error:
                        self.status.set("Could not load restore preview.")
                        messagebox.showerror("Can't undo", error)
                        self._schedule()
                        continue
                    self.undo_ledger, self.undo_folder, self.items = path, folder, items
                    ready = sum(i.status == "ready" for i in items)
                    missing = sum(i.status == "missing" for i in items)
                    conflicts = sum(i.status == "conflict" for i in items)
                    self.status.set(f"Restore preview for {folder}: {ready} ready; {missing} missing; {conflicts} conflicts. Resolve conflicts before restoring. Press Esc to cancel.")
                    self.go.configure(text="Restore names", state="disabled")
                    def undo_ready(generation=generation, ready=ready, conflicts=conflicts):
                        if generation == self._generation and not self._busy and self.undo_ledger:
                            self._preview_ready = bool(ready and not conflicts)
                            self.go.configure(state="normal" if self._preview_ready else "disabled")
                    self._show(items, undo_ready)
                elif msg[0] == "progress":
                    _, done, total = msg
                    self.progress.configure(value=100 * done / total if total else 0)
                    self.status.set(f"Processed: {done} of {total}; remaining: {total - done}." + (" Preparing files…" if done == 0 else ""))
                elif msg[0] == "finished":
                    _, is_undo, todo, missing, unchanged, path, error = msg
                    self._set_busy(False)
                    if error:
                        self.go.configure(state="disabled")
                        self.status.set("Operation stopped. Check the message and record before retrying.")
                        messagebox.showerror("Operation stopped", error)
                        if is_undo:
                            self.undo_ledger = self.undo_folder = None
                        self._schedule()
                    else:
                        self._show(self.items)
                        self.go.configure(state="disabled")
                        action = "Restored" if is_undo else "Renamed"
                        self.status.set(f"{action} {todo}; unchanged: {unchanged}; missing: {missing}. Record: {path}")
                        messagebox.showinfo(action, f"{action}: {todo}\nUnchanged: {unchanged}\nMissing/skipped: {missing}\n\nRecord:\n{path}")
                        if is_undo:
                            self.undo_ledger = self.undo_folder = None
        finally:
            self._poll_after = self.after(75, self._poll)

    def _load_undo(self):
        start = core.default_ledger_dir()
        path = filedialog.askopenfilename(title="Choose a RenameLedger record", filetypes=[("CSV record", "*.csv")],
                                          initialdir=start if start.is_dir() else None)
        if not path:
            return
        self._generation += 1
        generation = self._generation
        self._preview_ready = False
        if self._after:
            self.after_cancel(self._after); self._after = None
        self.undo_ledger = self.undo_folder = None
        self.items = []
        self._show([])
        self.go.configure(state="disabled")
        self.status.set("Loading restore preview…")
        def work():
            try:
                folder, items = core.plan_undo(path)
                self._messages.put(("undo_preview", generation, path, folder, items, None))
            except Exception as exc:
                self._messages.put(("undo_preview", generation, path, None, [], str(exc) or "Could not load record."))
        threading.Thread(target=work, daemon=True).start()

    def _cancel_undo(self):
        if not self._busy and self.undo_ledger:
            self.undo_ledger = self.undo_folder = None
            self._schedule()

    def _open_ledgers(self):
        try:
            d = core.default_ledger_dir(); d.mkdir(parents=True, exist_ok=True); _open(d)
        except OSError as exc:
            messagebox.showerror("Can't open ledgers", str(exc))

    def destroy(self):
        for token in (self._after, self._poll_after, self._render_after):
            if token:
                self.after_cancel(token)
        super().destroy()

    def _close(self):
        if self._busy:
            messagebox.showinfo("Operation in progress", "Wait for the current operation to finish before closing.")
        else:
            self.root.destroy()


def run(folder: str = "") -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    root.title(f"RenameLedger {__version__}")
    root.minsize(760, 480)
    assets = Path(__file__).resolve().parent / "assets"
    try:
        if sys.platform == "win32":
            root.iconbitmap(default=str(assets / "renameledger.ico"))
        else:
            root.iconphoto(True, tk.PhotoImage(file=str(assets / "renameledger.png")))
    except tk.TclError:
        pass
    if "vista" in ttk.Style().theme_names():
        ttk.Style().theme_use("vista")
    App(root, folder or (sys.argv[1] if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir() else ""))
    root.mainloop()


if __name__ == "__main__":
    run()
