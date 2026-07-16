[CmdletBinding()]
param(
    [string]$OutputDirectory = "artifacts/judge-package"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Destination = Join-Path $Root $OutputDirectory
$ImagePath = Join-Path $Destination "opspilot-checkout-0.1.tar"

Push-Location $Root
try {
    docker build --tag opspilot-checkout:0.1 "$Root\demo\checkout"
    if ($LASTEXITCODE -ne 0) { throw "Checkout image build failed." }
    New-Item -ItemType Directory -Force $Destination | Out-Null
    docker save --output $ImagePath opspilot-checkout:0.1
    if ($LASTEXITCODE -ne 0) { throw "Checkout image export failed." }
    Copy-Item "$Root\JUDGE_PATH.md" "$Destination\README.md" -Force
    Write-Host "Created $ImagePath"
}
finally {
    Pop-Location
}
