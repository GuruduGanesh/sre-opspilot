[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "opspilot-dev"
$Namespace = "opspilot-demo"
$RunId = "e2e-" + (Get-Date -Format "yyyyMMddHHmmss")
$ResultPath = Join-Path $Root "artifacts/e2e-p1-$RunId.json"
$DatabasePath = Join-Path $Root "artifacts/e2e-p1-$RunId.db"

if (-not ((kind get clusters) -contains $Cluster)) {
    throw "Dedicated kind cluster '$Cluster' is required. Run .\scripts\scenario.ps1 create first."
}

function Get-FailMode {
    $deployment = kubectl -n $Namespace get deployment checkout -o json | ConvertFrom-Json
    $container = $deployment.spec.template.spec.containers | Where-Object { $_.name -eq "checkout" }
    $variable = $container.env | Where-Object { $_.name -eq "FAIL_MODE" }
    return [string]$variable.value
}

Push-Location $Root
try {
    $env:PYTHONPATH = "backend"
    # A clean baseline makes this test rerunnable. The finally block always restores it.
    if ((Get-FailMode) -ne "false") { .\scripts\scenario.ps1 reset-p1 }
    .\scripts\scenario.ps1 inject-p1
    Start-Sleep -Seconds 3

    $result = uv run python tests/e2e_p1_kind.py --run-id $RunId --db-path $DatabasePath
    $result | Set-Content -Encoding utf8 $ResultPath
    Get-Content -Raw $ResultPath
}
finally {
    if ((Get-FailMode) -ne "false") { .\scripts\scenario.ps1 reset-p1 }
    Pop-Location
}
