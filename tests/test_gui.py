"""Real Tk smoke tests run when Tk and a display are available (including Windows)."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    import tkinter as tk
    from renameledger import gui
except ImportError:
    tk = None


@unittest.skipIf(tk is None, "Tkinter is not installed")
class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"Tk display unavailable: {e}")
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.files = self.base / "files"; self.files.mkdir()
        (self.files / "a.txt").write_text("A")
        (self.files / "sub").mkdir(); (self.files / "sub/b.txt").write_text("B")
        self.app = gui.App(self.root, str(self.files))

    def tearDown(self):
        self.root.update_idletasks()
        self.root.destroy(); self.temp.cleanup()

    def wait_for(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.root.update()
            if predicate(): return
            time.sleep(0.01)
        self.fail("Timed out waiting for GUI state")

    def test_folder_argument_with_spaces_and_invalid_folder(self):
        target = self.base / "Folder with spaces"
        target.mkdir()
        (target / "one.txt").write_text("one")
        self.app.folder.set(str(target))
        self.wait_for(lambda: len(self.app.items) == 1 and self.app._preview_ready)
        self.assertEqual(self.app.folder.get(), str(target))
        self.assertEqual(self.app.items[0].old_name, "one.txt")
        for bad in (self.base / "missing folder", target / "one.txt"):
            self.app.folder.set(str(bad))
            self.wait_for(lambda: "Folder not found" in self.app.status.get())
            self.assertEqual(self.app.folder.get(), str(bad))
            self.assertEqual(str(self.app.go["state"]), "disabled")

    def test_run_forwards_argument_and_keeps_no_argument_empty(self):
        fake_root = mock.MagicMock()
        with mock.patch.object(gui.tk, "Tk", return_value=fake_root), \
             mock.patch.object(gui.tk, "PhotoImage"), \
             mock.patch.object(gui.ttk, "Style") as style, \
             mock.patch.object(gui, "App") as app:
            style.return_value.theme_names.return_value = []
            for args, expected in ((["RenameLedger.exe"], ""),
                                   (["RenameLedger.exe", str(self.base / "Folder with spaces")], str(self.base / "Folder with spaces")),
                                   (["RenameLedger.exe", str(self.base / "missing")], str(self.base / "missing"))):
                with mock.patch.object(gui.sys, "argv", args):
                    gui.run()
                    app.assert_called_with(fake_root, expected)
            with mock.patch.object(gui.sys, "argv", ["renameledger", "gui"]):
                gui.run("")
                app.assert_called_with(fake_root, "")

    def test_cli_gui_command_does_not_use_subcommand_as_folder(self):
        from renameledger import cli
        with mock.patch.object(gui, "run") as run:
            self.assertEqual(cli.main(["gui"]), 0)
            run.assert_called_once_with("")

    def test_preview_updates_for_recursive_and_invalid_options(self):
        self.wait_for(lambda: len(self.app.items) == 1 and str(self.app.go["state"]) == "normal")
        self.app.recursive.set(True)
        self.wait_for(lambda: len(self.app.items) == 2 and str(self.app.go["state"]) == "normal")
        self.app.digits.set("bad")
        self.wait_for(lambda: "whole numbers" in self.app.status.get())
        self.assertEqual(str(self.app.go["state"]), "disabled")
        self.app.digits.set("3")
        self.wait_for(lambda: len(self.app.items) == 2 and str(self.app.go["state"]) == "normal")

    def test_visible_checkbox_toggles_and_renames_exact_preview(self):
        # Reproduce the reported folder structure, including duplicate basenames.
        tree = self.base / "RenameLedger-Recursive-Test"
        names = ["root-file.pdf", "Documents/document.docx", "Documents/Reports/scan.pdf",
                 "Images/photo.jpg", "Images/scan.pdf"]
        for name in names:
            p = tree / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(name, encoding="utf-8")
        self.root.geometry("760x480")
        self.root.deiconify()
        self.app.folder.set(str(tree))
        self.wait_for(lambda: len(self.app.items) == 1 and self.app._preview_ready)
        check = self.app.include_subfolders
        self.assertEqual(str(check.cget("text")), "Include subfolders")
        self.assertTrue(check.winfo_ismapped())
        self.assertGreater(check.winfo_width(), 1)
        x = check.winfo_rootx() - self.root.winfo_rootx()
        y = check.winfo_rooty() - self.root.winfo_rooty()
        self.assertGreaterEqual(x, 0); self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + check.winfo_width(), self.root.winfo_width())
        self.assertLessEqual(y + check.winfo_height(), self.root.winfo_height())
        def shown_paths():
            return {str(self.app.tree.item(row, "values")[0]) for row in self.app.tree.get_children()}
        self.assertEqual(shown_paths(), {"root-file.pdf"})
        check.invoke()
        self.wait_for(lambda: len(self.app.items) == 5 and self.app._preview_ready)
        self.assertEqual(shown_paths(), set(names))
        check.invoke()
        self.wait_for(lambda: len(self.app.items) == 1 and self.app._preview_ready)
        self.assertEqual(shown_paths(), {"root-file.pdf"})
        check.invoke()
        self.wait_for(lambda: len(self.app.items) == 5 and self.app._preview_ready)
        preview = {str(self.app.tree.item(row, "values")[0]): str(self.app.tree.item(row, "values")[1])
                   for row in self.app.tree.get_children()}
        entered, release = threading.Event(), threading.Event()
        real_move = gui.core._move
        def slow(src, dst):
            if not entered.is_set():
                entered.set(); release.wait(5)
            real_move(src, dst)
        try:
            with mock.patch.object(gui.core, "_move", slow), \
                 mock.patch.object(gui.core, "default_ledger_dir", return_value=self.base / "records"), \
                 mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                 mock.patch.object(gui.messagebox, "showinfo"), \
                 mock.patch.object(gui.messagebox, "showerror") as error:
                self.app.go.invoke()
                self.wait_for(entered.is_set)
                self.assertTrue(self.app.recursive.get())
                self.assertTrue(check.instate(["disabled"]))
                release.set()
                self.wait_for(lambda: not self.app._busy)
                self.assertTrue(self.app.recursive.get())
                error.assert_not_called()
                for old, new in preview.items():
                    self.assertEqual((tree / new).read_text(encoding="utf-8"), old)
                records = list((self.base / "records").glob("*.csv"))
                self.assertEqual(len(records), 1)
                _, rows = gui.core.read_ledger(records[0])
                self.assertEqual({r["old_name"]: r["new_name"] for r in rows}, preview)
                with mock.patch.object(gui.filedialog, "askopenfilename", return_value=str(records[0])):
                    self.app._load_undo()
                    self.wait_for(lambda: self.app.undo_ledger is not None and self.app._preview_ready)
                    self.app.go.invoke()
                    self.wait_for(lambda: not self.app._busy)
                for name in names:
                    self.assertEqual((tree / name).read_text(encoding="utf-8"), name)
                self.assertTrue(self.app.recursive.get())
                error.assert_not_called()
        finally:
            release.set()

    def test_rename_disabled_until_all_preview_rows_are_rendered(self):
        for n in range(155):
            (self.files / f"extra{n:03}.txt").write_text(str(n))
        original_after = self.app.after
        def delayed(ms, callback=None, *args):
            return original_after(300 if ms == 1 else ms, callback, *args)
        with mock.patch.object(self.app, "after", side_effect=delayed), \
             mock.patch.object(gui.messagebox, "askyesno") as confirm:
            self.app.recursive.set(True)
            self.wait_for(lambda: self.app._render_after is not None)
            self.assertLess(len(self.app.tree.get_children()), len(self.app.items))
            self.assertFalse(self.app._preview_ready)
            self.assertEqual(str(self.app.go["state"]), "disabled")
            self.app._run()
            confirm.assert_not_called()
            self.wait_for(lambda: self.app._preview_ready)
            self.assertEqual(len(self.app.tree.get_children()), len(self.app.items))
            self.assertEqual(str(self.app.go["state"]), "normal")

    def test_operation_runs_off_ui_thread_with_progress(self):
        self.app.recursive.set(True)
        self.wait_for(lambda: len(self.app.items) == 2 and str(self.app.go["state"]) == "normal")
        entered, release = threading.Event(), threading.Event()
        real = gui.core._move
        def slow(src, dst):
            if not entered.is_set():
                entered.set(); release.wait(5)
            real(src, dst)
        try:
            with mock.patch.object(gui.core, "_move", slow), \
                 mock.patch.object(gui.core, "default_ledger_dir", return_value=self.base / "records"), \
                 mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                 mock.patch.object(gui.messagebox, "showinfo"), \
                 mock.patch.object(gui.messagebox, "showerror") as error:
                self.app._run()
                self.wait_for(entered.is_set)
                self.assertTrue(self.app._busy)
                self.assertIn("remaining: 2", self.app.status.get())
                beat = []; self.root.after(10, lambda: beat.append(True))
                self.wait_for(lambda: bool(beat))  # event loop runs while the move is blocked
                release.set()
                self.wait_for(lambda: not self.app._busy)
                self.assertIn("Renamed 2", self.app.status.get())
                self.assertEqual(float(self.app.progress["value"]), 100.0)
                error.assert_not_called()
        finally:
            release.set()

    def test_restore_conflict_preview_and_success(self):
        ledger = self.base / "record.csv"
        gui.core.apply(self.files, gui.core.plan(self.files, "renamed", recursive=True), ledger)
        (self.files / "a.txt").write_text("occupied")
        with mock.patch.object(gui.filedialog, "askopenfilename", return_value=str(ledger)), \
             mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
             mock.patch.object(gui.messagebox, "showinfo"), \
             mock.patch.object(gui.messagebox, "showerror") as error:
            self.app._load_undo()
            self.wait_for(lambda: self.app.undo_ledger is not None)
            self.assertIn("1 conflicts", self.app.status.get())
            self.assertEqual(str(self.app.go["state"]), "disabled")
            (self.files / "a.txt").unlink()
            self.app._load_undo()
            self.wait_for(lambda: str(self.app.go["state"]) == "normal")
            self.app._run()
            self.wait_for(lambda: not self.app._busy)
            self.assertEqual((self.files / "a.txt").read_text(), "A")
            self.assertEqual((self.files / "sub/b.txt").read_text(), "B")
            self.assertIn("Restored 2", self.app.status.get())
            error.assert_not_called()

    def test_outdated_preview_is_ignored(self):
        entered, release = threading.Event(), threading.Event()
        real = gui.core.plan
        def slow(*args, **kwargs):
            if args[1] == "file":
                entered.set(); release.wait(5)
            return real(*args, **kwargs)
        try:
            with mock.patch.object(gui.core, "plan", slow):
                self.wait_for(entered.is_set)
                self.app.prefix.set("new")
                self.wait_for(lambda: bool(self.app.items) and self.app.items[0].new_name.startswith("new_"))
                release.set()
                end = time.monotonic() + 0.3
                while time.monotonic() < end:
                    self.root.update(); time.sleep(0.01)
                self.assertTrue(self.app.items[0].new_name.startswith("new_"))
        finally:
            release.set()


if __name__ == "__main__": unittest.main()
