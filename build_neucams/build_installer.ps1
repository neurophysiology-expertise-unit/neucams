# build_neucams\build_installer.ps1
param(
    [switch]$Clean = $false,
    [switch]$Help = $false
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Show help if requested
if ($Help) {
    Write-Host @"
NeuCams Build Script

Usage:
  .\build_installer.ps1              # Normal build (cleans some artifacts)
  .\build_installer.ps1 -Clean       # Full clean build (removes all artifacts)
  .\build_installer.ps1 -Help        # Show this help

Options:
  -Clean    Perform a complete clean build, removing all build artifacts,
            dist folders, cache files, and previous installers
  -Help     Show this help message

"@
    exit 0
}

# keep PyInstaller outputs under build_neucams
$Dist  = Join-Path $PSScriptRoot 'dist'
$Build = Join-Path $PSScriptRoot 'build'
$Spec  = Join-Path $PSScriptRoot 'NeuCams.spec'

# ---- Config ----
$PYINSTALLER_ENV = 'neucams_env'   # your app env (py39)
$CONSTRUCTOR_ENV = 'ctorenv'       # tool env for constructor

# ---- Paths ----
$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot     = Split-Path -Parent $ScriptDir
$BuildDir     = Join-Path $RepoRoot 'build_neucams'
$SpecPath     = Join-Path $BuildDir 'NeuCams.spec'
$ConstructYml = Join-Path $BuildDir 'construct.yaml'

$DistDir      = Join-Path $BuildDir 'dist\NeuCams'
$ExePath      = Join-Path $DistDir 'NeuCams.exe'
$InternalDir  = Join-Path $DistDir '_internal'
$JsonFilesDir = Join-Path $RepoRoot 'neucams\jsonfiles'
$PayloadZip   = Join-Path $BuildDir 'payload.zip'

# ---- Sanity ----
if (-not (Test-Path $SpecPath))     { throw "Missing $SpecPath" }
if (-not (Test-Path $ConstructYml)) { throw "Missing $ConstructYml" }

# ---- ctorenv (constructor) ----
$haveCtorEnv = (& conda env list) -match '^\s*ctorenv\s'
if (-not $haveCtorEnv) {
  conda create -n $CONSTRUCTOR_ENV -c conda-forge -y python=3.11 constructor | Out-Host
} else {
  conda install -n $CONSTRUCTOR_ENV -c conda-forge -y constructor | Out-Host
}

# ---- neucams_env must already exist (from environment.yml) ----
$haveNeuEnv = (& conda env list) -match '^\s*' + [regex]::Escape($PYINSTALLER_ENV) + '\s'
if (-not $haveNeuEnv) {
  throw "Conda env '$PYINSTALLER_ENV' not found. Create it with:  conda env create -f environment.yml"
}

# Ensure PyInstaller via pip (avoid conda solver)
conda run -n $PYINSTALLER_ENV python -m pip install --upgrade "pyinstaller==6.15.0" pyDCAM | Out-Host

# ---- Enhanced Cleaning ----
function Clean-BuildArtifacts {
    param([bool]$FullClean = $false)
    
    Write-Host ">> Cleaning build artifacts..."
    
    # Standard cleanup (always done)
    $standardCleanPaths = @(
        (Join-Path $BuildDir 'build'),
        $DistDir,
        $PayloadZip
    )
    
    foreach ($path in $standardCleanPaths) {
        if (Test-Path $path) {
            Write-Host "   Removing: $path"
            Remove-Item -Recurse -Force $path -ErrorAction SilentlyContinue
        }
    }
    
    if ($FullClean) {
        Write-Host "   Performing FULL clean..."
        
        # Additional paths for full clean
        $fullCleanPaths = @(
            (Join-Path $BuildDir '*.exe'),           # Previous installers
            (Join-Path $BuildDir '__pycache__'),     # Python cache
            (Join-Path $RepoRoot '**\__pycache__'),  # All Python cache
            (Join-Path $RepoRoot '**\*.pyc'),        # Compiled Python files
            (Join-Path $BuildDir 'NeuCams')         # Any leftover NeuCams folder
        )
        
        foreach ($pattern in $fullCleanPaths) {
            $items = Get-ChildItem -Path $pattern -Recurse -Force -ErrorAction SilentlyContinue
            foreach ($item in $items) {
                Write-Host "   Removing: $($item.FullName)"
                Remove-Item -Recurse -Force $item.FullName -ErrorAction SilentlyContinue
            }
        }
        
        # Clear conda build cache if it exists
        try {
            conda clean --all -y | Out-Host
            Write-Host "   Conda cache cleared"
        } catch {
            Write-Host "   Conda cache clear skipped (not critical)"
        }
        
        # Force garbage collection to release file handles
        [System.GC]::Collect()
        [System.GC]::WaitForPendingFinalizers()
        Start-Sleep -Seconds 2
        
        Write-Host "   Full clean completed"
    }
    
    Write-Host "   Cleanup completed"
}

# Perform cleaning based on parameters
Clean-BuildArtifacts -FullClean $Clean

# ---- Build with PyInstaller ----
Write-Host ">> Building NeuCams.exe via PyInstaller..."
conda run -n $PYINSTALLER_ENV python -m PyInstaller $SpecPath --clean -y | Out-Host

if (-not (Test-Path $ExePath))     { throw "PyInstaller did not produce $ExePath" }
if (-not (Test-Path $InternalDir)) { throw "Missing _internal at $InternalDir" }
Write-Host "   OK: PyInstaller output ready."

# ---- Create payload.zip (exe + _internal + jsonfiles) ----
Write-Host ">> Creating payload.zip..."
if (Test-Path $PayloadZip) { Remove-Item -Force $PayloadZip }

$toZip = @($ExePath, $InternalDir)
if (Test-Path $JsonFilesDir) {
  $toZip += $JsonFilesDir
  Write-Host "   Including jsonfiles from $JsonFilesDir"
} else {
  Write-Warning "No jsonfiles at $JsonFilesDir (skipping)."
}

# Enhanced zip creation with retry logic for file locking issues
$maxRetries = 3
$retryCount = 0
$zipSuccess = $false

while (-not $zipSuccess -and $retryCount -lt $maxRetries) {
    try {
        Compress-Archive -Path $toZip -DestinationPath $PayloadZip -Force
        $zipSuccess = $true
        Write-Host "   Payload zip created successfully"
    }
    catch {
        $retryCount++
        Write-Warning "   Zip attempt $retryCount failed: $($_.Exception.Message)"
        
        if ($retryCount -lt $maxRetries) {
            Write-Host "   Waiting 3 seconds before retry..."
            
            # Force garbage collection to release file handles
            [System.GC]::Collect()
            [System.GC]::WaitForPendingFinalizers()
            Start-Sleep -Seconds 3
            
            Write-Host "   Retrying zip creation..."
        } else {
            Write-Error "Failed to create payload.zip after $maxRetries attempts. File may be locked by another process."
            throw $_
        }
    }
}
if (-not (Test-Path $PayloadZip)) { throw "Failed to create $PayloadZip" }
Write-Host "   OK: payload.zip created."

# ---- Run Constructor ----
Write-Host ">> Running Constructor..."
Push-Location $BuildDir
try {
  conda run -n $CONSTRUCTOR_ENV python -m constructor . | Out-Host
} finally {
  Pop-Location
}

$installer = Get-ChildItem -Path $BuildDir -Filter 'NeuCams-*-windows-x86_64.exe' |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw "Constructor did not produce an installer." }

Write-Host ""
Write-Host "========== DONE =========="
Write-Host ("Installer: {0}" -f $installer.FullName)
Write-Host ("Payload:   {0}" -f $PayloadZip)
Write-Host ("Dist:      {0}" -f $DistDir)
Write-Host "=========================="