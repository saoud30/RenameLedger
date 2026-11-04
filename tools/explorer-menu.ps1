# Internal helpers. The public scripts request confirmation before calling these.
$script:MenuKeyName = 'RenameLedger'
$script:MenuOwner = 'RenameLedger Explorer integration v1'
$script:MenuLabel = 'Rename with RenameLedger'
$script:MenuParent = 'Software\Classes\Directory\shell'

function Get-RenameLedgerCommand([string]$Executable) {
    if (-not $Executable -or $Executable.Contains('"') -or $Executable.Contains('%')) {
        throw 'The executable path cannot be empty or contain a double quote or percent sign.'
    }
    return ('"{0}" "%1"' -f $Executable)
}

function Test-RenameLedgerMenu($Parent) {
    $key = $Parent.OpenSubKey($script:MenuKeyName, $false)
    if ($null -eq $key) { return $false }
    try {
        $names = @($key.GetValueNames() | Sort-Object)
        $children = @($key.GetSubKeyNames())
        if (($names -join '|') -cne '|Icon|RenameLedgerExecutable|RenameLedgerOwner' -or
            $children.Count -ne 1 -or $children[0] -cne 'command') { return $false }
        foreach ($name in $names) {
            if ($key.GetValueKind($name) -ne [Microsoft.Win32.RegistryValueKind]::String) { return $false }
        }
        $exe = $key.GetValue('RenameLedgerExecutable')
        if ($key.GetValue('RenameLedgerOwner') -cne $script:MenuOwner -or
            $key.GetValue('') -cne $script:MenuLabel -or
            $key.GetValue('Icon') -cne ('"{0}",0' -f $exe)) { return $false }
        try { $expected = Get-RenameLedgerCommand $exe } catch { return $false }
        $command = $key.OpenSubKey('command', $false)
        if ($null -eq $command) { return $false }
        try {
            $commandNames = @($command.GetValueNames())
            if ($commandNames.Count -ne 1 -or $commandNames[0] -cne '' -or
                @($command.GetSubKeyNames()).Count -ne 0 -or
                $command.GetValueKind('') -ne [Microsoft.Win32.RegistryValueKind]::String) { return $false }
            return $command.GetValue('') -ceq $expected
        } finally { $command.Dispose() }
    } finally { $key.Dispose() }
}

function Install-RenameLedgerMenu($Parent, [string]$Executable) {
    $commandText = Get-RenameLedgerCommand $Executable
    $existing = $Parent.OpenSubKey($script:MenuKeyName, $false)
    if ($null -ne $existing) {
        $existing.Dispose()
        throw 'The RenameLedger menu key already exists. Nothing was changed; inspect or uninstall it first.'
    }
    $key = $Parent.CreateSubKey($script:MenuKeyName)
    try {
        if (@($key.GetValueNames()).Count -ne 0 -or @($key.GetSubKeyNames()).Count -ne 0) {
            throw 'The menu key is no longer empty. Nothing was changed.'
        }
        $key.SetValue('RenameLedgerOwner', $script:MenuOwner, [Microsoft.Win32.RegistryValueKind]::String)
        $key.SetValue('RenameLedgerExecutable', $Executable, [Microsoft.Win32.RegistryValueKind]::String)
        $key.SetValue('', $script:MenuLabel, [Microsoft.Win32.RegistryValueKind]::String)
        $key.SetValue('Icon', ('"{0}",0' -f $Executable), [Microsoft.Win32.RegistryValueKind]::String)
        $command = $key.CreateSubKey('command')
        try { $command.SetValue('', $commandText, [Microsoft.Win32.RegistryValueKind]::String) }
        finally { $command.Dispose() }
    } catch {
        throw "Installation did not finish. Inspect the RenameLedger registry key before retrying; it may be incomplete. $($_.Exception.Message)"
    } finally { $key.Dispose() }
}

function Uninstall-RenameLedgerMenu($Parent) {
    $existing = $Parent.OpenSubKey($script:MenuKeyName, $false)
    if ($null -eq $existing) { return $false }
    $existing.Dispose()
    if (-not (Test-RenameLedgerMenu $Parent)) {
        throw 'The menu key is not an unchanged RenameLedger registration. Nothing was removed; inspect it manually.'
    }
    $Parent.DeleteSubKeyTree($script:MenuKeyName, $false)
    return $true
}
