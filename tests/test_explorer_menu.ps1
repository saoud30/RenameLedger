$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\tools\explorer-menu.ps1')

class FakeKey {
    [hashtable]$Values = @{}
    [hashtable]$Kinds = @{}
    [hashtable]$Children = @{}
    [FakeKey] OpenSubKey([string]$name, [bool]$writable) {
        if ($this.Children.ContainsKey($name)) { return $this.Children[$name] }
        return $null
    }
    [FakeKey] CreateSubKey([string]$name) {
        if (-not $this.Children.ContainsKey($name)) { $this.Children[$name] = [FakeKey]::new() }
        return $this.Children[$name]
    }
    [void] SetValue([string]$name, [object]$value, [Microsoft.Win32.RegistryValueKind]$kind) {
        $this.Values[$name] = $value; $this.Kinds[$name] = $kind
    }
    [object] GetValue([string]$name) { return $this.Values[$name] }
    [string[]] GetValueNames() { return [string[]]@($this.Values.Keys) }
    [string[]] GetSubKeyNames() { return [string[]]@($this.Children.Keys) }
    [Microsoft.Win32.RegistryValueKind] GetValueKind([string]$name) { return $this.Kinds[$name] }
    [void] DeleteSubKeyTree([string]$name, [bool]$throwOnMissing) { $this.Children.Remove($name) }
    [void] Dispose() { }
}
function Assert-True([bool]$value, [string]$message) { if (-not $value) { throw $message } }
function Assert-Fails([scriptblock]$action, [string]$message) {
    $failed = $false
    try { & $action } catch { $failed = $true }
    if (-not $failed) { throw $message }
}
function Exercise-Registration($parent, [string]$exe) {
    $expected = '"C:\Program Files\Rename Ledger\RenameLedger.exe" "%1"'
    Assert-True ((Get-RenameLedgerCommand $exe) -ceq $expected) 'The command is not correctly quoted.'
    Install-RenameLedgerMenu $parent $exe
    Assert-True (Test-RenameLedgerMenu $parent) 'Installed registration did not validate.'
    $key = $parent.OpenSubKey('RenameLedger', $true)
    $cmd = $key.OpenSubKey('command', $true)
    Assert-True ($cmd.GetValue('') -ceq $expected) 'The registered command differs from the expected command.'
    Assert-Fails { Install-RenameLedgerMenu $parent $exe } 'An existing key was overwritten.'
    Assert-True ($cmd.GetValue('') -ceq $expected) 'The existing command changed.'
    $cmd.SetValue('', 'other application', [Microsoft.Win32.RegistryValueKind]::String)
    Assert-Fails { Uninstall-RenameLedgerMenu $parent } 'A modified command was removed.'
    Assert-True ($cmd.GetValue('') -ceq 'other application') 'The modified command was overwritten.'
    $cmd.SetValue('', $expected, [Microsoft.Win32.RegistryValueKind]::String)
    $key.SetValue('OtherTool', 'keep', [Microsoft.Win32.RegistryValueKind]::String)
    Assert-Fails { Uninstall-RenameLedgerMenu $parent } 'An unrelated registry value was removed.'
    Assert-True ($key.GetValue('OtherTool') -ceq 'keep') 'An unrelated registry value changed.'
    $key.DeleteValue('OtherTool')
    $extra = $key.CreateSubKey('other'); $extra.Dispose()
    Assert-Fails { Uninstall-RenameLedgerMenu $parent } 'An unrelated registry subkey was removed.'
    $key.DeleteSubKeyTree('other', $false)
    $cmd.Dispose(); $key.Dispose()
    Assert-True (Uninstall-RenameLedgerMenu $parent) 'Expected owned key to be removed.'
    Assert-True (-not (Uninstall-RenameLedgerMenu $parent)) 'Missing key was not handled.'
    $unrelated = $parent.CreateSubKey('RenameLedger')
    $unrelated.SetValue('', 'unrelated', [Microsoft.Win32.RegistryValueKind]::String)
    $unrelated.Dispose()
    Assert-Fails { Install-RenameLedgerMenu $parent $exe } 'An unrelated key was overwritten.'
    Assert-Fails { Uninstall-RenameLedgerMenu $parent } 'An unrelated key was removed.'
    $check = $parent.OpenSubKey('RenameLedger', $false)
    Assert-True ($check.GetValue('') -ceq 'unrelated') 'An unrelated key changed.'
    $check.Dispose()
    $parent.DeleteSubKeyTree('RenameLedger', $false)
}
# Fake API mirrors the .NET registry methods; no real Explorer key is touched.
Update-TypeData -TypeName FakeKey -MemberType ScriptMethod -MemberName DeleteValue -Value {
    param([string]$name)
    $this.Values.Remove($name); $this.Kinds.Remove($name)
} -Force
$exe = 'C:\Program Files\Rename Ledger\RenameLedger.exe'
Assert-Fails { Get-RenameLedgerCommand 'C:\bad%1\RenameLedger.exe' } 'Percent sign was not rejected.'
Assert-Fails { Get-RenameLedgerCommand 'C:\bad"name\RenameLedger.exe' } 'Quote was not rejected.'
Exercise-Registration ([FakeKey]::new()) $exe
# On Windows, repeat against a disposable HKCU test key, never the Explorer key.
if ($env:OS -eq 'Windows_NT') {
    $testName = 'Software\RenameLedger-test-' + [guid]::NewGuid().ToString('N')
    $parent = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($testName)
    try { Exercise-Registration $parent $exe }
    finally { $parent.Dispose(); [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($testName, $false) }
}
Write-Output 'Explorer registry helper tests passed.'
