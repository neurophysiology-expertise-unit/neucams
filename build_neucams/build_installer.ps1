# build_neucams\build_installer.ps1
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

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
conda run -n $PYINSTALLER_ENV python -m pip install --upgrade "pyinstaller==6.15.0" | Out-Host

# ---- Clean old outputs ----
if (Test-Path (Join-Path $BuildDir 'build')) { Remove-Item -Recurse -Force (Join-Path $BuildDir 'build') }
if (Test-Path $DistDir)                       { Remove-Item -Recurse -Force $DistDir }

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

Compress-Archive -Path $toZip -DestinationPath $PayloadZip -Force
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