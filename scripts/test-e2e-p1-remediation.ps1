[CmdletBinding()]
param(
    [switch]$SimulationInvestigation
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "opspilot-dev"
$Namespace = "opspilot-demo"
$PrometheusPort = 9091
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
    kubectl -n $Namespace patch deployment checkout --type merge --patch '{"spec":{"strategy":{"type":"RollingUpdate","rollingUpdate":{"maxSurge":0,"maxUnavailable":1}}}}'
    Assert-LastExitCode "P1 controlled rollout strategy"
    # A P1 recovery test requires the checkout baseline, not a leftover P2
    # memory-pressure condition from an earlier rehearsal.
    .\scripts\scenario.ps1 reset-p2
    if ((Get-FailMode) -ne "false") { .\scripts\scenario.ps1 reset-p1 }
    .\scripts\scenario.ps1 inject-p1
    $PrometheusProcess = Start-Process kubectl -ArgumentList @(
        "-n", $Namespace, "port-forward", "deployment/prometheus", "$PrometheusPort`:9090"
    ) -RedirectStandardOutput $PrometheusLog -RedirectStandardError $PrometheusErrorLog -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 3

    $env:PYTHONPATH = "backend;tests"
    $arguments = @("-W", "ignore::DeprecationWarning", "tests/e2e_p1_remediation.py", "--run-id", $RunId, "--db-path", $DatabasePath, "--prometheus-url", "http://127.0.0.1:$PrometheusPort")
    if ($SimulationInvestigation) { $arguments += "--simulate-investigation" }
    $result = uv run python @arguments
    Assert-LastExitCode "P1 remediation setup"
    $result | Set-Content -Encoding utf8 $ResultPath
    kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
    Start-Sleep -Seconds 15

    $actionId = (Get-Content -Raw $ResultPath | ConvertFrom-Json).action_id
    $verification = @"
from opspilot.adapters.kubernetes import KubernetesAdapter
from opspilot.adapters.prometheus import PrometheusAdapter
from opspilot.recovery import RecoveryVerifier
from opspilot.remediation import KubernetesRemediationAdapter, RemediationCoordinator
from opspilot.settings import Settings
from opspilot.storage.incidents import SQLiteIncidentStore
import os
import time
settings = Settings(OPS_PILOT_DB_PATH=os.environ["OPS_PILOT_E2E_DB"], OPS_PILOT_PROMETHEUS_URL="http://127.0.0.1:$PrometheusPort")
store = SQLiteIncidentStore(settings.db_path)
coordinator = RemediationCoordinator(
    store,
    KubernetesRemediationAdapter(allowed_namespace=settings.demo_namespace, allowed_workloads={"checkout"}),
    namespace=settings.demo_namespace,
    workload="checkout",
    recovery_max_5xx_rate=settings.recovery_max_5xx_rate,
    recovery_min_2xx_rate=settings.recovery_min_2xx_rate,
)
indicators = PrometheusAdapter(settings.prometheus_url)
verifier = RecoveryVerifier(
    KubernetesAdapter(allowed_namespace=settings.demo_namespace),
    indicators,
    max_5xx_rate=settings.recovery_max_5xx_rate,
    min_2xx_rate=settings.recovery_min_2xx_rate,
)
try:
    for attempt in range(12):
        plan, recovery = coordinator.verify(os.environ["OPS_PILOT_E2E_ACTION"], verifier)
        print(plan.status.value, recovery.model_dump_json())
        if recovery.recovered:
            break
        if not recovery.pending:
            raise RuntimeError("P1 recovery verifier rejected the controlled baseline")
        if attempt == 11:
            raise RuntimeError("P1 recovery verifier did not observe restored checkout traffic")
        time.sleep(5)
finally:
    indicators.close()
"@
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
    .\scripts\scenario.ps1 reset-p2
    Pop-Location
}
