# Double-click entry point (and PyInstaller entry). Works without installing the package.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from renameledger.gui import run

run()
