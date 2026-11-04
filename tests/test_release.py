import re
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "renameledger" / "assets"


class ReleaseFileTests(unittest.TestCase):
    def test_version_file_matches_package_version(self):
        version = re.search(r'__version__ = "([\d.]+)"', (ROOT / "src/renameledger/__init__.py").read_text()).group(1)
        text = (ROOT / "version_info.txt").read_text()
        quad = "(" + ", ".join((version.split(".") + ["0"] * 4)[:4]) + ")"
        self.assertEqual(text.count(f"filevers={quad}") + text.count(f"prodvers={quad}"), 2)
        self.assertIn(f"'FileVersion', '{version}'", text)
        self.assertIn(f"'ProductVersion', '{version}'", text)

    def test_icon_has_small_and_large_sizes(self):
        data = (ASSETS / "renameledger.ico").read_bytes()
        _, kind, count = struct.unpack_from("<HHH", data)
        sizes = {data[6 + 16 * i] or 256 for i in range(count)}  # 0 means 256 in ICO headers
        self.assertEqual(kind, 1)
        self.assertTrue({16, 32, 48, 256} <= sizes, sizes)
        self.assertTrue((ASSETS / "renameledger.png").is_file())

    def test_both_builds_use_the_same_pyinstaller_options(self):
        bat = (ROOT / "build-exe.bat").read_text()
        yml = (ROOT / ".github/workflows/release.yml").read_text()
        for needed in ("pip install .", "pyinstaller", "--paths src", "--version-file version_info.txt",
                       "renameledger.ico", "--name RenameLedger"):
            self.assertIn(needed.lower(), bat.lower(), needed)
            self.assertIn(needed.lower(), yml.lower(), needed)
        for text in (bat, yml):
            self.assertIn('--add-data "src/renameledger/assets:renameledger/assets"', text.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
