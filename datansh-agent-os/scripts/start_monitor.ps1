$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$Python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }
$HostAddress = if ($env:DATANSH_MONITOR_HOST) { $env:DATANSH_MONITOR_HOST } else { "127.0.0.1" }
$Port = if ($env:DATANSH_MONITOR_PORT) { $env:DATANSH_MONITOR_PORT } else { "8501" }
& $Python -m streamlit run monitor\app.py --server.address $HostAddress --server.port $Port

