[CmdletBinding()]
param(
    [switch]$ClearIncidentHistory
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DatabasePath = Join-Path $Root "artifacts/opspilot.db"

Push-Location $Root
try {
    .\scripts\scenario.ps1 reset-p1
    .\scripts\scenario.ps1 reset-p2

    if ($ClearIncidentHistory) {
        if (Test-Path -LiteralPath $DatabasePath) {
            Remove-Item -LiteralPath $DatabasePath
            Write-Host "Cleared retained local incident history: $DatabasePath"
        }
        else {
            Write-Host "No retained local incident history exists."
        }
    }

    Write-Host "Checkout is back at the controlled healthy baseline."
    if (-not $ClearIncidentHistory) {
        Write-Host "Retained local incident history was preserved. Use -ClearIncidentHistory only after stopping the API."
    }
}
finally {
    Pop-Location
}
