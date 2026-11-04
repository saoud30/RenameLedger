import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from renameledger import cli, core


class RecursiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.ledger = self.base / "record.csv"

    def tearDown(self):
        self.temp.cleanup()

    def make(self, name, content=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content is not None else name, encoding="utf-8")
        return path

    def test_root_only_remains_default(self):
        self.make("top.txt"); self.make("sub/child.txt")
        items = core.plan(self.root, "x")
        self.assertEqual([i.old_name for i in items], ["top.txt"])
        core.apply(self.root, items, self.ledger)
        self.assertTrue((self.root / "sub/child.txt").exists())

    def test_nested_duplicate_names_and_restore(self):
        names = ["same.TXT", "a/same.TXT", "a/deep/same.TXT", "b/same.TXT"]
        for name in names: self.make(name)
        items = core.plan(self.root, "n", recursive=True, lowercase_ext=True)
        self.assertEqual(len(items), 4)
        self.assertEqual({i.old_name for i in items}, set(names))
        for i in items:
            self.assertEqual(Path(i.old_name).parent, Path(i.new_name).parent)
            self.assertTrue(i.new_name.endswith(".txt"))
        core.apply(self.root, items, self.ledger)
        self.assertEqual({(self.root / i.new_name).read_text(encoding="utf-8") for i in items}, set(names))
        with open(self.ledger, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual({r["old_name"] for r in rows}, set(names))
        core.undo(self.ledger)
        for name in names:
            self.assertEqual((self.root / name).read_text(encoding="utf-8"), name)
        self.assertTrue((self.root / "a/deep").is_dir())

    def test_global_order_filter_and_collision_skip_per_directory(self):
        self.make("sub/scan10.PDF"); self.make("sub/scan2.PDF"); self.make("sub/ignore.jpg")
        (self.root / "sub/x_001.PDF").mkdir()
        items = core.plan(self.root, "x", extensions=[" pdf "], recursive=True)
        self.assertEqual([i.old_name for i in items], ["sub/scan2.PDF", "sub/scan10.PDF"])
        self.assertEqual([i.new_name for i in items], ["sub/x_002.PDF", "sub/x_003.PDF"])

    def test_modified_sort_and_numbering_across_directories(self):
        a = self.make("a/z.txt"); b = self.make("b/a.txt")
        os.utime(a, (200, 200)); os.utime(b, (100, 100))
        items = core.plan(self.root, "n", start=7, digits=2, separator="-", sort="modified", recursive=True)
        self.assertEqual([i.new_name for i in items], ["b/n-07.txt", "a/n-08.txt"])

    def test_old_format_ledger_restores(self):
        self.make("new.txt", "original content")
        with open(self.ledger, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=core.LEDGER_FIELDS); w.writeheader()
            w.writerow(dict(batch_id="abc", timestamp="2020-01-01", folder=str(self.root),
                            old_name="old.txt", new_name="new.txt", size_bytes=16, status="renamed"))
        core.undo(self.ledger)
        self.assertEqual((self.root / "old.txt").read_text(), "original content")

    def test_missing_file_and_directory_are_skipped(self):
        self.make("a/one.txt"); self.make("b/two.txt"); self.make("c/three.txt")
        items = core.plan(self.root, "x", recursive=True); core.apply(self.root, items, self.ledger)
        (self.root / items[0].new_name).unlink(); (self.root / "a").rmdir()
        (self.root / items[1].new_name).unlink()
        _, undo = core.plan_undo(self.ledger)
        self.assertEqual([x.status for x in undo], ["missing", "missing", "ready"])
        core.undo(self.ledger)
        self.assertTrue((self.root / "c/three.txt").exists())
        self.assertFalse((self.root / "a").exists())

    def test_new_collision_after_preview_does_not_overwrite(self):
        self.make("sub/a.txt")
        items = core.plan(self.root, "x", recursive=True)
        self.make(items[0].new_name, "other")
        with self.assertRaisesRegex(core.RenameError, "Destination already exists"):
            core.apply(self.root, items, self.ledger)
        self.assertEqual((self.root / items[0].new_name).read_text(), "other")
        self.assertTrue((self.root / "sub/a.txt").exists())
        self.assertFalse(self.ledger.exists())

    def test_restore_collision_does_not_overwrite(self):
        self.make("sub/a.txt", "original")
        core.apply(self.root, core.plan(self.root, "x", recursive=True), self.ledger)
        self.make("sub/a.txt", "other")
        _, preview = core.plan_undo(self.ledger)
        self.assertEqual([i.status for i in preview], ["conflict"])
        with self.assertRaises(core.RenameError): core.undo(self.ledger)
        self.assertEqual((self.root / "sub/a.txt").read_text(), "other")
        self.assertEqual((self.root / "sub/x_001.txt").read_text(), "original")

    def test_existing_record_is_never_overwritten(self):
        self.make("a.txt"); self.ledger.write_text("keep")
        with self.assertRaisesRegex(core.RenameError, "Ledger already exists"):
            core.apply(self.root, core.plan(self.root, "x"), self.ledger)
        self.assertEqual(self.ledger.read_text(), "keep")
        self.assertTrue((self.root / "a.txt").exists())

    def test_rollback_across_nested_folders(self):
        for name in ("a/a.txt", "b/b.txt", "c/c.txt"): self.make(name)
        items = core.plan(self.root, "x", recursive=True)
        real = core._move; calls = []
        def fail_once(src, dst):
            calls.append((src, dst))
            if len(calls) == 5: raise PermissionError("in use")
            real(src, dst)
        with mock.patch.object(core, "_move", fail_once):
            with self.assertRaisesRegex(core.RenameError, "rolled back"):
                core.apply(self.root, items, self.ledger)
        for name in ("a/a.txt", "b/b.txt", "c/c.txt"): self.assertEqual((self.root / name).read_text(), name)
        self.assertFalse(any(p.name.startswith(core._TMP_PREFIX) for p in self.root.rglob("*")))

    def test_rollback_cycle_uses_temporary_names(self):
        self.make("a.txt", "A"); self.make("b.txt", "B"); self.make("c.txt", "C")
        items = [core.Item("a.txt", "b.txt"), core.Item("b.txt", "a.txt"), core.Item("c.txt", "d.txt")]
        real = core._move; calls = []
        def fail_once(src, dst):
            calls.append(1)
            if len(calls) == 6: raise OSError("failure")
            real(src, dst)
        with mock.patch.object(core, "_move", fail_once):
            with self.assertRaises(core.RenameError): core.apply(self.root, items, self.ledger)
        for name, content in (("a.txt", "A"), ("b.txt", "B"), ("c.txt", "C")):
            self.assertEqual((self.root / name).read_text(), content)

    def test_incomplete_rollback_is_reported_as_pending(self):
        self.make("a.txt"); self.make("b.txt")
        items = core.plan(self.root, "x")
        real = core._move; count = []
        def fail_twice(src, dst):
            count.append(1)
            if len(count) in (3, 4): raise OSError("blocked")
            real(src, dst)
        with mock.patch.object(core, "_move", fail_twice):
            with self.assertRaisesRegex(core.RenameError, "incomplete"):
                core.apply(self.root, items, self.ledger)
        with open(self.ledger, encoding="utf-8-sig", newline="") as f:
            self.assertEqual({r["status"] for r in csv.DictReader(f)}, {"pending"})

    def test_rejects_path_traversal_and_absolute_paths(self):
        self.make("a.txt")
        outside = self.base / "outside.txt"; outside.write_text("safe")
        for name in ("../outside.txt", "/outside.txt", "C:\\outside.txt", "sub/../../outside.txt"):
            with self.assertRaises(core.RenameError, msg=name):
                core.apply(self.root, [core.Item("a.txt", name)], self.ledger)
        self.assertEqual(outside.read_text(), "safe")
        self.assertTrue((self.root / "a.txt").exists())

    def test_rejects_cross_folder_move_and_duplicate_source(self):
        self.make("a/a.txt"); self.make("b/b.txt")
        with self.assertRaises(core.RenameError):
            core.apply(self.root, [core.Item("a/a.txt", "b/a.txt")], self.ledger)
        with self.assertRaises(core.RenameError):
            core.apply(self.root, [core.Item("a/a.txt", "a/x.txt"), core.Item("a/a.txt", "a/y.txt")], self.ledger)

    def test_symlinks_are_skipped_and_rejected_on_undo(self):
        outside = self.base / "outside"; outside.mkdir(); (outside / "a.txt").write_text("outside")
        try: (self.root / "linked").symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest("Symlink creation unavailable")
        self.make("a.txt")
        self.assertEqual([i.old_name for i in core.plan(self.root, "x", recursive=True)], ["a.txt"])
        with self.assertRaises(core.RenameError):
            core.apply(self.root, [core.Item("linked/a.txt", "linked/b.txt")], self.ledger)
        self.assertEqual((outside / "a.txt").read_text(), "outside")

    def test_progress_counts_finalized_files(self):
        self.make("a.txt"); self.make("sub/b.txt")
        values = []
        core.apply(self.root, core.plan(self.root, "x", recursive=True), self.ledger,
                   lambda done, total: values.append((done, total)))
        self.assertEqual(values, [(0, 2), (1, 2), (2, 2)])

    def test_callback_error_does_not_break_operation(self):
        self.make("a.txt")
        def broken(*args): raise RuntimeError("UI closed")
        core.apply(self.root, core.plan(self.root, "x"), self.ledger, broken)
        self.assertTrue((self.root / "x_001.txt").exists())

    def test_cli_recursive_preview_rename_and_undo(self):
        self.make("a.txt"); self.make("sub/a.txt")
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(cli.main(["preview", str(self.root), "--prefix", "x", "--recursive"]), 0)
            self.assertEqual(cli.main(["rename", str(self.root), "--prefix", "x", "--recursive", "--ledger", str(self.ledger), "-y"]), 0)
            self.assertEqual(cli.main(["undo", str(self.ledger), "-y"]), 0)
        self.assertTrue((self.root / "a.txt").exists()); self.assertTrue((self.root / "sub/a.txt").exists())

    def test_record_update_failure_preserves_pending_record(self):
        self.make("a.txt")
        with mock.patch.object(core.os, "replace", side_effect=PermissionError("blocked")):
            with self.assertRaisesRegex(core.RenameError, "Files were renamed"):
                core.apply(self.root, core.plan(self.root, "x"), self.ledger)
        self.assertTrue((self.root / "x_001.txt").exists())
        with open(self.ledger, encoding="utf-8-sig", newline="") as f:
            self.assertEqual([r["status"] for r in csv.DictReader(f)], ["pending"])
        self.assertEqual(list(self.base.glob(".renameledger-record-*")), [])

    def test_rejects_malicious_ledger(self):
        outside = self.base / "outside.txt"; outside.write_text("safe")
        with open(self.ledger, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=core.LEDGER_FIELDS); w.writeheader()
            w.writerow(dict(batch_id="a", timestamp="now", folder=str(self.root), old_name="x.txt",
                            new_name="../outside.txt", size_bytes=4, status="renamed"))
        with self.assertRaises(core.RenameError): core.undo(self.ledger)
        self.assertEqual(outside.read_text(), "safe")


if __name__ == "__main__": unittest.main()
