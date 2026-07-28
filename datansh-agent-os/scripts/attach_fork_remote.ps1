$ErrorActionPreference = "Stop"
$DatanshRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$EnvPath = Join-Path $DatanshRoot ".env"
if (-not (Test-Path $EnvPath)) {
  throw "Missing $EnvPath. Copy .env.example to .env and set DATANSH_HERMES_FORK_URL."
}

$ForkUrl = ""
Get-Content $EnvPath | ForEach-Object {
  if ($_ -match "^\s*DATANSH_HERMES_FORK_URL\s*=\s*(.+)\s*$") {
    $ForkUrl = $Matches[1].Trim().Trim('"').Trim("'")
  }
}

if (-not $ForkUrl) {
  throw "DATANSH_HERMES_FORK_URL is empty in $EnvPath."
}

Set-Location $RepoRoot
$remoteNames = @(git remote)
if ($remoteNames -contains "origin") {
  git remote set-url origin $ForkUrl
} else {
  git remote add origin $ForkUrl
}

if (-not ($remoteNames -contains "upstream")) {
  git remote add upstream https://github.com/NousResearch/hermes-agent.git
}

git remote -v
