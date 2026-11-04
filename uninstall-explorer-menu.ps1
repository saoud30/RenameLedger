[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'tools\explorer-menu.ps1')
if ($env:OS -ne 'Windows_NT') { throw 'Explorer integration requires Windows.' }
$fullKey = 'HKCU:\' + $script:MenuParent + '\' + $script:MenuKeyName
$parent = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:MenuParent, $true)
if ($null -eq $parent) { Write-Host 'RenameLedger Explorer menu is not installed. Nothing changed.'; return }
try {
    $existing = $parent.OpenSubKey($script:MenuKeyName, $false)
    if ($null -eq $existing) { Write-Host 'RenameLedger Explorer menu is not installed. Nothing changed.'; return }
    $existing.Dispose()
    if (-not (Test-RenameLedgerMenu $parent)) {
        throw "The key is not an unchanged RenameLedger registration: $fullKey. Nothing was removed; inspect it manually."
    }
    Write-Host "Removes: $fullKey and its command subkey; parent keys remain."
    if ($PSCmdlet.ShouldProcess($fullKey, 'Remove the Explorer folder menu')) {
        if ((Read-Host 'Type REMOVE to confirm; anything else cancels') -cne 'REMOVE') { Write-Host 'Cancelled. Nothing changed.'; return }
        [void](Uninstall-RenameLedgerMenu $parent)
        Write-Host 'Removed for the current user.'
    }
} finally { $parent.Dispose() }
