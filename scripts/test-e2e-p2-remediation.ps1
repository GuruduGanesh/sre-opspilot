[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "opspilot-dev"
$Namespace = "opspilot-demo"
$RunId = "e2e-p2-remediation-" + (Get-Date -Format "yyyyMMddHHmmss")
$ResultPath = Join-Path $Root "artifacts/e2e-p2-remediation-$RunId.json"
$DatabasePath = Join-Path $Root "artifacts/e2e-p2-remediation-$RunId.db"

if (-not ((kind get clusters) -contains $Cluster)) {
    throw "Dedicated kind cluster '$Cluster' is required. Run .\scripts\scenario.ps1 create first."
}

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}

function Get-CheckoutPods {
    return (kubectl -n $Namespace get pods -l app.kubernetes.io/name=checkout -o json | ConvertFrom-Json).items
}

Push-Location $Root
try {
    .\scripts\scenario.ps1 reset-p2
    .\scripts\scenario.ps1 inject-p2
    kubectl -n $Namespace exec deployment/load-generator -- sh -c 'for i in $(seq 1 12); do wget -T 5 -q -O /dev/null http://checkout:8000/checkout 2>/dev/null || true; sleep 1; done'
    Assert-LastExitCode "P2 in-cluster load"
    $oom = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        foreach ($pod in @(Get-CheckoutPods)) {
            foreach ($container in @($pod.status.containerStatuses)) {
                if ($container.lastState.terminated.reason -eq "OOMKilled") { $oom = $true; break }
            }
            if ($oom) { break }
        }
        if ($oom) { break }
        Start-Sleep -Seconds 3
    }
    if (-not $oom) { throw "P2 did not expose OOMKilled before remediation." }

    $env:PYTHONPATH = "backend"
    $result = uv run python -W ignore::DeprecationWarning tests/e2e_p2_remediation.py --run-id $RunId --db-path $DatabasePath
    Assert-LastExitCode "P2 remediation setup"
    $result | Set-Content -Encoding utf8 $ResultPath
    kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
    Assert-LastExitCode "P2 controlled recovery rollout"

    $actionId = (Get-Content -Raw $ResultPath | ConvertFrom-Json).action_id
    $verification = @'
from fastapi.testclient import TestClient
from opspilot.api.main import create_app
from opspilot.settings import Settings
import os
settings = Settings(OPS_PILOT_DB_PATH=os.environ["OPS_PILOT_E2E_DB"])
with TestClient(create_app(settings)) as http:
    response = http.post(f"/api/v1/actions/{os.environ['OPS_PILOT_E2E_ACTION']}/verify")
    print(response.status_code, response.text)
    response.raise_for_status()
'@
    $env:OPS_PILOT_E2E_DB = $DatabasePath
    $env:OPS_PILOT_E2E_ACTION = $actionId
    $verified = $verification | uv run python -W ignore::DeprecationWarning -
    Assert-LastExitCode "P2 recovery verification"
    $verified | Add-Content -Encoding utf8 $ResultPath
    Get-Content -Raw $ResultPath
}
finally {
    .\scripts\scenario.ps1 reset-p2
    Pop-Location
}
