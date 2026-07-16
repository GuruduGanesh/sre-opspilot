[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "opspilot-dev"
$Namespace = "opspilot-demo"
$RunId = "e2e-p2-" + (Get-Date -Format "yyyyMMddHHmmss")
$ResultPath = Join-Path $Root "artifacts/e2e-p2-$RunId.json"

if (-not ((kind get clusters) -contains $Cluster)) {
    throw "Dedicated kind cluster '$Cluster' is required. Run .\scripts\scenario.ps1 create first."
}

function Get-CheckoutPods {
    return (kubectl -n $Namespace get pods -l app.kubernetes.io/name=checkout -o json | ConvertFrom-Json).items
}

Push-Location $Root
try {
    .\scripts\scenario.ps1 reset-p2
    .\scripts\scenario.ps1 inject-p2
    # Force bounded in-cluster traffic so the memory trigger does not depend on scheduler timing.
    kubectl -n $Namespace exec deployment/load-generator -- sh -c 'for i in $(seq 1 12); do wget -T 5 -q -O /dev/null http://checkout:8000/checkout 2>/dev/null || true; sleep 1; done'

    $match = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        foreach ($pod in @(Get-CheckoutPods)) {
            foreach ($container in @($pod.status.containerStatuses)) {
                $reason = $container.lastState.terminated.reason
                if ($reason -eq "OOMKilled") {
                    $match = [PSCustomObject]@{
                        pod = $pod.metadata.name
                        restart_count = $container.restartCount
                        termination_reason = $reason
                        exit_code = $container.lastState.terminated.exitCode
                    }
                    break
                }
            }
            if ($match) { break }
        }
        if ($match) { break }
        Start-Sleep -Seconds 3
    }
    if (-not $match) { throw "P2 did not expose an OOMKilled container state within 120 seconds." }

    $match | ConvertTo-Json -Compress | Set-Content -Encoding utf8 $ResultPath
    Get-Content -Raw $ResultPath
}
finally {
    .\scripts\scenario.ps1 reset-p2
    Pop-Location
}
