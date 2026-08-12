param(
    [string]$Image = "datansh-pm-os:sandbox"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dockerfile = Join-Path $repo "docker\Dockerfile.pmo-project-sandbox"

docker version | Out-Null
docker build --file $dockerfile --tag $Image $repo
if ($LASTEXITCODE -ne 0) {
    throw "PM-OS project sandbox image build failed"
}

Write-Host "Built $Image"
