[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
param([string]$ExePath)
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('ExePath')) {
    $ExePath = Join-Path $PSScriptRoot 'dist\RenameLedger.exe'
}
. (Join-Path $PSScriptRoot 'tools\explorer-menu.ps1')
if ($env:OS -ne 'Windows_NT') { throw 'Explorer integration requires Windows.' }
if (-not $ExePath -or -not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "RenameLedger.exe was not found: $ExePath. Build it first or pass -ExePath with the full executable path."
}
$exe = (Resolve-Path -LiteralPath $ExePath).ProviderPath
if ([IO.Path]::GetFileName($exe) -ine 'RenameLedger.exe') { throw 'Choose the standalone RenameLedger.exe.' }
$command = Get-RenameLedgerCommand $exe
Write-Host "Executable: $exe"
$fullKey = 'HKCU:\' + $script:MenuParent + '\' + $script:MenuKeyName
# A per-user verb would shadow a same-named machine-wide verb in the merged classes view.
foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
    $machine = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
    try {
        $machineKey = $machine.OpenSubKey($script:MenuParent + '\' + $script:MenuKeyName, $false)
        if ($null -ne $machineKey) {
            $machineKey.Dispose()
            throw 'A machine-wide RenameLedger menu key already exists. Nothing was changed; inspect it first.'
        }
    } finally { $machine.Dispose() }
}
$parent = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:MenuParent, $false)
if ($null -ne $parent) {
    try {
        $existing = $parent.OpenSubKey($script:MenuKeyName, $false)
        if ($null -ne $existing) { $existing.Dispose(); throw "The key already exists: $fullKey. Nothing was changed." }
    } finally { $parent.Dispose() }
}
Write-Host "Creates: $fullKey and its command subkey"
Write-Host "Command: $command"
Write-Host 'This affects only the current user. Existing keys are not overwritten.'
if ($PSCmdlet.ShouldProcess($fullKey, 'Create the Explorer folder menu')) {
    if ((Read-Host 'Type INSTALL to confirm; anything else cancels') -cne 'INSTALL') { Write-Host 'Cancelled. Nothing changed.'; return }
    $parent = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:MenuParent)
    try { Install-RenameLedgerMenu $parent $exe } finally { $parent.Dispose() }
    Write-Host 'Installed. On Windows 11, right-click a folder and choose Show more options.'
}
