[CmdletBinding()]
param(
    [string]$Cluster = "opspilot-dev",
    [string]$Namespace = "opspilot-demo",
    [int]$ApiPort = 8000,
    [int]$ConsolePort = 5173,
    [switch]$RequireLiveModel
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)
    Write-Host "`n=== $Title ===" -ForegroundColor Cyan
}

function Test-HttpEndpoint {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $response.StatusCode
    }
    catch {
        return $null
    }
}

Write-Host "OpsPilot local demo proof" -ForegroundColor Green
Write-Host "Read-only check: this script does not reset a scenario, create an incident, or call a model."

Write-Section "kind cluster"
$clusters = @(kind get clusters)
if ($clusters -notcontains $Cluster) {
    throw "Required local kind cluster '$Cluster' was not found. Run .\scripts\scenario.ps1 create first."
}
Write-Host "kind cluster: $Cluster"
Write-Host "kubectl context: $(kubectl config current-context)"

Write-Section "Kubernetes workloads"
kubectl -n $Namespace get pods -o wide
kubectl -n $Namespace get deployments

Write-Section "Prometheus"
$prometheusPod = kubectl -n $Namespace get pods -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}'
if (-not $prometheusPod) {
    throw "Prometheus pod was not found in namespace '$Namespace'."
}
Write-Host "Prometheus pod: $prometheusPod"
$prometheusStatus = Test-HttpEndpoint "http://127.0.0.1:9090/-/ready"
if ($prometheusStatus -eq 200) {
    Write-Host "Prometheus local endpoint: ready (http://127.0.0.1:9090)" -ForegroundColor Green
}
else {
    Write-Host "Prometheus local endpoint: not exposed on port 9090 yet. Start .\scripts\run-console.ps1 before recording." -ForegroundColor Yellow
}

Write-Section "OpsPilot API"
try {
    $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 3
}
catch {
    throw "OpsPilot API is not reachable at http://127.0.0.1:$ApiPort. Start .\scripts\run-console.ps1 first."
}
$health | ConvertTo-Json -Compress | Write-Host
if ($RequireLiveModel -and $health.investigation_mode -ne "live_model") {
    throw "Expected investigation_mode 'live_model' for the final recording, got '$($health.investigation_mode)'."
}
if ($health.investigation_mode -eq "live_model") {
    Write-Host "Live-model mode confirmed." -ForegroundColor Green
}
else {
    Write-Host "Controlled-simulation mode confirmed. Use it only for rehearsal." -ForegroundColor Yellow
}

Write-Section "Console"
$consoleStatus = Test-HttpEndpoint "http://127.0.0.1:$ConsolePort/"
if ($consoleStatus -eq 200) {
    Write-Host "Console ready: http://127.0.0.1:$ConsolePort" -ForegroundColor Green
}
else {
    Write-Host "Console is not reachable at http://127.0.0.1:$ConsolePort. In another terminal run: cd frontend; npm run dev" -ForegroundColor Yellow
}

Write-Host "`nProof complete. Switch to the browser and open http://127.0.0.1:$ConsolePort" -ForegroundColor Green
