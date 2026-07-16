[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ImageArchive
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $ImageArchive)) { throw "Judge image archive not found: $ImageArchive" }
Push-Location $Root
try {
    docker load --input $ImageArchive
    if ($LASTEXITCODE -ne 0) { throw "Judge image import failed." }
    .\scripts\scenario.ps1 create -UsePrebuiltImage
    .\scripts\test-e2e-p1-remediation.ps1
    .\scripts\test-e2e-p2-remediation.ps1
}
finally {
    Pop-Location
}
