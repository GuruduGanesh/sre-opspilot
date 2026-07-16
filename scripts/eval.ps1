[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutputPath = Join-Path $Root "artifacts/eval-$Timestamp.json"
$PreviousPythonWarnings = $env:PYTHONWARNINGS

function Get-ScenarioResult {
    param([string]$ScriptPath)
    $output = & $ScriptPath 2>&1
    if ($LASTEXITCODE -ne 0) { throw "$ScriptPath failed with exit code $LASTEXITCODE" }
    $text = ($output | Out-String)
    $jsonLine = @(
        $text -split "`r?`n" |
        Where-Object { $_.Trim() -match '^\{.*\}$' } |
        Select-Object -Last 1
    )
    if (-not $jsonLine) { throw "$ScriptPath did not emit a JSON result." }
    return ($jsonLine | ConvertFrom-Json)
}

Push-Location $Root
try {
    # FastAPI's current test-client dependency emits a known deprecation warning
    # on stderr. The harness treats stderr as evidence only when the process exits
    # nonzero, so suppress warnings without suppressing actual command failures.
    $env:PYTHONWARNINGS = "ignore"
    .\scripts\verify.ps1
    $p1 = Get-ScenarioResult ".\scripts\test-e2e-p1.ps1"
    $p2 = Get-ScenarioResult ".\scripts\test-e2e-p2.ps1"
    $p1Remediation = Get-ScenarioResult ".\scripts\test-e2e-p1-remediation.ps1"
    $p2Remediation = Get-ScenarioResult ".\scripts\test-e2e-p2-remediation.ps1"
    $result = [PSCustomObject]@{
        suite_version = "controlled-eval-v2"
        recorded_at = (Get-Date).ToUniversalTime().ToString("o")
        unit_and_build_checks = "passed"
        p1 = $p1
        p2 = $p2
        p1_remediation = $p1Remediation
        p2_remediation = $p2Remediation
    }
    $result | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $OutputPath
    Get-Content -Raw $OutputPath
}
finally {
    $env:PYTHONWARNINGS = $PreviousPythonWarnings
    Pop-Location
}
