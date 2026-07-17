[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$PrometheusPort = 9090,
    [switch]$ControlledSimulation
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PrometheusUrl = "http://127.0.0.1:$PrometheusPort"
$portForward = $null

function Test-PrometheusReady {
    param([string]$Url)

    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri "$Url/-/ready" -TimeoutSec 2).StatusCode -eq 200
    }
    catch {
        return $false
    }
}

Push-Location $Root
try {
    if (-not (Test-PrometheusReady $PrometheusUrl)) {
        $portForward = Start-Process -FilePath "kubectl" -ArgumentList @(
            "-n", "opspilot-demo", "port-forward", "deployment/prometheus", "$PrometheusPort`:9090"
        ) -PassThru -WindowStyle Hidden

        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline -and -not (Test-PrometheusReady $PrometheusUrl)) {
            Start-Sleep -Milliseconds 500
        }
        if (-not (Test-PrometheusReady $PrometheusUrl)) {
            throw "Prometheus was not ready at $PrometheusUrl. Run .\scripts\scenario.ps1 create first."
        }
    }

    $env:OPS_PILOT_PROMETHEUS_URL = $PrometheusUrl
    if ($ControlledSimulation) {
        $env:OPS_PILOT_SIMULATION_INVESTIGATION_ENABLED = "true"
        Write-Host "Controlled simulation reports are enabled. They are not GPT-5.6 outputs."
    }
    Write-Host "Starting OpsPilot API with controlled Prometheus telemetry at $PrometheusUrl"
    uv run uvicorn opspilot.api.main:app --app-dir backend --host 127.0.0.1 --port $ApiPort
}
finally {
    if ($portForward -and -not $portForward.HasExited) {
        Stop-Process -Id $portForward.Id -Force
    }
    Pop-Location
}
