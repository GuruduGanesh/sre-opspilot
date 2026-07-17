[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("create", "deploy", "inject-p1", "reset-p1", "inject-p2", "reset-p2", "status", "destroy")]
    [string]$Command,

    [switch]$UsePrebuiltImage
)

$ErrorActionPreference = "Stop"
$Cluster = "opspilot-dev"
$Namespace = "opspilot-demo"
$Root = Split-Path -Parent $PSScriptRoot

function Test-Cluster {
    return (kind get clusters) -contains $Cluster
}

function Assert-Cluster {
    if (-not (Test-Cluster)) {
        throw "Cluster '$Cluster' does not exist. Run: .\scripts\scenario.ps1 create"
    }
}

function Prepare-CheckoutImage {
    if ($UsePrebuiltImage) {
        docker image inspect opspilot-checkout:0.1 *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Prebuilt opspilot-checkout:0.1 image is missing. Load the judge image archive first."
        }
    }
    else {
        docker build --tag opspilot-checkout:0.1 "$Root\demo\checkout"
        if ($LASTEXITCODE -ne 0) { throw "Checkout image build failed." }
    }
}

switch ($Command) {
    "create" {
        if (-not (Test-Cluster)) {
            kind create cluster --name $Cluster --config "$Root\infra\kind\config.yaml"
        }
        Prepare-CheckoutImage
        kind load docker-image opspilot-checkout:0.1 --name $Cluster
        kubectl apply -f "$Root\infra\k8s\namespace.yaml"
        kubectl apply -f "$Root\infra\k8s\checkout.yaml"
        kubectl apply -f "$Root\infra\k8s\prometheus.yaml"
        kubectl apply -f "$Root\infra\k8s\load-generator.yaml"
        kubectl -n $Namespace rollout restart deployment/checkout
        kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
        kubectl -n $Namespace rollout status deployment/prometheus --timeout=120s
        kubectl -n $Namespace rollout status deployment/load-generator --timeout=120s
    }
    "deploy" {
        Assert-Cluster
        Prepare-CheckoutImage
        kind load docker-image opspilot-checkout:0.1 --name $Cluster
        kubectl apply -f "$Root\infra\k8s\namespace.yaml"
        kubectl apply -f "$Root\infra\k8s\checkout.yaml"
        kubectl apply -f "$Root\infra\k8s\prometheus.yaml"
        kubectl apply -f "$Root\infra\k8s\load-generator.yaml"
        kubectl -n $Namespace rollout restart deployment/checkout
        kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
    }
    "inject-p1" {
        Assert-Cluster
        kubectl -n $Namespace set env deployment/checkout FAIL_MODE=true
        kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
        Write-Host "P1 injected: checkout returns controlled HTTP 500 responses under load."
    }
    "reset-p1" {
        Assert-Cluster
        kubectl -n $Namespace set env deployment/checkout FAIL_MODE=false
        kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
        Write-Host "P1 reset: checkout returns HTTP 200 responses under load."
    }
    "inject-p2" {
        Assert-Cluster
        kubectl -n $Namespace set env deployment/checkout MEMORY_LEAK_MODE=true FAIL_MODE=false
        kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
        Write-Host "P2 injected: checkout retains controlled memory under load until Kubernetes restarts it."
    }
    "reset-p2" {
        Assert-Cluster
        kubectl -n $Namespace set env deployment/checkout MEMORY_LEAK_MODE=false
        kubectl -n $Namespace rollout status deployment/checkout --timeout=120s
        Write-Host "P2 reset: checkout no longer retains controlled memory."
    }
    "status" {
        Assert-Cluster
        kubectl -n $Namespace get deployments,pods,services
        kubectl -n $Namespace rollout history deployment/checkout
    }
    "destroy" {
        if (Test-Cluster) {
            kind delete cluster --name $Cluster
        }
    }
}
