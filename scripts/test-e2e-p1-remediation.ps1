[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "opspilot-dev"
$Namespace = "opspilot-demo"
$RunId = "e2e-remediation-" + (Get-Date -Format "yyyyMMddHHmmss")
$ResultPath = Join-Path $Root "artifacts/e2e-p1-remediation-$RunId.json"
$DatabasePath = Join-Path $Root "artifacts/e2e-p1-remediation-$RunId.db"
$PrometheusLog = Join-Path $Root "artifacts/prometheus-port-forward-$RunId.log"
$PrometheusErrorLog = Join-Path $Root "artifacts/prometheus-port-forward-$RunId.err.log"

if (-not ((kind get clusters) -contains $Cluster)) {
    throw "Dedicated kind cluster '$Cluster' is required. Run .\scripts\scenario.ps1 create first."
}

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}

function Get-FailMode {
    $deployment = kubectl -n $Namespace get deployment checkout -o json | ConvertFrom-Json
    $container = $deployment.spec.template.spec.containers | Where-Object { $_.name -eq "checkout" }
    $variable = $container.env | Where-Object { $_.name -eq "FAIL_MODE" }
    return [string]$variable.value
}

Push-Location $Root
$PrometheusProcess = $null
try {
    if ((Get-FailMode) -ne "false") { .\scripts\scenario.ps1 reset-p1 }
    .\scripts\scenario.ps1 inject-p1
    $PrometheusProcess = Start-Process kubectl -ArgumentList @(
        "-n", $Namespace, "port-forward", "deployment/prometheus", "9090:9090"
    ) -RedirectStandardOutput $PrometheusLog -RedirectStandardError $PrometheusErrorLog -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 3

    $env:PYTHONPATH = "backend;tests"
    $result = uv run python -W ignore::DeprecationWarning tests/e2e_p1_remediation.py --run-id $RunId --db-path $DatabasePath --prometheus-url http://127.0.0.1:9090
    Assert-LastExitCode "P1 remediation setup"
    $result | Set-Content -Encoding utf8 $ResultPath
    kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
    Start-Sleep -Seconds 25

    $actionId = (Get-Content -Raw $ResultPath | ConvertFrom-Json).action_id
    $verification = @'
from fastapi.testclient import TestClient
from opspilot.api.main import create_app
from opspilot.settings import Settings
import os
settings = Settings(OPS_PILOT_DB_PATH=os.environ["OPS_PILOT_E2E_DB"], OPS_PILOT_PROMETHEUS_URL="http://127.0.0.1:9090")
with TestClient(create_app(settings)) as http:
    response = http.post(f"/api/v1/actions/{os.environ['OPS_PILOT_E2E_ACTION']}/verify")
    print(response.status_code, response.text)
    response.raise_for_status()
'@
    $env:OPS_PILOT_E2E_DB = $DatabasePath
    $env:OPS_PILOT_E2E_ACTION = $actionId
    $verified = $verification | uv run python -W ignore::DeprecationWarning -
    Assert-LastExitCode "P1 remediation recovery verification"
    $verified | Add-Content -Encoding utf8 $ResultPath
    Get-Content -Raw $ResultPath
}
finally {
    if ($PrometheusProcess -and -not $PrometheusProcess.HasExited) { Stop-Process -Id $PrometheusProcess.Id -Force }
    if ((Get-FailMode) -ne "false") { .\scripts\scenario.ps1 reset-p1 }
    Pop-Location
}
