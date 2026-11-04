import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from renameledger import cli, core  # noqa: E402


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir = self.root / "files"
        self.dir.mkdir()
        self.ledger = self.root / "ledger.csv"

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, *names):
        for n in names:
            (self.dir / n).write_text(n)

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_rename_writes_ledger_and_keeps_contents(self):
        self.make("b.pdf", "a.pdf", "img10.jpg", "img2.jpg")
        items = core.plan(self.dir, "sample")
        self.assertEqual([i.new_name for i in items], ["sample_001.pdf", "sample_002.pdf", "sample_003.jpg", "sample_004.jpg"])
        self.assertEqual(items[2].old_name, "img2.jpg")  # natural sort
        core.apply(self.dir, items, self.ledger)
        self.assertEqual((self.dir / "sample_003.jpg").read_text(), "img2.jpg")
        with open(self.ledger, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual({r["status"] for r in rows}, {"renamed"})

    def test_undo_restores_everything(self):
        self.make("one.txt", "two.txt")
        core.apply(self.dir, core.plan(self.dir, "x"), self.ledger)
        core.undo(self.ledger)
        self.assertEqual(self.names(), ["one.txt", "two.txt"])
        self.assertEqual((self.dir / "one.txt").read_text(), "one.txt")

    def test_skips_names_used_by_folders(self):
        self.make("a.pdf", "b.pdf")
        (self.dir / "s_001.pdf").mkdir()  # a folder with the target name is not part of the batch
        items = core.plan(self.dir, "s")
        self.assertEqual([i.new_name for i in items], ["s_002.pdf", "s_003.pdf"])
        core.apply(self.dir, items, self.ledger)
        self.assertTrue((self.dir / "s_001.pdf").is_dir())

    def test_rerun_on_already_renamed_files_swaps_safely(self):
        self.make("s_001.txt", "s_002.txt")
        items = core.plan(self.dir, "s", start=2)  # s_001->s_002, s_002->s_003
        core.apply(self.dir, items, self.ledger)
        self.assertEqual((self.dir / "s_002.txt").read_text(), "s_001.txt")
        self.assertEqual((self.dir / "s_003.txt").read_text(), "s_002.txt")

    def test_unchanged_files_are_left_alone(self):
        self.make("s_001.txt")
        self.assertEqual(core.plan(self.dir, "s")[0].status, "unchanged")
        with self.assertRaises(core.RenameError):
            core.apply(self.dir, core.plan(self.dir, "s"), self.ledger)

    def test_failure_rolls_back(self):
        self.make("a.txt", "b.txt", "c.txt")
        items = core.plan(self.dir, "z")
        real, calls = core._move, []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) == 5:  # fail in the middle of phase two
                raise PermissionError("file in use")
            real(src, dst)

        with mock.patch("renameledger.core._move", flaky):
            with self.assertRaises(core.RenameError):
                core.apply(self.dir, items, self.ledger)
        self.assertEqual(self.names(), ["a.txt", "b.txt", "c.txt"])
        with open(self.ledger, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual({r["status"] for r in rows}, {"failed"})

    def test_refuses_if_target_appears_after_preview(self):
        self.make("a.txt")
        items = core.plan(self.dir, "n")
        self.make("n_001.txt")
        with self.assertRaises(core.RenameError):
            core.apply(self.dir, items, self.ledger)
        self.assertEqual((self.dir / "n_001.txt").read_text(), "n_001.txt")

    def test_bad_prefixes(self):
        self.make("a.txt")
        for bad in ["", "a/b", "x?", "CON", "dot."]:
            with self.assertRaises(core.RenameError, msg=bad):
                core.plan(self.dir, bad)

    def test_undo_skips_missing_files(self):
        self.make("a.txt", "b.txt")
        core.apply(self.dir, core.plan(self.dir, "m"), self.ledger)
        (self.dir / "m_001.txt").unlink()
        folder, items = core.plan_undo(self.ledger)
        self.assertEqual([i.status for i in items], ["missing", "ready"])
        core.undo(self.ledger)
        self.assertEqual(self.names(), ["b.txt"])

    def test_cli_rename_and_undo(self):
        self.make("a.txt", "b.txt")
        with mock.patch("sys.stdout"):
            self.assertEqual(cli.main(["rename", str(self.dir), "--prefix", "c", "--ledger", str(self.ledger), "-y"]), 0)
            self.assertEqual(self.names(), ["c_001.txt", "c_002.txt"])
            self.assertEqual(cli.main(["undo", str(self.ledger), "-y"]), 0)
        self.assertEqual(self.names(), ["a.txt", "b.txt"])
        with mock.patch("sys.stderr"):
            self.assertEqual(cli.main(["preview", str(self.root / "nope"), "--prefix", "c"]), 2)


if __name__ == "__main__":
    unittest.main()
