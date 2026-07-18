<# ============================================================================
 build_installer.ps1 — drop-in
 - Uses conda env `neucams_env` by default
 - Upgrades pip/setuptools/wheel/packaging (fixes pyzmq metadata warning)
 - Installs PyInstaller + pyDCAM in the build env
 - Writes a build stamp for verification (read by NeuCams.spec)
 - Runs PyInstaller on NeuCams.spec
 - Rebuilds payload.zip from dist/NeuCams/*
 - If construct.yaml exists, runs `constructor` to make the installer
============================================================================ #>

[CmdletBinding()]
param(
  [string]$PyInstallerEnv = "neucams_env",
  [switch]$OneFile,
  [switch]$DebugPyInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- Resolve paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir ".")
$SpecCandidates = @(
  (Join-Path $RepoRoot "build_neucams\NeuCams.spec"),
  (Join-Path $RepoRoot "NeuCams.spec")
)
$SpecPath = $SpecCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $SpecPath) {
  throw "NeuCams.spec not found. Looked in: $($SpecCandidates -join ', ')"
}
$SpecDir     = Split-Path -Parent $SpecPath
$DistDir     = Join-Path $RepoRoot "dist\NeuCams"
$BuildDir    = Join-Path $RepoRoot "build"
$PayloadZip  = Join-Path $RepoRoot "payload.zip"
$ConstructYml= Join-Path $RepoRoot "construct.yaml"

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Host "RepoRoot:         $RepoRoot"
Write-Host "SpecPath:         $SpecPath"
Write-Host "PyInstaller Env:  $PyInstallerEnv"

# --- Find conda
$conda = $Env:CONDA_EXE
if (-not $conda) { $conda = "conda" }

# --- Make packaging tools current (fixes 'Invalid version: cpython' warning)
& $conda run -n $PyInstallerEnv python -m pip install --upgrade pip setuptools wheel packaging | Out-Host

# (Optional but robust) pre-install pyzmq via conda to avoid pip metadata parsing
try {
  & $conda install -n $PyInstallerEnv -c conda-forge -y pyzmq | Out-Host
} catch {
  Write-Host "Skipping conda pyzmq: $($_.Exception.Message)"
}

# --- Install build deps into the SAME env PyInstaller will use
& $conda run -n $PyInstallerEnv python -m pip install --upgrade "pyinstaller==6.15.0" pyDCAM | Out-Host

# --- Sanity check the build env (PowerShell-safe)
$SanityPy = Join-Path $env:TEMP "neucams_sanity_$([Guid]::NewGuid().ToString('N')).py"
@'
import sys
print("python ->", sys.executable)
try:
    import pyDCAM
    print("pyDCAM ->", getattr(pyDCAM, "__file__", "(namespace package)"))
except Exception as e:
    print("pyDCAM import failed:", e)
'@ | Set-Content -Path $SanityPy -Encoding UTF8

& $conda run -n $PyInstallerEnv python $SanityPy | Out-Host
Remove-Item $SanityPy -Force

# --- Control onefile/onedir via env var that NeuCams.spec reads
if ($OneFile) { $env:NEUCAMS_ONEFILE = "1" } else { Remove-Item Env:\NEUCAMS_ONEFILE -ErrorAction SilentlyContinue }

# --- Write build stamp (NeuCams.spec includes this file via datas)
$StampPath = Join-Path $SpecDir "build_info.txt"
$branch = ""; $commit = ""
try { $branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD).Trim() } catch {}
try { $commit = (git -C $RepoRoot rev-parse HEAD).Trim() } catch {}
$builderPy = (& $conda run -n $PyInstallerEnv python -c "import sys; print(sys.executable)") -join ""
@(
  "built=$(Get-Date -Format o)"
  "root=$RepoRoot"
  "spec=$SpecPath"
  "branch=$branch"
  "commit=$commit"
  "builder_env=$builderPy"
) -join "`n" | Set-Content -NoNewline $StampPath
Write-Host "Wrote build stamp: $StampPath"

# --- Clean pip cache (optional) to avoid stale wheels
& $conda run -n $PyInstallerEnv python -m pip cache purge | Out-Host

# --- Run PyInstaller
$pyiArgs = @($SpecPath, "--clean", "-y")
if ($DebugPyInstaller) { $pyiArgs += @("--log-level=DEBUG") }

& $conda run -n $PyInstallerEnv python -m PyInstaller @pyiArgs | Out-Host

if (-not (Test-Path $DistDir)) {
  throw "Expected PyInstaller output at $DistDir, but it wasn't created."
}

# --- Rebuild payload.zip from the fresh dist output
if (Test-Path $PayloadZip) { Remove-Item -Force $PayloadZip }
Compress-Archive -Path (Join-Path $DistDir '*') -DestinationPath $PayloadZip -Force
Write-Host "Rebuilt payload: $PayloadZip"

# --- If construct.yaml exists, build installer with constructor
if (Test-Path $ConstructYml) {
  Write-Host "Found construct.yaml — building installer with constructor..."
  # Ensure constructor is present
  & $conda run -n $PyInstallerEnv python -m pip install --upgrade constructor | Out-Host
  # Build installer into /build
  & $conda run -n $PyInstallerEnv constructor $RepoRoot --output-dir $BuildDir | Out-Host
  Write-Host "Installer build finished. Check: $BuildDir"
} else {
  Write-Host "No construct.yaml found — skipped constructor step. You can run it later if needed."
}

Write-Host "`nDone."
Write-Host "Dist folder : $DistDir"
Write-Host "Payload     : $PayloadZip"
Write-Host "Build output: $BuildDir"
