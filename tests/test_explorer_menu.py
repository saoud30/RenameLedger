"""Run the PowerShell helper tests without touching Explorer's real registry key."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ExplorerScriptTests(unittest.TestCase):
    def test_registry_helpers(self):
        shell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not shell:
            self.skipTest("PowerShell is not installed")
        result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(ROOT / "tests/test_explorer_menu.ps1")],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertIn("Explorer registry helper tests passed.", result.stdout)

    def test_scripts_require_confirmation_and_scope_to_current_user(self):
        for name in ("install-explorer-menu.ps1", "uninstall-explorer-menu.ps1"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("SupportsShouldProcess = $true", text)
            self.assertIn("$PSCmdlet.ShouldProcess", text)
            self.assertIn("Read-Host", text)
            self.assertIn("Registry]::CurrentUser", text)
        installer = (ROOT / "install-explorer-menu.ps1").read_text(encoding="utf-8")
        self.assertIn("param([string]$ExePath)", installer)
        self.assertIn("$PSBoundParameters.ContainsKey('ExePath')", installer)
        library = (ROOT / "tools/explorer-menu.ps1").read_text(encoding="utf-8")
        self.assertIn(r"Software\Classes\Directory\shell", library)
        self.assertIn("RenameLedgerOwner", library)

    def _copy_scripts(self, directory):
        project = Path(directory) / "Project with spaces"
        (project / "tools").mkdir(parents=True)
        for name in ("install-explorer-menu.ps1", "uninstall-explorer-menu.ps1", "tools/explorer-menu.ps1"):
            shutil.copyfile(ROOT / name, project / name)
        elsewhere = Path(directory) / "Other working directory"
        elsewhere.mkdir()
        # Resolve existing directories before appending the possibly missing dist/EXE.
        return project.resolve(strict=True), elsewhere.resolve(strict=True)

    def _invoke_script(self, script, cwd, env=None):
        shell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not shell:
            self.skipTest("PowerShell is not installed")
        return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-WhatIf"],
                              cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)

    def test_default_path_missing_exe_from_other_working_directory(self):
        # The missing-file check occurs before any registry access; safe on all platforms.
        with tempfile.TemporaryDirectory(prefix="RenameLedger Path Test ") as folder:
            project, elsewhere = self._copy_scripts(folder)
            env = os.environ.copy()
            env["OS"] = "Windows_NT"
            result = self._invoke_script(project / "install-explorer-menu.ps1", elsewhere, env)
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RenameLedger.exe was not found", output)
            plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
            plain = re.sub(r"(?m)^\s*\|\s*", "", plain)
            expected = project / "dist" / "RenameLedger.exe"
            self.assertIn("".join(str(expected).split()).lower(), "".join(plain.split()).lower())
            self.assertNotIn("Cannot bind argument", output)

    def test_copy_scripts_resolves_directory_alias(self):
        with tempfile.TemporaryDirectory(prefix="RenameLedger Alias Test ") as folder:
            actual = Path(folder) / "Actual directory"
            actual.mkdir()
            alias = Path(folder) / "Alias"
            try:
                alias.symlink_to(actual, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Directory symlinks unavailable: {exc}")
            project, elsewhere = self._copy_scripts(alias)
            self.assertEqual(project, (actual / "Project with spaces").resolve(strict=True))
            self.assertEqual(elsewhere, (actual / "Other working directory").resolve(strict=True))

    @unittest.skipUnless(os.name == "nt", "Requires Windows short-path support")
    def test_copy_scripts_canonicalizes_windows_short_paths(self):
        import ctypes
        from ctypes import wintypes

        get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short_path.restype = wintypes.DWORD
        with tempfile.TemporaryDirectory(prefix="RenameLedger Path Test ") as folder:
            long_folder = str(Path(folder).resolve(strict=True))
            length = get_short_path(long_folder, None, 0)
            if not length:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_unicode_buffer(length)
            if not get_short_path(long_folder, buffer, length):
                raise ctypes.WinError(ctypes.get_last_error())
            short_folder = buffer.value
            if os.path.normcase(short_folder) == os.path.normcase(long_folder):
                self.skipTest("8.3 short names are unavailable for this temporary directory")
            project, elsewhere = self._copy_scripts(short_folder)
            self.assertEqual(project, (Path(long_folder) / "Project with spaces").resolve(strict=True))
            self.assertEqual(elsewhere, (Path(long_folder) / "Other working directory").resolve(strict=True))
            self.assertIn(" ", str(project))

    def test_both_scripts_initialize_from_another_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="RenameLedger Path Test ") as folder:
            project, elsewhere = self._copy_scripts(folder)
            env = os.environ.copy()
            env["OS"] = "not-windows"
            for name in ("install-explorer-menu.ps1", "uninstall-explorer-menu.ps1"):
                with self.subTest(script=name):
                    result = self._invoke_script(project / name, elsewhere, env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Explorer integration requires Windows", result.stdout + result.stderr)

    @unittest.skipUnless(os.name == "nt", "Requires Windows PowerShell and registry")
    def test_default_executable_whatif_from_another_working_directory(self):
        import winreg
        key = r"Software\Classes\Directory\shell\RenameLedger"
        def exists(hive, flag=0):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | flag):
                    return True
            except FileNotFoundError:
                return False
        if exists(winreg.HKEY_CURRENT_USER) or any(exists(winreg.HKEY_LOCAL_MACHINE, flag)
                for flag in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)):
            self.skipTest("An Explorer key already exists; it will not be touched")
        with tempfile.TemporaryDirectory(prefix="RenameLedger Path Test ") as folder:
            project, elsewhere = self._copy_scripts(folder)
            dist = project / "dist"
            dist.mkdir()
            exe = dist / "RenameLedger.exe"
            exe.touch()
            result = self._invoke_script(project / "install-explorer-menu.ps1", elsewhere)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            executable = re.search(r"(?m)^Executable: (.+)\r?$", output)
            command = re.search(r'(?m)^Command: "([^"\r\n]+)" "%1"\r?$', output)
            self.assertIsNotNone(executable, output)
            self.assertIsNotNone(command, output)
            expected = os.path.normcase(str(exe.resolve(strict=True)))
            self.assertEqual(expected, os.path.normcase(str(Path(executable.group(1)).resolve(strict=True))))
            self.assertEqual(expected, os.path.normcase(str(Path(command.group(1)).resolve(strict=True))))
            self.assertIn(" ", str(exe))
            self.assertFalse(exists(winreg.HKEY_CURRENT_USER))

    @unittest.skipUnless(os.name == "nt", "Requires Windows PowerShell and registry")
    def test_uninstaller_initializes_from_another_working_directory(self):
        import winreg
        key = r"Software\Classes\Directory\shell\RenameLedger"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                self.skipTest("An Explorer key already exists; it will not be touched")
        except FileNotFoundError:
            pass
        with tempfile.TemporaryDirectory(prefix="RenameLedger Path Test ") as folder:
            project, elsewhere = self._copy_scripts(folder)
            result = self._invoke_script(project / "uninstall-explorer-menu.ps1", elsewhere)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("not installed", output.lower())
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                    self.fail("Uninstaller unexpectedly created an Explorer key")
            except FileNotFoundError:
                pass

    @unittest.skipUnless(os.name == "nt", "Requires the Windows registry and PowerShell")
    def test_installer_rejects_missing_executable(self):
        shell = shutil.which("powershell.exe")
        with tempfile.TemporaryDirectory(prefix="RenameLedger Test ") as folder:
            exe = Path(folder) / "RenameLedger.exe"
            result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-File", str(ROOT / "install-explorer-menu.ps1"), "-ExePath", str(exe), "-WhatIf"],
                                    capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not found", (result.stderr + result.stdout).lower())

    @unittest.skipUnless(os.name == "nt", "Requires the Windows registry and PowerShell")
    def test_installer_whatif_does_not_create_explorer_key(self):
        import winreg
        key = r"Software\Classes\Directory\shell\RenameLedger"
        def exists(hive, flag=0):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | flag):
                    return True
            except FileNotFoundError:
                return False
        if exists(winreg.HKEY_CURRENT_USER) or any(exists(winreg.HKEY_LOCAL_MACHINE, flag)
                for flag in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)):
            self.skipTest("An Explorer key already exists; it will not be touched")
        shell = shutil.which("powershell.exe")
        with tempfile.TemporaryDirectory(prefix="RenameLedger Test ") as folder:
            exe = Path(folder) / "RenameLedger.exe"
            exe.touch()
            result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-File", str(ROOT / "install-explorer-menu.ps1"), "-ExePath", str(exe), "-WhatIf"],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
            self.assertFalse(exists(winreg.HKEY_CURRENT_USER))


if __name__ == "__main__":
    unittest.main()
