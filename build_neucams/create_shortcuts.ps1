param(
    [string]$Prefix = $Env:PREFIX
)

$ErrorActionPreference = 'Stop'

# ── Resolve $Prefix if it was not provided ─────────────────────
if (-not $Prefix) {
    try {
        $Prefix = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath
    } catch {
        Write-Warning 'Unable to resolve PREFIX automatically.'
        throw
    }
}

# ── Locations (per user to avoid admin rights) ────────────────
$startMenuRoot = Join-Path $Env:AppData 'Microsoft\Windows\Start Menu\Programs'
$groupDir      = Join-Path $startMenuRoot 'neucams'
$desktopDir    = [Environment]::GetFolderPath('Desktop')

# ── Target + Icon relative to <prefix> ─────────────────────────
$targetExe  = Join-Path $Prefix 'NeuCams.exe'
$workingDir = Split-Path $targetExe -Parent
$iconSource = Join-Path $Prefix 'icon.ico'  # optional

# ── Ensure Start Menu folder exists ────────────────────────────
New-Item -ItemType Directory -Force -Path $groupDir | Out-Null

# ── Helper: creates a .lnk file ────────────────────────────────
function New-Shortcut {
    param(
        [Parameter(Mandatory)][string]$LinkPath,
        [Parameter(Mandatory)][string]$TargetPath,
        [string]$Arguments = '',
        [string]$WorkingDirectory = '',
        [string]$IconLocation = ''
    )
    $shell = New-Object -ComObject WScript.Shell
    $sc    = $shell.CreateShortcut($LinkPath)
    $sc.TargetPath       = $TargetPath
    if ($Arguments)        { $sc.Arguments        = $Arguments }
    if ($WorkingDirectory) { $sc.WorkingDirectory = $WorkingDirectory }
    if ($IconLocation)     { $sc.IconLocation     = "$IconLocation,0" }
    $sc.Save()
}

# ── Create Start Menu shortcut ────────────────────────────────
$newStartMenuLnk = Join-Path $groupDir 'NeuCams.lnk'
if (Test-Path $iconSource) {
    New-Shortcut -LinkPath $newStartMenuLnk `
                 -TargetPath $targetExe `
                 -WorkingDirectory $workingDir `
                 -IconLocation $iconSource
} else {
    New-Shortcut -LinkPath $newStartMenuLnk `
                 -TargetPath $targetExe `
                 -WorkingDirectory $workingDir
}

# ── (Optional) Desktop shortcut ───────────────────────────────
$newDesktopLnk = Join-Path $desktopDir 'NeuCams.lnk'
if (Test-Path $iconSource) {
    New-Shortcut -LinkPath $newDesktopLnk `
                 -TargetPath $targetExe `
                 -WorkingDirectory $workingDir `
                 -IconLocation $iconSource
} else {
    New-Shortcut -LinkPath $newDesktopLnk `
                 -TargetPath $targetExe `
                 -WorkingDirectory $workingDir
}
