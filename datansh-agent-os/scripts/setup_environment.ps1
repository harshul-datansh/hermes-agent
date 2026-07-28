$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Write-Host "Datansh Agent OS setup"
Write-Host "Root: $Root"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example. Add OPENROUTER_API_KEY there for live calls."
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  throw "Python 3.11-3.13 is required."
}
python --version

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r monitor\requirements.txt

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
  uv --version
} else {
  Write-Host "uv is not installed. Hermes' official installer can install it, or install uv before editable Hermes development."
}

Write-Host "Setup complete."
Write-Host "Next: scripts\discover_openrouter_free_models.ps1, scripts\audit_free_model_config.ps1, scripts\run_demo.ps1"

