$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$condaEnv = Join-Path $root ".conda\\envs\\paperforge"
$condaPkgs = Join-Path $root ".conda\\pkgs"
$pipCache = Join-Path $root ".pip-cache"

if (-not (Test-Path $condaPkgs)) {
    New-Item -ItemType Directory -Path $condaPkgs -Force | Out-Null
}

$env:CONDA_PKGS_DIRS = $condaPkgs

if (-not (Test-Path (Join-Path $condaEnv "python.exe"))) {
    conda create --yes --prefix $condaEnv python=3.12
}

conda run --prefix $condaEnv python -m pip install --disable-pip-version-check --cache-dir $pipCache -r (Join-Path $root "backend\\requirements.txt")

Push-Location (Join-Path $root "frontend")
npm.cmd install --cache .npm-cache
Pop-Location

Write-Host "Dependencies installed under $root (.conda/.pip-cache/frontend/.npm-cache)."
Write-Host "Use backend env with: conda activate `"$condaEnv`""
