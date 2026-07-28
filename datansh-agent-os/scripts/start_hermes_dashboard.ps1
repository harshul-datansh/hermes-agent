$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
$HostAddress = if ($env:HERMES_DASHBOARD_HOST) { $env:HERMES_DASHBOARD_HOST } else { "127.0.0.1" }
$Port = if ($env:HERMES_DASHBOARD_PORT) { $env:HERMES_DASHBOARD_PORT } else { "9119" }
$Hermes = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $Hermes) {
  throw "hermes command not found. This fork is cloned, but Hermes CLI is not installed on PATH. Follow Hermes developer setup or official installer, then rerun."
}
hermes dashboard --host $HostAddress --port $Port --no-open

